//! Read WordprocessingML and resolve typed, localized style names.

use super::model::*;
use super::overrides::normalized_style_spec;
use anyhow::{Context, Result};
use roxmltree::{Document, Node};
use serde_json::{Map, Value};
use std::collections::{HashMap, HashSet};

const WORD_NS: &str = "http://schemas.openxmlformats.org/wordprocessingml/2006/main";

/// Find a direct WordprocessingML child without matching foreign namespaces.
fn child<'a, 'input>(node: Node<'a, 'input>, name: &str) -> Option<Node<'a, 'input>> {
    node.children()
        .find(|item| item.has_tag_name((WORD_NS, name)))
}

/// Read one WordprocessingML attribute from an optional element.
fn attr(node: Option<Node<'_, '_>>, name: &str) -> Option<String> {
    node.and_then(|item| item.attribute((WORD_NS, name)))
        .map(str::to_owned)
}

/// Convert one numeric Word attribute to a CSS amount with contextual errors.
fn amount(node: Option<Node<'_, '_>>, name: &str, divisor: f64) -> Result<Option<f64>> {
    attr(node, name)
        .map(|raw| {
            raw.parse::<f64>()
                .with_context(|| format!("Invalid Word {name}: {raw}"))
                .map(|number| number / divisor)
        })
        .transpose()
}

/// Read a Word boolean while preserving explicitly disabled formatting.
fn boolean(node: Option<Node<'_, '_>>) -> Option<bool> {
    node.map(|item| {
        !matches!(
            item.attribute((WORD_NS, "val"))
                .unwrap_or("true")
                .to_lowercase()
                .as_str(),
            "0" | "false" | "off" | "no"
        )
    })
}

/// Read automatic, at-least, and exact Word line spacing.
fn line_height(node: Option<Node<'_, '_>>) -> Result<Option<String>> {
    let Some(raw) = attr(node, "line") else {
        return Ok(None);
    };
    let number = raw
        .parse::<f64>()
        .with_context(|| format!("Invalid Word line spacing: {raw}"))?;
    let rule = attr(node, "lineRule")
        .unwrap_or_else(|| "auto".into())
        .to_lowercase();
    Ok(Some(if matches!(rule.as_str(), "auto" | "atleast") {
        general(number / 240.0)
    } else {
        format!("{}pt", general(number / 20.0))
    }))
}

/// Read a table's cell margin, treating Word's nil value as explicit zero.
fn cell_margin(properties: Option<Node<'_, '_>>, side: &str) -> Result<Option<f64>> {
    let margin = properties
        .and_then(|node| child(node, "tblCellMar"))
        .and_then(|node| child(node, side));
    let kind = attr(margin, "type")
        .unwrap_or_else(|| "dxa".into())
        .to_lowercase();
    match kind.as_str() {
        "nil" => Ok(Some(0.0)),
        "dxa" => amount(margin, "w", 20.0),
        _ => Ok(None),
    }
}

/// Translate a table border, preserving explicit nil/none and enabled hairlines.
fn table_border(properties: Option<Node<'_, '_>>, side: &str) -> Result<Option<String>> {
    let border = properties
        .and_then(|node| child(node, "tblBorders"))
        .and_then(|node| child(node, side));
    border_css(border)
}

