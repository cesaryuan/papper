//! Finalize DOCX empty-span fields, section stories, and caption indexes without Word automation.

use super::footer;
use super::formatting::{length_twips, style_index};
use super::package::Package;
use super::xml::{Element, Node};
use anyhow::{Context, Result, bail};
use serde_json::Value;
use std::collections::BTreeMap;

/// Consume layout requests even when optional DOCX formatting is disabled.
pub(super) fn apply(
    package: &mut Package,
    document: &mut Element,
    styles: &mut Element,
) -> Result<bool> {
    let mut changed = false;
    let mut update = false;
    let mut records = Vec::new();
    let mut invalid = None;
    document.visit(&mut |element| {
        if element.name == "w:r" {
            let text = element.text();
            changed |= text.starts_with("PMT_FOOTER_PAGE:") || text.starts_with("PMT_DOCX_FIELD:");
        }
        if element.name == "w:p"
            && let Some(raw) = element.text().strip_prefix("PMT_DOCX_SECTION:")
        {
            match serde_json::from_str::<Value>(raw) {
                Ok(record) => records.push(record),
                Err(error) => invalid = Some(error),
            }
        }
        if element.name == "w:instrText" && element.direct_text().trim_start().starts_with("TOC ") {
            update = true;
        }
    });
    if let Some(error) = invalid {
        return Err(error).context("Invalid DOCX section directive");
    }
    for child in &mut document.children {
        footer::finalize_page_fields(child, styles)?;
    }
    if update
        && let Some(index) = style_index(styles, "TOC Heading")
        && let Node::Element(style) = &styles.children[index]
        && let Some(identifier) = style.attr("w:styleId")
    {
        document.visit_mut(&mut |paragraph| {
            if paragraph.name == "w:p"
                && paragraph
                    .child("w:pPr")
                    .and_then(|properties| properties.child("w:pStyle"))
                    .and_then(|style| style.attr("w:val"))
                    == Some(identifier)
            {
                // Custom references may base TOC Heading on Heading 1; exclude
                // directory titles from inherited numbering and outline entries.
                paragraph
                    .word("w:pPr")
                    .word("w:numPr")
                    .word("w:numId")
                    .set("w:val", "0");
                paragraph
                    .word("w:pPr")
                    .word("w:outlineLvl")
                    .set("w:val", "9");
                changed = true;
            }
        });
    }
    if !records.is_empty() {
        let mut stories = BTreeMap::new();
        for record in &records {
            let id = record["id"]
                .as_u64()
                .context("Missing section directive ID")?;
            for kind in ["header", "footer"] {
                for suffix in ["", "Default", "Even", "First"] {
                    let key = format!("{kind}{suffix}");
                    if record[&key].as_bool() != Some(true) {
                        continue;
                    }
                    let blocks = footer::take_rendered_blocks(
                        document,
                        &format!("SECTION_{id}_{}", key.to_uppercase()),
                    )?
                    .context("Missing rendered section header/footer")?;
                    let reference = footer::section_story(
                        package,
                        document,
                        styles,
                        blocks,
                        kind,
                        record[format!("{kind}Alignment")]
                            .as_str()
                            .unwrap_or("center"),
                    )?;
                    stories.insert((id, key), reference);
                }
            }
        }
        sections(package, document, &records, &stories)?;
        changed = true;
    }
    if update {
        let mut settings = package.xml("word/settings.xml")?;
        settings.word("w:updateFields").set("w:val", "true");
        package.set_xml("word/settings.xml", &settings);
        changed = true;
    }
    Ok(changed)
}

