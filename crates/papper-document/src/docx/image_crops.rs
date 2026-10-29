//! Recover per-picture Word crops and restore editable crops in generated DOCX packages.

use super::package::Package;
use super::xml::{Element, Node};
use anyhow::{Context, Result, ensure};
use image::{DynamicImage, ImageFormat, ImageReader, RgbaImage};
use serde::{Deserialize, Serialize};
use sha2::{Digest, Sha256};
use std::io::Cursor;
use std::ops::Range;
use std::path::Path;

mod vector;

const DRAWING: &str = "http://schemas.openxmlformats.org/drawingml/2006/main";
const WORD: &str = "http://schemas.openxmlformats.org/wordprocessingml/2006/main";
const WP: &str = "http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing";
const REL: &str = "http://schemas.openxmlformats.org/officeDocument/2006/relationships";
const SVG: &str = "http://schemas.microsoft.com/office/drawing/2016/SVG/main";
const MARKER: &str = "PAPPER_IMAGE_CROP:";

/// Transport a crop through the DOCX reader/writer without altering authored image descriptions.
#[derive(Serialize, Deserialize)]
struct CropRecord {
    crop: [i32; 4],
    title: String,
    #[serde(default)]
    scale: Option<[f64; 2]>,
}

/// Prepare an isolated DOCX only when pictures need cropping or attribute recovery.
pub fn prepare_image_crops(input: &Path, output: &Path, attributes: bool) -> Result<bool> {
    let mut importer = CropImporter {
        package: Package::open(input)?,
        attributes,
        count: 0,
        serial: 0,
    };
    let parts: Vec<_> = importer
        .package
        .entries
        .keys()
        .filter(|name| name.starts_with("word/") && name.ends_with(".xml"))
        .cloned()
        .collect();
    for part in parts {
        importer.prepare_part(&part)?;
    }
    if importer.count == 0 {
        return Ok(false);
    }
    // Raster crops use PNG; vector crops keep their original format.
    let mut types = importer.package.xml("[Content_Types].xml")?;
    if !attributes {
        for (extension, content_type) in [
            ("png", "image/png"),
            ("svg", "image/svg+xml"),
            ("emf", "image/x-emf"),
            ("wmf", "image/x-wmf"),
        ] {
            if importer
                .package
                .entries
                .keys()
                .any(|name| name.ends_with(&format!(".{extension}")))
                && !types
                    .elements()
                    .any(|node| node.attr("Extension") == Some(extension))
            {
                let prefix = types.name.strip_suffix("Types").unwrap_or("");
                types.push(Element::with_attrs(
                    &format!("{prefix}Default"),
                    &[("Extension", extension), ("ContentType", content_type)],
                ));
            }
        }
        importer.package.set_xml("[Content_Types].xml", &types);
    }
    importer.package.save_preserving(output)?;
    println!(
        "[convert] {} Word image crop(s) {}",
        importer.count,
        if attributes {
            "retained as attributes"
        } else {
            "applied to exported images"
        }
    );
    Ok(true)
}

/// Keep original package media intact when separate pictures share one image relationship.
struct CropImporter {
    package: Package,
    attributes: bool,
    count: usize,
    serial: usize,
}