/// Convert one table or conditional-cell border, preserving explicit removal.
fn border_css(border: Option<Node<'_, '_>>) -> Result<Option<String>> {
    let Some(border) = border else {
        return Ok(None);
    };
    let kind = attr(Some(border), "val").unwrap_or_else(|| "single".into());
    if matches!(kind.as_str(), "nil" | "none") {
        return Ok(Some("none".into()));
    }
    let pattern = match kind.as_str() {
        "double" | "triple" => "double",
        "dotted" => "dotted",
        "dashed" | "dashSmallGap" | "dashDotStroked" | "dotDash" | "dotDotDash" => "dashed",
        _ => "solid",
    };
    let color = attr(Some(border), "color")
        .filter(|raw| raw.len() == 6 && u32::from_str_radix(raw, 16).is_ok())
        .map(|raw| format!("#{raw}"))
        // Borders now live on cells, whose Revision Char color may be red.
        // Word's automatic border color must not inherit that character style;
        // use the document border palette while preserving explicit XML colors.
        .unwrap_or_else(|| "var(--pmt-table-border-color, #1a1a1a)".into());
    let declared_width = amount(Some(border), "sz", 8.0)?.unwrap_or(0.5);
    // Word displays sz=0 as a hairline for an enabled border, unlike CSS 0pt.
    // CSS has no device hairline unit; use the existing 0.5pt fallback as an
    // approximation, while only nil/none explicitly remove the border.
    let width = if declared_width == 0.0 {
        0.5
    } else {
        declared_width
    };
    Ok(Some(format!("{}pt {pattern} {color}", general(width))))
}

/// Read only properties directly present in one style element.
fn read_style(node: Node<'_, '_>) -> Result<StyleSpec> {
    let mut style =
        read_style_properties(child(node, "pPr"), child(node, "rPr"), child(node, "tblPr"))?;
    let header = node.children().find(|item| {
        item.has_tag_name((WORD_NS, "tblStylePr"))
            && attr(Some(*item), "type").as_deref() == Some("firstRow")
    });
    let borders = header
        .and_then(|item| child(item, "tcPr"))
        .and_then(|item| child(item, "tcBorders"));
    for (value, side) in style
        .header_borders
        .iter_mut()
        .zip(["top", "right", "bottom", "left"])
    {
        *value = border_css(borders.and_then(|item| child(item, side)))?;
    }
    Ok(style)
}

/// Read the same formatting from named styles and document-default properties.
fn read_style_properties(
    paragraph: Option<Node<'_, '_>>,
    run: Option<Node<'_, '_>>,
    table: Option<Node<'_, '_>>,
) -> Result<StyleSpec> {
    let spacing = paragraph.and_then(|item| child(item, "spacing"));
    let indent = paragraph.and_then(|item| child(item, "ind"));
    let fonts = run.and_then(|item| child(item, "rFonts"));
    let mut first_line = None;
    for (name, sign, divisor, unit) in [
        ("firstLineChars", "", 100.0, "em"),
        ("hangingChars", "-", 100.0, "em"),
        ("firstLine", "", 20.0, "pt"),
        ("hanging", "-", 20.0, "pt"),
    ] {
        if let Some(value) = amount(indent, name, divisor)? {
            first_line = Some(format!("{sign}{}{unit}", general(value)));
            break;
        }
    }
    let color = attr(run.and_then(|item| child(item, "color")), "val")
        .filter(|raw| raw.len() == 6)
        .and_then(|raw| u32::from_str_radix(&raw, 16).ok())
        .map(|number| [(number >> 16) as u8, (number >> 8) as u8, number as u8]);
    Ok(StyleSpec {
        font_size: amount(run.and_then(|item| child(item, "sz")), "val", 2.0)?,
        western_font: attr(fonts, "ascii").or_else(|| attr(fonts, "hAnsi")),
        chinese_font: attr(fonts, "eastAsia"),
        bold: boolean(run.and_then(|item| child(item, "b"))),
        italic: boolean(run.and_then(|item| child(item, "i"))),
        color,
        line_height: line_height(spacing)?,
        before: amount(spacing, "before", 20.0)?,
        after: amount(spacing, "after", 20.0)?,
        alignment: attr(paragraph.and_then(|item| child(item, "jc")), "val"),
        first_line,
        left: amount(indent, "left", 20.0)?,
        right: amount(indent, "right", 20.0)?,
        cell_top: cell_margin(table, "top")?,
        cell_right: cell_margin(table, "right")?,
        cell_bottom: cell_margin(table, "bottom")?,
        cell_left: cell_margin(table, "left")?,
        borders: [
            table_border(table, "top")?,
            table_border(table, "right")?,
            table_border(table, "bottom")?,
            table_border(table, "left")?,
            table_border(table, "insideH")?,
            table_border(table, "insideV")?,
        ],
        header_borders: Default::default(),
    })
}

