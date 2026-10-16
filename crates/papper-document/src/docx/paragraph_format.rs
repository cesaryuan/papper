//! Materialize fenced-Div formatting as direct Word paragraph/run properties.

use super::formatting::{apply_paragraph_properties, apply_text_properties};
use super::package::Package;
use super::xml::{Element, Node};
use anyhow::Result;
use papper_core::paragraph_format::resolve_scopes;
use std::collections::{BTreeMap, BTreeSet};

const MARKER: &str = "PMT_PARAGRAPH_FORMAT:";

/// Finalize body, note and story paragraphs even when optional postprocessing is disabled.
pub(super) fn apply(
    package: &mut Package,
    document: &mut Element,
    styles: &Element,
) -> Result<bool> {
    let character_styles: BTreeMap<_, _> = styles
        .elements()
        .filter(|style| style.name == "w:style" && style.attr("w:type") == Some("character"))
        .filter_map(|style| style.attr("w:styleId").map(|id| (id.to_owned(), style)))
        .collect();
    let mut changed = apply_tree(document, &character_styles)?;
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
        if apply_tree(&mut part, &character_styles)? {
            package.set_xml(&name, &part);
            changed = true;
        }
    }
    Ok(changed)
}

/// Consume one paragraph's hidden transport run and retain every other authored run.
fn apply_tree(element: &mut Element, styles: &BTreeMap<String, &Element>) -> Result<bool> {
    let mut changed = false;
    if element.name == "w:p" {
        let marker = element
            .children
            .iter()
            .enumerate()
            .find_map(|(index, node)| {
                if let Node::Element(run) = node
                    && run.name == "w:r"
                    && run
                        .child("w:rPr")
                        .is_some_and(|properties| properties.child("w:vanish").is_some())
                {
                    return run
                        .text()
                        .strip_prefix(MARKER)
                        .map(|payload| (index, payload.to_owned()));
                }
                None
            });
        if let Some((index, payload)) = marker {
            let format = resolve_scopes(&payload)?;
            element.children.remove(index);
            apply_paragraph_properties(element.word("w:pPr"), &format.raw, "paragraph format")?;
            reset_inherited_indents(element.word("w:pPr"), &format.raw);
            let mut defaults = Element::new("w:rPr");
            apply_text_properties(&mut defaults, &format.raw, "paragraph format")?;
            for child in element.elements_mut().filter(|child| child.name != "w:pPr") {
                apply_runs(child, &defaults, styles);
            }
            changed = true;
        }
    }
    for child in element.elements_mut() {
        changed |= apply_tree(child, styles)?;
    }
    Ok(changed)
}

/// Neutralize inherited character indents when a local physical indent replaces them in Word.
fn reset_inherited_indents(
    properties: &mut Element,
    raw: &serde_json::Map<String, serde_json::Value>,
) {
    let chars = raw.get("firstLineIndentChars");
    let indent = raw
        .get("indentation")
        .and_then(serde_json::Value::as_object);
    if chars.is_some() || indent.is_some() {
        let properties = properties.word("w:ind");
        if let Some(chars) = chars {
            properties.set("w:firstLine", "0");
            // Word ignores zero character indents, but even a zero hanging
            // attribute suppresses a positive first-line character indent.
            if papper_core::style_values::parse_number(
                chars,
                "paragraph format.firstLineIndentChars",
            )
            .is_ok_and(|value| value == 0.0)
            {
                properties.set("w:hanging", "0");
                properties.set("w:hangingChars", "0");
            }
        }
        if let Some(indent) = indent {
            if indent.contains_key("firstLine") || indent.contains_key("hanging") {
                properties.set("w:firstLineChars", "0");
                properties.set("w:hangingChars", "0");
            }
            for (side, character_attribute) in [("left", "w:leftChars"), ("right", "w:rightChars")]
            {
                if indent.contains_key(side) {
                    properties.set(character_attribute, "0");
                }
            }
        }
    }
}

/// Collect explicit character-style properties, retaining nearest values and breaking cycles.
fn character_properties(run: &Element, styles: &BTreeMap<String, &Element>) -> Element {
    let mut result = Element::new("w:rPr");
    let mut next = run
        .child("w:rPr")
        .and_then(|properties| properties.child("w:rStyle"))
        .and_then(|style| style.attr("w:val"));
    let mut visited = BTreeSet::new();
    while let Some(id) = next {
        if !visited.insert(id) {
            break;
        }
        let Some(style) = styles.get(id) else {
            break;
        };
        if let Some(properties) = style.child("w:rPr") {
            for property in properties.elements() {
                let target = result.word(&property.name);
                for (name, value) in &property.attrs {
                    target
                        .attrs
                        .entry(name.clone())
                        .or_insert_with(|| value.clone());
                }
            }
        }
        next = style
            .child("w:basedOn")
            .and_then(|parent| parent.attr("w:val"));
    }
    result
}

/// Add local text defaults below explicit inline formatting and character-style properties.
fn apply_runs(element: &mut Element, defaults: &Element, styles: &BTreeMap<String, &Element>) {
    if element.name == "w:r" && !defaults.children.is_empty() {
        let character = character_properties(element, styles);
        let properties = element.word("w:rPr");
        for property in defaults.elements() {
            if property.name == "w:rFonts" {
                for (attribute, value) in &property.attrs {
                    let theme = format!("{attribute}Theme");
                    let protected = [properties.child("w:rFonts"), character.child("w:rFonts")]
                        .into_iter()
                        .flatten()
                        .any(|fonts| {
                            fonts.attr(attribute).is_some() || fonts.attr(&theme).is_some()
                        });
                    if !protected {
                        properties.word("w:rFonts").set(attribute, value);
                    }
                }
            } else if properties.child(&property.name).is_none()
                && character.child(&property.name).is_none()
            {
                *properties.word(&property.name) = property.clone();
            }
        }
    } else if element.name != "w:p" {
        // Text boxes contain independent paragraphs; do not leak the enclosing run's scope.
        for child in element.elements_mut() {
            apply_runs(child, defaults, styles);
        }
    }
}