impl CropImporter {
    /// Edit only crop metadata and selected image relationships in one body, note or story part.
    fn prepare_part(&mut self, part: &str) -> Result<()> {
        let source = String::from_utf8(self.package.entries[part].clone())?;
        if !source.contains("srcRect") {
            return Ok(());
        }
        let document = roxmltree::Document::parse(&source)?;
        let (folder, filename) = part.rsplit_once('/').context("Invalid DOCX part path")?;
        let rel_part = format!("{folder}/_rels/{filename}.rels");
        let mut relationships = self
            .package
            .entries
            .contains_key(&rel_part)
            .then(|| self.package.xml(&rel_part))
            .transpose()?;
        let mut edits = Vec::new();
        let mut rels_changed = false;
        for rectangle in document
            .descendants()
            .filter(|node| node.has_tag_name((DRAWING, "srcRect")))
        {
            let Some(fill) = rectangle.parent() else {
                continue;
            };
            let Some(blip) = fill
                .children()
                .find(|node| node.has_tag_name((DRAWING, "blip")))
            else {
                continue;
            };
            let result = (|| -> Result<()> {
                let crop = read_crop(rectangle)?;
                if crop == [0; 4] {
                    return Ok(());
                }
                if self.attributes {
                    let drawing = fill
                        .ancestors()
                        .find(|node| node.has_tag_name((WORD, "drawing")))
                        .context("Crop is outside a supported Word picture")?;
                    let props = drawing
                        .descendants()
                        .find(|node| node.has_tag_name((WP, "docPr")))
                        .context("Picture has no description properties")?;
                    let record = CropRecord {
                        crop,
                        title: props.attribute("title").unwrap_or("").into(),
                        scale: None,
                    };
                    let value = format!("{MARKER}{}", serde_json::to_string(&record)?);
                    edits.push(attribute_edit(props, &source, "title", &value));
                } else {
                    // Word prefers the SVG extension over its raster fallback. Crop that
                    // vector source and redirect both references so neither exposes the original.
                    let mut embeds = vec![
                        blip.attribute_node((REL, "embed"))
                            .context("Linked picture is not embedded")?,
                    ];
                    for svg in blip
                        .descendants()
                        .filter(|node| node.has_tag_name((SVG, "svgBlip")))
                    {
                        embeds.push(
                            svg.attribute_node((REL, "embed"))
                                .context("SVG picture is not embedded")?,
                        );
                    }
                    let embed = embeds.last().context("Picture is not embedded")?;
                    let rels = relationships
                        .as_mut()
                        .context("Picture relationships are missing")?;
                    let relationship = rels
                        .elements()
                        .find(|node| node.attr("Id") == Some(embed.value()))
                        .context("Picture relationship is missing")?;
                    ensure!(
                        relationship.attr("TargetMode") != Some("External"),
                        "External picture cannot be cropped"
                    );
                    let target = resolve_part(
                        part,
                        relationship
                            .attr("Target")
                            .context("Picture target is missing")?,
                    )?;
                    let bytes = self
                        .package
                        .entries
                        .get(&target)
                        .context("Picture media is missing")?;
                    let (cropped, extension) = match vector::crop(bytes, &target, crop)? {
                        Some(result) => result,
                        None => (crop_pixels(bytes, crop)?, "png"),
                    };
                    let digest: String = Sha256::digest(&cropped)
                        .iter()
                        .map(|byte| format!("{byte:02x}"))
                        .collect();
                    let mut name = format!("word/media/papper-crop-{digest}.{extension}");
                    // An authored file can already occupy a generated name; never replace it.
                    let mut suffix = 0;
                    while self
                        .package
                        .entries
                        .get(&name)
                        .is_some_and(|existing| *existing != cropped)
                    {
                        suffix += 1;
                        name = format!("word/media/papper-crop-{digest}-{suffix}.{extension}");
                    }
                    self.package.entries.insert(name.clone(), cropped);
                    let id = loop {
                        self.serial += 1;
                        let candidate = format!("rIdPapperCrop{}", self.serial);
                        if !rels
                            .elements()
                            .any(|node| node.attr("Id") == Some(candidate.as_str()))
                        {
                            break candidate;
                        }
                    };
                    let prefix = rels.name.strip_suffix("Relationships").unwrap_or("");
                    rels.push(Element::with_attrs(
                        &format!("{prefix}Relationship"),
                        &[
                            ("Id", &id),
                            ("Type", &format!("{REL}/image")),
                            ("Target", &format!("/{name}")),
                        ],
                    ));
                    for embed in embeds {
                        edits.push((embed.range_value(), id.clone()));
                    }
                    edits.push((rectangle.range(), String::new()));
                    rels_changed = true;
                }
                self.count += 1;
                Ok(())
            })();
            if let Err(error) = result {
                eprintln!(
                    "[WARN] Could not recover image crop in {part}; retaining original picture: {error:#}"
                );
            }
        }
        if !edits.is_empty() {
            let mut updated = source;
            edits.sort_by_key(|(range, _)| range.start);
            for (range, value) in edits.into_iter().rev() {
                updated.replace_range(range, &value);
            }
            self.package
                .entries
                .insert(part.into(), updated.into_bytes());
        }
        if rels_changed {
            self.package.set_xml(&rel_part, &relationships.unwrap());
        }
        Ok(())
    }
}

