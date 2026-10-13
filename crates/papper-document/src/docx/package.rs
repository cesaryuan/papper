//! Preserve every OPC ZIP member while editing only selected Word XML parts.

use super::xml::{Element, parse};
use anyhow::{Context, Result};
use std::collections::{BTreeMap, BTreeSet};
use std::io::{Read, Write};
use std::path::Path;
use zip::write::SimpleFileOptions;

/// Keep binary and unknown package parts intact during document edits.
pub(crate) struct Package {
    pub entries: BTreeMap<String, Vec<u8>>,
}

impl Package {
    /// Read all ZIP members without interpreting or dropping embedded resources.
    pub fn open(path: &Path) -> Result<Self> {
        let file = std::fs::File::open(path)
            .with_context(|| format!("DOCX not found: {}", path.display()))?;
        let mut archive = zip::ZipArchive::new(file).context("Invalid DOCX ZIP package")?;
        let mut entries = BTreeMap::new();
        for index in 0..archive.len() {
            let mut member = archive.by_index(index)?;
            let mut data = Vec::new();
            member.read_to_end(&mut data)?;
            entries.insert(member.name().into(), data);
        }
        Ok(Self { entries })
    }

    /// Parse a required XML part with the part name attached to any error.
    pub fn xml(&self, name: &str) -> Result<Element> {
        parse(
            self.entries
                .get(name)
                .with_context(|| format!("DOCX is missing {name}"))?,
        )
        .with_context(|| format!("Cannot parse DOCX part {name}"))
    }

    /// Replace one edited part while leaving all other package bytes unchanged.
    pub fn set_xml(&mut self, name: &str, element: &Element) {
        self.entries.insert(name.into(), element.bytes());
    }

    /// Repair custom names before callers decide whether an unchanged package can be copied.
    pub fn normalize_custom_properties(&mut self) -> Result<bool> {
        let Some(bytes) = self.entries.get("docProps/custom.xml") else {
            return Ok(false);
        };
        let Some(normalized) = normalize_custom_property_names(bytes)? else {
            return Ok(false);
        };
        self.entries
            .insert("docProps/custom.xml".into(), normalized);
        Ok(true)
    }

    /// Publish a complete valid archive atomically, preserving the previous output on failure.
    pub fn save(&mut self, path: &Path) -> Result<()> {
        self.normalize()?;
        self.save_preserving(path)
    }

    /// Apply ordinary OPC normalization before MathType injection without an intermediate ZIP.
    pub fn normalize(&mut self) -> Result<()> {
        // Edit only XML parts in place; large unchanged figures stay in the same allocation.
        let entries = &mut self.entries;
        // Pandoc emits an empty note relationship part even without note links.
        // The existing OPC writer omits that harmless, unused relationship part.
        for name in [
            "word/_rels/footnotes.xml.rels",
            "word/_rels/endnotes.xml.rels",
        ] {
            if let Some(data) = entries.get(name) {
                let root = parse(data)?;
                if root.elements().next().is_none() && root.children.iter().all(|node| matches!(node, super::xml::Node::Text(text) if text.trim().is_empty())) { entries.remove(name); }
            }
        }
        let names: Vec<_> = entries
            .keys()
            .filter(|name| {
                matches!(
                    name.as_str(),
                    "word/document.xml"
                        | "word/styles.xml"
                        | "word/numbering.xml"
                        | "word/settings.xml"
                        | "word/comments.xml"
                        | "docProps/core.xml"
                ) || name.starts_with("word/footer") && name.ends_with(".xml")
                    || name.starts_with("word/header") && name.ends_with(".xml")
            })
            .cloned()
            .collect();
        for name in names {
            entries.insert(name.clone(), parse(&entries[&name])?.bytes());
        }
        entries.insert("[Content_Types].xml".into(), content_types(entries)?);
        Ok(())
    }

    /// Publish injected MathType parts without rewriting other package XML or declarations.
    pub fn save_preserving(&self, path: &Path) -> Result<()> {
        publish(&self.entries, path)
    }
}

/// Build and atomically publish the archive only after every member has been written.
fn publish<T: AsRef<[u8]>>(entries: &BTreeMap<String, T>, path: &Path) -> Result<()> {
    // Word rejects case-colliding custom names even when ZIP/XML are valid.
    // Check every publication path, including reference export and MathType output.
    let custom_properties = entries
        .get("docProps/custom.xml")
        .map(|bytes| normalize_custom_property_names(bytes.as_ref()))
        .transpose()?
        .flatten();
    // ZIP members and the central directory stream to disk, retaining atomic replacement.
    papper_core::paths::atomic_write_with(path, |file| {
        let mut writer = zip::ZipWriter::new(file);
        let options =
            SimpleFileOptions::default().compression_method(zip::CompressionMethod::Deflated);
        for (name, data) in entries {
            writer.start_file(name, options)?;
            let bytes = if name == "docProps/custom.xml" {
                custom_properties.as_deref().unwrap_or(data.as_ref())
            } else {
                data.as_ref()
            };
            writer.write_all(bytes)?;
        }
        writer.finish()?;
        Ok(())
    })
}

