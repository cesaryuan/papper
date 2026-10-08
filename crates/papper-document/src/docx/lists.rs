//! Preserve square-bracket list markers as editable native Word numbering.

use super::formatting::style_index;
use super::package::Package;
use super::xml::{Element, Node};
use anyhow::{Context, Result};
use std::collections::{BTreeMap, BTreeSet};

/// Collect native list instances/levels carrying the Lua filter's paragraph style.
fn collect(document: &Element, style: &str, marked: &mut BTreeMap<String, BTreeSet<String>>) {
    document.visit(&mut |paragraph| {
        if paragraph.name != "w:p" {
            return;
        }
        let Some(properties) = paragraph.child("w:pPr") else {
            return;
        };
        if properties.child("w:pStyle").and_then(|s| s.attr("w:val")) != Some(style) {
            return;
        }
        if let Some(numbering) = properties.child("w:numPr")
            && let Some(id) = numbering.child("w:numId").and_then(|n| n.attr("w:val"))
        {
            let level = numbering
                .child("w:ilvl")
                .and_then(|n| n.attr("w:val"))
                .unwrap_or("0");
            marked.entry(id.into()).or_default().insert(level.into());
        }
    });
}

/// Override marked levels on their list instances, retaining restart values and indentation.
fn override_markers(
    numbering: &mut Element,
    marked: &BTreeMap<String, BTreeSet<String>>,
) -> Result<()> {
    let definitions: BTreeMap<_, _> = numbering
        .elements()
        .filter(|e| e.name == "w:abstractNum")
        .filter_map(|e| {
            e.attr("w:abstractNumId")
                .map(|id| (id.to_owned(), e.clone()))
        })
        .collect();
    for instance in numbering.elements_mut().filter(|e| e.name == "w:num") {
        let Some(levels) = instance.attr("w:numId").and_then(|id| marked.get(id)) else {
            continue;
        };
        let abstract_id = instance
            .child("w:abstractNumId")
            .and_then(|e| e.attr("w:val"))
            .context("Bracketed list has no abstract numbering ID")?;
        let definition = definitions
            .get(abstract_id)
            .context("Bracketed list numbering definition is missing")?;
        for level in levels {
            let level = level.as_str();
            let base = definition
                .elements()
                .find(|e| e.name == "w:lvl" && e.attr("w:ilvl") == Some(level))
                .context("Bracketed list numbering level is missing")?;
            let index = instance.children.iter().position(|node| {
                matches!(node, Node::Element(e) if e.name == "w:lvlOverride" && e.attr("w:ilvl") == Some(level))
            }).unwrap_or_else(|| {
                instance.push(Element::with_attrs("w:lvlOverride", &[("w:ilvl", level)]));
                instance.children.len() - 1
            });
            let Node::Element(overrides) = &mut instance.children[index] else {
                unreachable!()
            };
            let mut rendered = overrides.child("w:lvl").unwrap_or(base).clone();
            let placeholder = level
                .parse::<u8>()
                .context("Invalid bracketed list level")?
                + 1;
            rendered
                .word("w:lvlText")
                .set("w:val", format!("[%{placeholder}]"));
            overrides.remove("w:lvl");
            overrides.push(rendered);
        }
    }
    Ok(())
}

/// Finalize bracketed lists in the body and notes without altering shared abstract definitions.
pub(crate) fn bracketed(
    package: &mut Package,
    document: &mut Element,
    styles: &mut Element,
) -> Result<()> {
    let Some(index) = style_index(styles, "Bracketed List") else {
        return Ok(());
    };
    let Node::Element(style) = &mut styles.children[index] else {
        unreachable!()
    };
    let id = style
        .attr("w:styleId")
        .context("Bracketed list style has no ID")?
        .to_owned();
    // Para is needed to preserve the AST marker, but should retain compact-list spacing.
    style.word("w:basedOn").set("w:val", "Compact");
    let mut marked = BTreeMap::new();
    collect(document, &id, &mut marked);
    for name in ["word/footnotes.xml", "word/endnotes.xml"] {
        if package.entries.contains_key(name) {
            collect(&package.xml(name)?, &id, &mut marked);
        }
    }
    if marked.is_empty() {
        return Ok(());
    }
    let mut numbering = package.xml("word/numbering.xml")?;
    override_markers(&mut numbering, &marked)?;
    package.set_xml("word/numbering.xml", &numbering);
    Ok(())
}
