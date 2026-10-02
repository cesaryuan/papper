//! Preserve every OPC ZIP member while editing only selected Word XML parts.

use super::xml::{Element, parse};
use anyhow::{Context, Result};
use std::collections::BTreeMap;
use std::io::{Cursor, Read, Write};
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

    /// Publish a complete valid archive atomically, preserving the previous output on failure.
    pub fn save(&self, path: &Path) -> Result<()> {
        let mut entries = self.entries.clone();
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
        entries.insert("[Content_Types].xml".into(), content_types(&entries)?);
        publish(&entries, path)
    }

    /// Publish injected MathType parts without rewriting other package XML or declarations.
    pub fn save_preserving(&self, path: &Path) -> Result<()> {
        publish(&self.entries, path)
    }
}

/// Build and atomically publish the archive only after every member has been written.
fn publish(entries: &BTreeMap<String, Vec<u8>>, path: &Path) -> Result<()> {
    let mut buffer = Cursor::new(Vec::new());
    {
        let mut writer = zip::ZipWriter::new(&mut buffer);
        let options =
            SimpleFileOptions::default().compression_method(zip::CompressionMethod::Deflated);
        for (name, data) in entries {
            writer.start_file(name, options)?;
            writer.write_all(data)?;
        }
        writer.finish()?;
    }
    papper_core::paths::atomic_write(path, buffer.get_ref())
}

/// Canonicalize OPC declarations using actual package parts while retaining unknown data.
fn content_types(entries: &BTreeMap<String, Vec<u8>>) -> Result<Vec<u8>> {
    let original = parse(
        entries
            .get("[Content_Types].xml")
            .context("Missing OPC content types")?,
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