/// Read DrawingML thousandths of a percent, including OOXML's percentage spelling.
fn read_crop(rectangle: roxmltree::Node<'_, '_>) -> Result<[i32; 4]> {
    let mut crop = [0; 4];
    for (index, side) in ["l", "t", "r", "b"].iter().enumerate() {
        if let Some(raw) = rectangle.attribute(*side) {
            crop[index] = if let Some(percent) = raw.strip_suffix('%') {
                let value = percent.parse::<f64>()? * 1000.0;
                ensure!(
                    value.is_finite() && value.abs() <= i32::MAX as f64,
                    "Invalid crop percentage"
                );
                value.round() as i32
            } else {
                raw.parse()?
            };
        }
    }
    ensure!(
        i64::from(crop[0]) + i64::from(crop[2]) < 100_000
            && i64::from(crop[1]) + i64::from(crop[3]) < 100_000,
        "Crop leaves no visible image"
    );
    Ok(crop)
}

/// Replace an attribute value by source range, preserving namespace prefixes and unrelated XML.
fn attribute_edit(
    node: roxmltree::Node<'_, '_>,
    source: &str,
    name: &str,
    value: &str,
) -> (Range<usize>, String) {
    // Source XML can use either quote style; escaping both also preserves titles
    // containing apostrophes when an existing single-quoted attribute is replaced.
    let escaped = html_escape::encode_double_quoted_attribute(value).replace('\'', "&apos;");
    if let Some(attribute) = node.attribute_node(name) {
        return (attribute.range_value(), escaped);
    }
    let start = node.range().start;
    let offset = source[start..]
        .find(|c: char| c.is_ascii_whitespace() || c == '/' || c == '>')
        .unwrap();
    let position = start + offset;
    (position..position, format!(" {name}=\"{escaped}\""))
}

/// Resolve OPC-relative media targets without allowing traversal outside the package.
fn resolve_part(part: &str, target: &str) -> Result<String> {
    let path = if let Some(absolute) = target.strip_prefix('/') {
        absolute.to_owned()
    } else {
        format!("{}/{target}", part.rsplit_once('/').unwrap().0)
    };
    let mut components = Vec::new();
    for component in path.split('/') {
        match component {
            ".." => {
                ensure!(
                    components.pop().is_some(),
                    "Image target escapes DOCX package"
                );
            }
            "." | "" => {}
            _ => components.push(component),
        }
    }
    Ok(components.join("/"))
}

/// Crop at source resolution and preserve negative Word crops as transparent canvas padding.
fn crop_pixels(bytes: &[u8], crop: [i32; 4]) -> Result<Vec<u8>> {
    let image = ImageReader::new(Cursor::new(bytes))
        .with_guessed_format()?
        .decode()?;
    let (width, height) = (image.width(), image.height());
    let [left, top, right, bottom] = crop;
    let x = (f64::from(width) * f64::from(left) / 100_000.0).round() as i64;
    let y = (f64::from(height) * f64::from(top) / 100_000.0).round() as i64;
    let end_x = (f64::from(width) * (1.0 - f64::from(right) / 100_000.0)).round() as i64;
    let end_y = (f64::from(height) * (1.0 - f64::from(bottom) / 100_000.0)).round() as i64;
    let output_width = u32::try_from(end_x - x).context("Invalid crop width")?;
    let output_height = u32::try_from(end_y - y).context("Invalid crop height")?;
    ensure!(
        output_width > 0 && output_height > 0,
        "Crop is smaller than one pixel"
    );
    // Outward crops can request arbitrarily large padding; share the decoder's
    // allocation limit rather than allowing malformed metadata to exhaust memory.
    let allocation = u64::from(output_width)
        .checked_mul(u64::from(output_height))
        .and_then(|pixels| pixels.checked_mul(4))
        .context("Crop canvas is too large")?;
    image::Limits::default().reserve(allocation)?;
    let mut output = RgbaImage::new(output_width, output_height);
    image::imageops::replace(&mut output, &image.to_rgba8(), -x, -y);
    let mut encoded = Cursor::new(Vec::new());
    DynamicImage::ImageRgba8(output).write_to(&mut encoded, ImageFormat::Png)?;
    Ok(encoded.into_inner())
}