/// Resolve basedOn inheritance and tolerate malformed cycles as the old reader did.
fn resolve_style<'a, 'input>(
    id: &str,
    nodes: &HashMap<String, Node<'a, 'input>>,
    defaults: &StyleSpec,
    overrides: &HashMap<String, (String, StyleSpec)>,
    resolved: &mut HashMap<String, StyleSpec>,
    stack: &mut HashSet<String>,
) -> Result<StyleSpec> {
    if let Some(style) = resolved.get(id) {
        return Ok(style.clone());
    }
    let Some(node) = nodes.get(id) else {
        return Ok(defaults.clone());
    };
    if !stack.insert(id.to_owned()) {
        return Ok(defaults.clone());
    }
    let parent = attr(child(*node, "basedOn"), "val");
    let mut style = if let Some(parent) = parent {
        resolve_style(&parent, nodes, defaults, overrides, resolved, stack)?
    } else if attr(Some(*node), "type").as_deref() == Some("paragraph") {
        defaults.clone()
    } else {
        // Paragraph docDefaults must not introduce paragraph margins into tables.
        StyleSpec::default()
    };
    let mut declared = read_style(*node)?;
    // Configuration changes the named style before basedOn inheritance, as in
    // Word. Child XML properties must still override configured parent values.
    if let Some((_, configured)) = overrides.get(id) {
        declared.overlay(configured.clone());
    }
    style.overlay(declared);
    stack.remove(id);
    resolved.insert(id.to_owned(), style.clone());
    Ok(style)
}

/// Load paragraph defaults and effective named styles from reference styles.xml.
pub(super) fn read_reference_styles(xml: &str) -> Result<ReferenceStyles> {
    read_reference_styles_with_overrides(xml, &[])
}

/// Apply configured named-style changes before resolving descendants' effective values.
pub(super) fn read_reference_styles_with_overrides(
    xml: &str,
    records: &[Map<String, Value>],
) -> Result<ReferenceStyles> {
    let document = Document::parse(xml).context("Cannot parse reference Word styles")?;
    let root = document.root_element();
    let defaults_node = child(root, "docDefaults");
    let paragraph = defaults_node
        .and_then(|item| child(item, "pPrDefault"))
        .and_then(|item| child(item, "pPr"));
    let run = defaults_node
        .and_then(|item| child(item, "rPrDefault"))
        .and_then(|item| child(item, "rPr"));
    let defaults = read_style_properties(paragraph, run, None)?;
    let mut nodes = HashMap::new();
    let mut ordered = Vec::new();
    for node in root
        .children()
        .filter(|item| item.has_tag_name((WORD_NS, "style")))
    {
        if let (Some(id), Some(name)) = (
            attr(Some(node), "styleId"),
            attr(child(node, "name"), "val"),
        ) {
            let kind = match attr(Some(node), "type").as_deref() {
                Some("paragraph") => StyleKind::Paragraph,
                Some("character") => StyleKind::Character,
                Some("table") => StyleKind::Table,
                _ => continue,
            };
            ordered.push((id.clone(), name, kind));
            nodes.insert(id, node);
        }
    }
    let mut overrides: HashMap<String, (String, StyleSpec)> = HashMap::new();
    for record in records {
        let Some(name) = record.get("style_name").and_then(Value::as_str) else {
            continue;
        };
        let candidates = papper_core::style_values::candidate_style_names_for(name);
        let matched = candidates
            .iter()
            .find_map(|candidate| {
                ordered
                    .iter()
                    .find(|(_, actual, _)| actual.eq_ignore_ascii_case(candidate))
            })
            .or_else(|| {
                candidates.iter().find_map(|candidate| {
                    ordered.iter().find(|(_, actual, _)| {
                        let key = style_key(candidate);
                        let actual = style_key(actual);
                        actual == key || actual == alias(&key)
                    })
                })
            })
            .or_else(|| ordered.iter().find(|(id, _, _)| id == name));
        if let Some((id, _, _)) = matched {
            let configured = normalized_style_spec(record);
            overrides
                .entry(id.clone())
                .and_modify(|(source, style)| {
                    *source = name.to_owned();
                    style.overlay(configured.clone());
                })
                .or_insert((name.to_owned(), configured));
        }
    }
    let mut resolved = HashMap::new();
    for (id, _, _) in &ordered {
        resolve_style(
            id,
            &nodes,
            &defaults,
            &overrides,
            &mut resolved,
            &mut HashSet::new(),
        )?;
    }
    let ids = ordered
        .iter()
        .map(|(id, name, kind)| (id.clone(), (*kind, name.clone())))
        .collect();
    let named = ordered
        .into_iter()
        .map(|(id, name, kind)| {
            let parent = attr(child(nodes[&id], "basedOn"), "val");
            let effective = resolved[&id].clone();
            (
                (kind, name),
                ReferenceStyle {
                    configured_by: overrides.get(&id).map(|(name, _)| name.clone()),
                    id,
                    parent,
                    effective,
                },
            )
        })
        .collect();
    Ok(ReferenceStyles {
        named,
        ids,
        defaults,
    })
}

