//! Resolve Latin equation font sizes without a Word process or full style engine.

use super::Binding;
use crate::docx::xml::{Element, Node};
use std::collections::{BTreeMap, BTreeSet};

/// Read explicit half-point sizes, ignoring complex-script-only run overrides.
fn size(properties: Option<&Element>, complex: bool) -> Option<i64> {
    let properties = properties?;
    let value = properties
        .child("w:sz")
        .and_then(|node| node.attr("w:val"))
        .and_then(|value| value.parse().ok());
    value.or_else(|| {
        if complex {
            properties
                .child("w:szCs")
                .and_then(|node| node.attr("w:val"))
                .and_then(|value| value.parse().ok())
        } else {
            None
        }
    })
}

/// Resolve basedOn sizes recursively, terminating safely on malformed style cycles.
fn style_size<'a>(
    id: &str,
    styles: &BTreeMap<&'a str, &'a Element>,
    seen: &mut BTreeSet<String>,
) -> Option<i64> {
    if !seen.insert(id.into()) {
        return None;
    }
    let style = styles.get(id)?;
    size(style.child("w:rPr"), false).or_else(|| {
        let parent = style.child("w:basedOn")?.attr("w:val")?;
        style_size(parent, styles, seen)
    })
}

/// Retrieve an immutable original-tree element by its stable child-index path.
fn at<'a>(root: &'a Element, path: &[usize]) -> Option<&'a Element> {
    let mut element = root;
    for &index in path {
        let Node::Element(child) = element.children.get(index)? else {
            return None;
        };
        element = child;
    }
    Some(element)
}

/// Find the nearest original ancestor of a given Word element kind.
fn ancestor<'a>(root: &'a Element, path: &[usize], name: &str) -> Option<(&'a Element, usize)> {
    (0..path.len()).rev().find_map(|depth| {
        let element = at(root, &path[..depth])?;
        (element.name == name).then_some((element, depth))
    })
}

/// Apply direct→neighbor→paragraph→table→defaults precedence to every bound equation.
pub(super) fn resolve_sizes(document: &Element, styles_root: &Element, bindings: &mut [Binding]) {
    let styles: BTreeMap<_, _> = styles_root
        .elements()
        .filter_map(|style| style.attr("w:styleId").map(|id| (id, style)))
        .collect();
    let default = size(
        styles_root
            .child("w:docDefaults")
            .and_then(|element| element.child("w:rPrDefault"))
            .and_then(|element| element.child("w:rPr")),
        true,
    );
    for binding in bindings {
        let Some(marker) = at(document, &binding.marker) else {
            continue;
        };
        let mut resolved = size(marker.child("w:rPr"), false);
        if let Some((paragraph, depth)) = ancestor(document, &binding.marker, "w:p") {
            if resolved.is_none() && binding.marker.len() == depth + 1 {
                let index = binding.marker[depth];
                for offset in 1..paragraph.children.len() {
                    for candidate in [index.checked_sub(offset), index.checked_add(offset)] {
                        let Some(Node::Element(neighbor)) =
                            candidate.and_then(|index| paragraph.children.get(index))
                        else {
                            continue;
                        };
                        if neighbor.name == "w:r" {
                            resolved = size(neighbor.child("w:rPr"), false);
                        }
                        if resolved.is_some() {
                            break;
                        }
                    }
                    if resolved.is_some() {
                        break;
                    }
                }
            }
            let properties = paragraph.child("w:pPr");
            resolved = resolved
                .or_else(|| size(properties.and_then(|element| element.child("w:rPr")), false));
            resolved = resolved.or_else(|| {
                properties
                    .and_then(|element| element.child("w:pStyle"))
                    .and_then(|element| element.attr("w:val"))
                    .and_then(|id| style_size(id, &styles, &mut BTreeSet::new()))
            });
        }
        resolved = resolved
            .or_else(|| {
                let (table, _) = ancestor(document, &binding.marker, "w:tbl")?;
                let id = table.child("w:tblPr")?.child("w:tblStyle")?.attr("w:val")?;
                style_size(id, &styles, &mut BTreeSet::new())
            })
            .or(default);
        binding.size = resolved
            .filter(|size| *size > 0)
            .map(|size| size as f64 / 2.0);
    }
}