/// Add caption styles based on each authored style so Word lists keep live caption content.
pub(super) fn index_captions(document: &mut Element, styles: &mut Element) -> Result<bool> {
    let mut changed = false;
    document.visit_mut(&mut |paragraph| {
        if paragraph.name != "w:p" {
            return;
        }
        let mut kind = None;
        paragraph.children.retain(|node| {
            if let Node::Element(run) = node
                && run.name == "w:r"
                && let Some(value) = run.text().strip_prefix("PMT_DOCX_CAPTION:")
            {
                kind = Some(value.to_owned());
                return false;
            }
            true
        });
        let Some(kind) = kind else {
            return;
        };
        let (identifier, name, fallback) = match kind.as_str() {
            "T" => (
                "PapperTableCaption",
                "Papper Table Caption",
                "Table Caption",
            ),
            "S" => (
                "PapperSubfigureCaption",
                "Papper Subfigure Caption",
                "Image Caption",
            ),
            _ => (
                "PapperFigureCaption",
                "Papper Figure Caption",
                "Image Caption",
            ),
        };
        if style_index(styles, name).is_none() {
            let base = paragraph
                .child("w:pPr")
                .and_then(|properties| properties.child("w:pStyle"))
                .and_then(|style| style.attr("w:val"))
                .map(str::to_owned)
                .or_else(|| {
                    style_index(styles, fallback).and_then(|index| match &styles.children[index] {
                        Node::Element(style) => style.attr("w:styleId").map(str::to_owned),
                        _ => None,
                    })
                })
                .unwrap_or_else(|| "Caption".into());
            let mut style = Element::with_attrs(
                "w:style",
                &[("w:type", "paragraph"), ("w:styleId", identifier)],
            );
            style.push(Element::with_attrs("w:name", &[("w:val", name)]));
            style.push(Element::with_attrs("w:basedOn", &[("w:val", &base)]));
            styles.push(style);
        }
        paragraph.word("w:pPr").word("w:pStyle").set(
            "w:val",
            style_index(styles, name)
                .and_then(|index| match &styles.children[index] {
                    Node::Element(style) => style.attr("w:styleId"),
                    _ => None,
                })
                .unwrap_or(identifier),
        );
        changed = true;
    });
    Ok(changed)
}