/// Apply image attributes after stories are relocated, even with optional formatting disabled.
pub(super) fn finalize(package: &mut Package, document: &mut Element) -> Result<bool> {
    let mut changed = finalize_tree(document)?;
    let parts: Vec<_> = package
        .entries
        .iter()
        .filter(|(name, bytes)| {
            name.starts_with("word/")
                && name.ends_with(".xml")
                && name.as_str() != "word/document.xml"
                && bytes
                    .windows(MARKER.len())
                    .any(|window| window == MARKER.as_bytes())
        })
        .map(|(name, _)| name.clone())
        .collect();
    for name in parts {
        let mut part = package.xml(&name)?;
        if finalize_tree(&mut part)? {
            package.set_xml(&name, &part);
            changed = true;
        }
    }
    Ok(changed)
}

/// Restore crop rectangles, image titles and visible aspect ratios on each individual drawing.
fn finalize_tree(element: &mut Element) -> Result<bool> {
    let mut changed = false;
    if element.name == "w:drawing" {
        let mut record = None;
        element.visit(&mut |node| {
            if node.name == "wp:docPr" {
                record = node
                    .attr("title")
                    .and_then(|title| title.strip_prefix(MARKER))
                    .map(str::to_owned);
            }
        });
        if let Some(raw) = record {
            let record: CropRecord =
                serde_json::from_str(&raw).context("Invalid image crop metadata")?;
            let [left, top, right, bottom] = record.crop;
            ensure!(
                i64::from(left) + i64::from(right) < 100_000
                    && i64::from(top) + i64::from(bottom) < 100_000,
                "Crop leaves no visible image"
            );
            element.visit_mut(&mut |node| {
                if matches!(node.name.as_str(), "wp:docPr" | "pic:cNvPr")
                    && node
                        .attr("title")
                        .is_some_and(|title| title.starts_with(MARKER))
                {
                    if record.title.is_empty() {
                        node.attrs.remove("title");
                    } else {
                        node.set("title", &record.title);
                    }
                }
                if node.name == "pic:blipFill" {
                    node.remove("a:srcRect");
                    let mut rectangle = Element::new("a:srcRect");
                    for (side, value) in ["l", "t", "r", "b"].iter().zip(record.crop) {
                        if value != 0 {
                            rectangle.set(side, value);
                        }
                    }
                    let index = node
                        .children
                        .iter()
                        .position(
                            |child| matches!(child, Node::Element(child) if child.name == "a:blip"),
                        )
                        .map_or(0, |index| index + 1);
                    node.children.insert(index, Node::Element(rectangle));
                }
                if let Some(scale) = record.scale
                    && matches!(node.name.as_str(), "wp:extent" | "a:ext")
                {
                    for (dimension, factor) in ["cx", "cy"].iter().zip(scale) {
                        if let Some(value) =
                            node.attr(dimension).and_then(|raw| raw.parse::<f64>().ok())
                        {
                            node.set(dimension, (value * factor).round().max(1.0) as i64);
                        }
                    }
                }
            });
            changed = true;
        }
    }
    for child in element.elements_mut() {
        changed |= finalize_tree(child)?;
    }
    Ok(changed)
}