/// Normalize case and whitespace for localized built-in Word style lookup.
pub(super) fn style_key(name: &str) -> String {
    name.chars()
        .filter(|item| !item.is_whitespace())
        .flat_map(char::to_lowercase)
        .collect()
}

/// Return the English/Chinese semantic counterpart of one built-in style.
pub(super) fn alias(name: &str) -> String {
    match name {
        "title" => "标题",
        "标题" => "title",
        "subtitle" => "副标题",
        "副标题" => "subtitle",
        "bodytext" => "正文文本",
        "正文文本" => "bodytext",
        "normal" => "正文",
        "正文" => "normal",
        "caption" => "题注",
        "题注" => "caption",
        "tablecaption" => "表格题注",
        "表格题注" => "tablecaption",
        "imagecaption" => "图片题注",
        "图片题注" => "imagecaption",
        "tabletext" => "表格文字",
        "表格文字" => "tabletext",
        "table" => "表格",
        "表格" => "table",
        "firstparagraph" => "首段",
        "首段" => "firstparagraph",
        _ => {
            return if let Some(level) = name.strip_prefix("heading") {
                format!("标题{level}")
            } else if let Some(level) = name.strip_prefix("标题") {
                format!("heading{level}")
            } else {
                name.to_owned()
            };
        }
    }
    .to_owned()
}

/// Find a style by its normalized English or Chinese built-in name.
pub(super) fn find_style<'a>(styles: &'a ReferenceStyles, name: &str) -> Option<&'a StyleSpec> {
    let kind = if style_key(name) == "table" || style_key(name) == "表格" {
        StyleKind::Table
    } else {
        StyleKind::Paragraph
    };
    find_typed_style(styles, kind, name)
}

/// Resolve display names within their style type, including built-in localized aliases.
pub(super) fn find_typed_style<'a>(
    styles: &'a ReferenceStyles,
    kind: StyleKind,
    name: &str,
) -> Option<&'a StyleSpec> {
    find_style_definition(styles, kind, name).map(|style| &style.effective)
}

/// Look up ancestry using the same exact-name and localized fallback rules as CSS values.
pub(super) fn find_style_definition<'a>(
    styles: &'a ReferenceStyles,
    kind: StyleKind,
    name: &str,
) -> Option<&'a ReferenceStyle> {
    if let Some(style) = styles.named.get(&(kind, name.to_owned())) {
        return Some(style);
    }
    let key = style_key(name);
    let alias = alias(&key);
    styles
        .named
        .iter()
        .filter(|((candidate_kind, _), _)| *candidate_kind == kind)
        .filter(|((_, candidate), _)| {
            let normalized = style_key(candidate);
            normalized == key || normalized == alias
        })
        // Preserve deterministic fallback when custom names differ only in case
        // or whitespace; exact display-name matches always take precedence.
        .min_by_key(|((_, candidate), _)| candidate.as_str())
        .map(|(_, style)| style)
}