/// Close each preceding section and apply options to the following one in document order.
fn sections(
    package: &mut Package,
    document: &mut Element,
    records: &[Value],
    stories: &BTreeMap<(u64, String), Element>,
) -> Result<()> {
    let body = document
        .child("w:body")
        .context("DOCX is missing its document body")?;
    let mut current = body
        .child("w:sectPr")
        .context("DOCX is missing final section properties")?
        .clone();
    let mut settings = package.xml("word/settings.xml")?;
    let enabled = settings
        .child("w:evenAndOddHeaders")
        .is_some_and(|value| !matches!(value.attr("w:val"), Some("false" | "0" | "off")));
    let needs_even = records.iter().any(|record| {
        record["headerEven"].as_bool() == Some(true) || record["footerEven"].as_bool() == Some(true)
    });
    if needs_even && !enabled {
        // Enabling Word's document-wide switch must not expose dormant even stories
        // or turn unchanged sections' even pages blank, including the initial section.
        for kind in ["header", "footer"] {
            let name = format!("w:{kind}Reference");
            let primary = current
                .elements()
                .find(|child| child.name == name && child.attr("w:type") == Some("default"))
                .cloned();
            let mut even = match primary {
                Some(reference) => reference,
                None => footer::unlink_story(package, document, kind, None, "even")?,
            };
            even.set("w:type", "even");
            current.children.retain(|node| {
                !matches!(node,
                Node::Element(child) if child.name == name && child.attr("w:type") == Some("even"))
            });
            insert_story_reference(&mut current, even, kind);
        }
        settings.word("w:evenAndOddHeaders").set("w:val", "true");
        package.set_xml("word/settings.xml", &settings);
    }
    let mut inherited: BTreeMap<(String, String), Element> = BTreeMap::new();
    for reference in current.elements().filter(|element| {
        matches!(
            element.name.as_str(),
            "w:headerReference" | "w:footerReference"
        )
    }) {
        inherited.insert(
            (
                reference.name.clone(),
                reference.attr("w:type").unwrap_or("default").to_owned(),
            ),
            reference.clone(),
        );
    }
    let mut replacements = Vec::new();
    let mut even_active = enabled;
    for record in records {
        let id = record["id"]
            .as_u64()
            .context("Missing section directive ID")?;
        replacements.push(current.clone());
        current.remove("w:headerReference");
        current.remove("w:footerReference");
        // A page restart applies only to the authored section, not every section after it.
        if let Some(numbering) = current.child_mut("w:pgNumType") {
            numbering.attrs.remove("w:start");
        }
        if let Some(start) = record["pageStart"].as_str() {
            current.word("w:pgNumType").set("w:start", start);
        }
        for kind in ["header", "footer"] {
            let key = format!("{kind}Distance");
            if !record[&key].is_null() {
                let distance = length_twips(&record[&key])
                    .with_context(|| format!("Invalid sectionBreak {key}"))?;
                if !(0..=u32::MAX as i64).contains(&distance)
                    || papper_core::style_values::parse_length(&record[&key], &key)?.0 < 0
                {
                    bail!("sectionBreak {key} must be a nonnegative Word length");
                }
                current
                    .word("w:pgMar")
                    .set(&format!("w:{kind}"), distance.to_string());
            }
        }
        let was_first = current
            .child("w:titlePg")
            .is_some_and(|value| !matches!(value.attr("w:val"), Some("false" | "0" | "off")));
        if let Some(first) = record["differentFirstPage"].as_bool() {
            current
                .word("w:titlePg")
                .set("w:val", if first { "true" } else { "false" });
        }
        current
            .word("w:type")
            .set("w:val", record["kind"].as_str().unwrap_or("nextPage"));
        for kind in ["header", "footer"] {
            let name = format!("w:{kind}Reference");
            let has_content = ["", "Default", "Even", "First"]
                .iter()
                .any(|suffix| stories.contains_key(&(id, format!("{kind}{suffix}"))));
            let mut references: Vec<Element> = Vec::new();
            for (variant, suffix) in [("default", "Default"), ("first", "First"), ("even", "Even")]
            {
                let authored = stories
                    .get(&(id, format!("{kind}{suffix}")))
                    .or_else(|| stories.get(&(id, kind.to_owned())));
                let reference = if let Some(reference) = authored {
                    let mut reference = reference.clone();
                    reference.set("w:type", variant);
                    Some(reference)
                } else if variant == "even"
                    && needs_even
                    && !even_active
                    && stories.contains_key(&(id, format!("{kind}Default")))
                {
                    // Before any authored even variant, normal content applied to both
                    // parities. A later section's global toggle must preserve that behavior.
                    let mut reference = references
                        .iter()
                        .find(|reference| reference.attr("w:type") == Some("default"))
                        .context("Missing rendered default section story")?
                        .clone();
                    reference.set("w:type", variant);
                    Some(reference)
                } else if variant == "first"
                    && !was_first
                    && record["differentFirstPage"].as_bool() == Some(true)
                {
                    // A first-page header must not unexpectedly blank the unchanged footer,
                    // or reveal an inactive first-page story inherited from an earlier section.
                    let primary = references
                        .iter()
                        .find(|reference| reference.attr("w:type") == Some("default"))
                        .or_else(|| inherited.get(&(name.clone(), "default".into())));
                    let mut reference = match primary {
                        Some(reference) => reference.clone(),
                        None => footer::unlink_story(package, document, kind, None, variant)?,
                    };
                    reference.set("w:type", variant);
                    Some(reference)
                } else if !has_content && record[format!("{kind}Link")].as_bool() == Some(false) {
                    Some(footer::unlink_story(
                        package,
                        document,
                        kind,
                        inherited.get(&(name.clone(), variant.to_owned())),
                        variant,
                    )?)
                } else {
                    None
                };
                if let Some(reference) = reference {
                    references.push(reference);
                }
            }
            for reference in references {
                inherited.insert(
                    (
                        name.clone(),
                        reference.attr("w:type").unwrap_or("default").to_owned(),
                    ),
                    reference.clone(),
                );
                insert_story_reference(&mut current, reference, kind);
            }
        }
        even_active |= record["headerEven"].as_bool() == Some(true)
            || record["footerEven"].as_bool() == Some(true);
    }
    let body = document
        .child_mut("w:body")
        .context("DOCX is missing its document body")?;
    let mut index = 0;
    for node in &mut body.children {
        if let Node::Element(paragraph) = node
            && paragraph.name == "w:p"
            && paragraph.text().starts_with("PMT_DOCX_SECTION:")
        {
            paragraph.children.clear();
            paragraph.word("w:pPr").push(replacements[index].clone());
            index += 1;
        }
    }
    if index != records.len() {
        bail!(
            "DOCX sectionBreak must be a top-level body paragraph, outside tables, lists, and notes"
        );
    }
    *body
        .child_mut("w:sectPr")
        .context("Missing final section properties")? = current;
    Ok(())
}

/// Keep Word's required header-before-footer ordering when adding section references.
fn insert_story_reference(section: &mut Element, reference: Element, kind: &str) {
    let index = section.children.iter().take_while(|node| matches!(node,
        Node::Element(element) if element.name == "w:headerReference" || (kind == "footer" && element.name == "w:footerReference"))).count();
    section.children.insert(index, Node::Element(reference));
}