/// Rename case-insensitive custom property collisions without dropping values or changing IDs.
fn normalize_custom_property_names(bytes: &[u8]) -> Result<Option<Vec<u8>>> {
    let mut root = parse(bytes).context("Cannot parse DOCX part docProps/custom.xml")?;
    // Reserve authored suffixes first so fixing `code`/`Code` cannot steal `Code_2`.
    let mut reserved: BTreeSet<_> = root
        .elements()
        .filter_map(|property| property.attr("name").map(str::to_lowercase))
        .collect();
    let mut seen = BTreeSet::new();
    let mut changed = false;
    for property in root.elements_mut() {
        let Some(name) = property.attr("name").map(str::to_owned) else {
            continue;
        };
        if seen.insert(name.to_lowercase()) {
            continue;
        }
        let mut suffix = 2_u64;
        let replacement = loop {
            let candidate = format!("{name}_{suffix}");
            if reserved.insert(candidate.to_lowercase()) {
                break candidate;
            }
            suffix += 1;
        };
        property.set("name", &replacement);
        eprintln!("[DOCX] Renamed conflicting custom property '{name}' to '{replacement}'");
        changed = true;
    }
    Ok(changed.then(|| root.bytes()))
}

/// Canonicalize OPC declarations using actual package parts while retaining unknown data.
fn content_types<T: AsRef<[u8]>>(entries: &BTreeMap<String, T>) -> Result<Vec<u8>> {
    let original = parse(
        entries
            .get("[Content_Types].xml")
            .context("Missing OPC content types")?
            .as_ref(),
    )?;
    let mut declared_defaults = BTreeMap::new();
    let mut declared_overrides = BTreeMap::new();
    for entry in original.elements() {
        if entry.name == "Default" {
            if let (Some(extension), Some(content)) =
                (entry.attr("Extension"), entry.attr("ContentType"))
            {
                declared_defaults.insert(extension.to_lowercase(), content.to_owned());
            }
        } else if entry.name == "Override"
            && let (Some(name), Some(content)) = (entry.attr("PartName"), entry.attr("ContentType"))
        {
            declared_overrides.insert(name.to_owned(), content.to_owned());
        }
    }
    let mut defaults = BTreeMap::from([
        (
            "rels".to_owned(),
            "application/vnd.openxmlformats-package.relationships+xml".to_owned(),
        ),
        ("xml".to_owned(), "application/xml".to_owned()),
    ]);
    let mut overrides = BTreeMap::new();
    for name in entries
        .keys()
        .filter(|name| name.as_str() != "[Content_Types].xml" && !name.ends_with('/'))
    {
        let extension = name.rsplit('.').next().unwrap_or_default().to_lowercase();
        let absolute = format!("/{name}");
        let content = declared_overrides
            .get(&absolute)
            .or_else(|| declared_defaults.get(&extension))
            .cloned()
            .unwrap_or_else(|| "application/octet-stream".into());
        let default_pair = match extension.as_str() {
            "rels" => content == "application/vnd.openxmlformats-package.relationships+xml",
            "xml" => content == "application/xml",
            "png" => content == "image/png",
            "jpg" | "jpeg" | "jpe" => content == "image/jpeg",
            "bmp" => content == "image/bmp",
            "gif" => content == "image/gif",
            "tif" | "tiff" => content == "image/tiff",
            "emf" => content == "image/x-emf",
            "wmf" => content == "image/x-wmf",
            "wdp" => content == "image/vnd.ms-photo",
            "fntdata" => content == "application/x-fontdata",
            "xlsx" => {
                content == "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
            }
            "bin" => content.ends_with(".printerSettings"),
            _ => false,
        };
        if default_pair {
            defaults.insert(extension, content);
        } else {
            overrides.insert(absolute, content);
        }
    }
    let mut types = Element::new("Types");
    types.attrs = original.attrs;
    for (extension, content) in defaults {
        types.push(Element::with_attrs(
            "Default",
            &[("Extension", &extension), ("ContentType", &content)],
        ));
    }
    for (name, content) in overrides {
        types.push(Element::with_attrs(
            "Override",
            &[("PartName", &name), ("ContentType", &content)],
        ));
    }
    Ok(types.bytes())
}
