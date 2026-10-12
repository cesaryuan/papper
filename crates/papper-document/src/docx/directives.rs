//! Finalize DOCX empty-span fields, section stories, and caption indexes without Word automation.

use super::footer;
use super::formatting::style_index;
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
                if record[kind].as_bool() == Some(true) {
                    let blocks = footer::take_rendered_blocks(
                        document,
                        &format!("SECTION_{id}_{}", kind.to_uppercase()),
                    )?
                    .context("Missing rendered section header/footer")?;
                    let references = footer::section_story(
                        package,
                        document,
                        styles,
                        blocks,
                        kind,
                        record[format!("{kind}Alignment")]
                            .as_str()
                            .unwrap_or("center"),
                    )?;
                    stories.insert((id, kind.to_owned()), references);
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
    stories: &BTreeMap<(u64, String), Vec<Element>>,
) -> Result<()> {
    let body = document
        .child("w:body")
        .context("DOCX is missing its document body")?;
    let mut current = body
        .child("w:sectPr")
        .context("DOCX is missing final section properties")?
        .clone();
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
        current
            .word("w:type")
            .set("w:val", record["kind"].as_str().unwrap_or("nextPage"));
        for kind in ["header", "footer"] {
            let name = format!("w:{kind}Reference");
            let references = if let Some(references) = stories.get(&(id, kind.to_owned())) {
                references.clone()
            } else if record[format!("{kind}Link")].as_bool() == Some(false) {
                let mut references = Vec::new();
                for variant in ["default", "first", "even"] {
                    references.push(footer::unlink_story(
                        package,
                        document,
                        kind,
                        inherited.get(&(name.clone(), variant.to_owned())),
                        variant,
                    )?);
                }
                references
            } else {
                Vec::new()
            };
            for reference in references {
                inherited.insert(
                    (
                        name.clone(),
                        reference.attr("w:type").unwrap_or("default").to_owned(),
                    ),
                    reference.clone(),
                );
                // References precede section layout properties; header variants precede footers.
                let index = current.children.iter().take_while(|node| matches!(node,
                    Node::Element(element) if element.name == "w:headerReference" || (kind == "footer" && element.name == "w:footerReference"))).count();
                current.children.insert(index, Node::Element(reference));
            }
        }
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
