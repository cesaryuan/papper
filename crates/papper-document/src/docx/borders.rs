//! Preserve Word paragraph-border inheritance while applying compact border declarations.

use super::formatting::{get_style_mut, style_index};
use super::xml::{Element, Node};
use anyhow::Result;
use papper_core::metadata::PmtSettings;
use papper_core::paragraph_borders::{BORDER_SIDES, BorderLine, ParagraphBorders, parse_borders};
use std::collections::{BTreeMap, BTreeSet};

/// Read native border lines and per-side distances, including explicit nil values.
pub(super) fn read(properties: Option<&Element>) -> ParagraphBorders {
    let mut result = ParagraphBorders::default();
    let Some(borders) = properties.and_then(|properties| properties.child("w:pBdr")) else {
        return result;
    };
    for side in BORDER_SIDES {
        if let Some(border) = borders.child(&format!("w:{side}")) {
            if let Some(style) = border.attr("w:val") {
                result.lines.insert(
                    side.into(),
                    BorderLine {
                        style: style.into(),
                        width: border
                            .attr("w:sz")
                            .and_then(|raw| raw.parse().ok())
                            .unwrap_or(4),
                        color: border.attr("w:color").unwrap_or("auto").into(),
                        inherited_attributes: [
                            "themeColor",
                            "themeTint",
                            "themeShade",
                            "shadow",
                            "frame",
                        ]
                        .into_iter()
                        .filter_map(|name| {
                            border
                                .attr(&format!("w:{name}"))
                                .map(|value| (name.into(), value.into()))
                        })
                        .collect(),
                    },
                );
            }
            if let Some(space) = border.attr("w:space").and_then(|raw| raw.parse().ok()) {
                result.spaces.insert(side.into(), space);
            }
        }
    }
    result
}

/// Resolve border inheritance and pending configuration independently of YAML key order.
fn resolve_style(
    styles: &Element,
    id: &str,
    overrides: &BTreeMap<String, ParagraphBorders>,
    visited: &mut BTreeSet<String>,
) -> ParagraphBorders {
    if !visited.insert(id.into()) {
        return ParagraphBorders::default();
    }
    let style = styles
        .elements()
        .find(|style| style.name == "w:style" && style.attr("w:styleId") == Some(id));
    let mut result = if let Some(parent) = style
        .and_then(|style| style.child("w:basedOn"))
        .and_then(|parent| parent.attr("w:val"))
    {
        resolve_style(styles, parent, overrides, visited)
    } else {
        read(
            styles
                .child("w:docDefaults")
                .and_then(|defaults| defaults.child("w:pPrDefault"))
                .and_then(|defaults| defaults.child("w:pPr")),
        )
    };
    result.overlay(&read(style.and_then(|style| style.child("w:pPr"))));
    if let Some(configured) = overrides.get(id) {
        result.overlay(configured);
    }
    result
}

/// Apply all named border settings after collecting them, so configured parents work in any order.
pub(super) fn apply_styles(styles: &mut Element, settings: &PmtSettings) -> Result<()> {
    let Some(configured) = &settings.fields().docx_style else {
        return Ok(());
    };
    let mut overrides = BTreeMap::new();
    let mut names = Vec::new();
    for (name, raw) in configured.iter() {
        let Some(value) = raw.get("borders").filter(|value| !value.is_null()) else {
            continue;
        };
        let Some(index) = style_index(styles, name) else {
            continue;
        };
        if let Node::Element(style) = &styles.children[index]
            && style.attr("w:type") != Some("character")
            && let Some(id) = style.attr("w:styleId")
        {
            let patch = parse_borders(value, &format!("docxStyle.{name}.borders"))?;
            overrides
                .entry(id.to_owned())
                .or_insert_with(ParagraphBorders::default)
                .overlay(&patch);
            names.push((name.to_owned(), id.to_owned()));
        }
    }
    let effective: BTreeMap<_, _> = overrides
        .keys()
        .map(|id| {
            (
                id.clone(),
                resolve_style(styles, id, &overrides, &mut BTreeSet::new()),
            )
        })
        .collect();
    for (name, id) in names {
        if let Some(style) = get_style_mut(styles, &name) {
            write(style.word("w:pPr"), &effective[&id], &overrides[&id]);
        }
    }
    Ok(())
}

/// Resolve selected style and direct properties before overriding a locally formatted paragraph.
pub(super) fn apply_paragraph(paragraph: &mut Element, styles: &Element, patch: &ParagraphBorders) {
    let style = super::formatting::paragraph_style(paragraph).or_else(|| {
        styles
            .elements()
            .find(|style| {
                style.attr("w:type") == Some("paragraph") && style.attr("w:default") == Some("1")
            })
            .and_then(|style| style.attr("w:styleId"))
    });
    let mut effective = style
        .map(|id| resolve_style(styles, id, &BTreeMap::new(), &mut BTreeSet::new()))
        .unwrap_or_default();
    effective.overlay(&read(paragraph.child("w:pPr")));
    effective.overlay(patch);
    write(paragraph.word("w:pPr"), &effective, patch);
}

/// Materialize changed sides, retaining inherited distances and avoiding theme-color precedence.
fn write(properties: &mut Element, effective: &ParagraphBorders, patch: &ParagraphBorders) {
    for side in BORDER_SIDES {
        if !patch.lines.contains_key(side) && !patch.spaces.contains_key(side) {
            continue;
        }
        let Some(line) = effective.lines.get(side) else {
            continue;
        };
        let border = properties.word("w:pBdr").word(&format!("w:{side}"));
        if patch.lines.contains_key(side) {
            // A fixed local color must override a reference theme color too.
            for attribute in ["w:themeColor", "w:themeTint", "w:themeShade"] {
                border.attrs.remove(attribute);
            }
        } else {
            for (name, value) in &line.inherited_attributes {
                border.set(&format!("w:{name}"), value);
            }
        }
        border.set("w:val", &line.style);
        border.set("w:sz", line.width);
        border.set("w:color", &line.color);
        if let Some(space) = effective.spaces.get(side) {
            border.set("w:space", space);
        }
    }
}
