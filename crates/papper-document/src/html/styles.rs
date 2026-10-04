//! Translate Word style inheritance and configured paragraph overrides into CSS.

use anyhow::{Context, Result};
use roxmltree::{Document, Node};
use serde_json::{Map, Value};
use std::collections::{HashMap, HashSet};
use std::path::Path;

const WORD_NS: &str = "http://schemas.openxmlformats.org/wordprocessingml/2006/main";

// Custom-style Divs wrap body paragraphs; keep this fallback at the same
// specificity as direct paragraphs so dedicated custom-style rules can win.
const BODY_PARAGRAPH_SELECTOR: &str =
    ".pmt-page > p, .pmt-page > :where(div[data-custom-style]) > p";
// Pandoc's section headings share one page parent. First-of-type only finds
// one paragraph for the entire page, rather than the opening of each section.
// :where keeps authored Body Text/First Paragraph override order effective.
const FIRST_PARAGRAPH_SELECTOR: &str = ".pmt-page > :where(h1:not(.title), h2, h3, h4, h5, h6) + p";

/// Effective formatting declared by a Word paragraph, character, or table style.
#[derive(Clone, Default)]
struct StyleSpec {
    font_size: Option<f64>,
    western_font: Option<String>,
    chinese_font: Option<String>,
    bold: Option<bool>,
    italic: Option<bool>,
    color: Option<[u8; 3]>,
    line_height: Option<String>,
    before: Option<f64>,
    after: Option<f64>,
    alignment: Option<String>,
    first_line: Option<String>,
    left: Option<f64>,
    right: Option<f64>,
    cell_top: Option<f64>,
    cell_right: Option<f64>,
    cell_bottom: Option<f64>,
    cell_left: Option<f64>,
}

impl StyleSpec {
    /// Apply only explicitly declared child properties, including false and zero.
    fn overlay(&mut self, child: Self) {
        macro_rules! copy {
            ($($field:ident),*) => {$(if child.$field.is_some() {self.$field = child.$field;})*};
        }
        copy!(
            font_size,
            western_font,
            chinese_font,
            bold,
            italic,
            color,
            line_height,
            before,
            after,
            alignment,
            first_line,
            left,
            right,
            cell_top,
            cell_right,
            cell_bottom,
            cell_left
        );
    }
}

/// Render six significant digits, matching Python's general number formatting.
pub(super) fn general(value: f64) -> String {
    if !value.is_finite() {
        return value.to_string().to_lowercase();
    }
    if value == 0.0 {
        return if value.is_sign_negative() { "-0" } else { "0" }.into();
    }
    // Decide notation after rounding, since 999999.9 rounds into exponent six.
    let scientific = format!("{value:.5e}");
    let (mantissa, exponent) = scientific.split_once('e').expect("scientific float");
    let exponent = exponent.parse::<i32>().expect("scientific exponent");
    if !(-4..6).contains(&exponent) {
        let part = mantissa
            .trim_end_matches('0')
            .trim_end_matches('.')
            .to_owned();
        return format!(
            "{part}e{}{:02}",
            if exponent < 0 { "-" } else { "+" },
            exponent.abs()
        );
    }
    let precision = (5 - exponent).max(0) as usize;
    let rendered = format!("{value:.precision$}");
    if rendered.contains('.') {
        rendered.trim_end_matches('0').trim_end_matches('.').into()
    } else {
        rendered
    }
}

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

/// Read only properties directly present in one style element.
fn read_style(node: Node<'_, '_>) -> Result<StyleSpec> {
    let paragraph = child(node, "pPr");
    let run = child(node, "rPr");
    let table = child(node, "tblPr");
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
    })
}

/// Resolve basedOn inheritance and tolerate malformed cycles as the old reader did.
fn resolve_style<'a, 'input>(
    id: &str,
    nodes: &HashMap<String, Node<'a, 'input>>,
    defaults: &StyleSpec,
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
        resolve_style(&parent, nodes, defaults, resolved, stack)?
    } else if attr(Some(*node), "type").as_deref() == Some("paragraph") {
        defaults.clone()
    } else {
        // Paragraph docDefaults must not introduce paragraph margins into tables.
        StyleSpec::default()
    };
    style.overlay(read_style(*node)?);
    resolved.insert(id.to_owned(), style.clone());
    Ok(style)
}

/// Load paragraph defaults and effective named styles from reference styles.xml.
fn read_reference_styles(xml: &str) -> Result<HashMap<String, StyleSpec>> {
    let document = Document::parse(xml).context("Cannot parse reference Word styles")?;
    let root = document.root_element();
    let defaults_node = child(root, "docDefaults");
    let paragraph = defaults_node
        .and_then(|item| child(item, "pPrDefault"))
        .and_then(|item| child(item, "pPr"));
    let run = defaults_node
        .and_then(|item| child(item, "rPrDefault"))
        .and_then(|item| child(item, "rPr"));
    let spacing = paragraph.and_then(|item| child(item, "spacing"));
    let fonts = run.and_then(|item| child(item, "rFonts"));
    let defaults = StyleSpec {
        font_size: amount(run.and_then(|item| child(item, "sz")), "val", 2.0)?,
        western_font: attr(fonts, "ascii").or_else(|| attr(fonts, "hAnsi")),
        chinese_font: attr(fonts, "eastAsia"),
        line_height: line_height(spacing)?,
        before: amount(spacing, "before", 20.0)?,
        after: amount(spacing, "after", 20.0)?,
        ..StyleSpec::default()
    };
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
            ordered.push((id.clone(), name));
            nodes.insert(id, node);
        }
    }
    let mut resolved = HashMap::new();
    for (id, _) in &ordered {
        resolve_style(id, &nodes, &defaults, &mut resolved, &mut HashSet::new())?;
    }
    Ok(ordered
        .into_iter()
        .map(|(id, name)| (style_key(&name), resolved[&id].clone()))
        .collect())
}

/// Normalize case and whitespace for localized built-in Word style lookup.
fn style_key(name: &str) -> String {
    name.chars()
        .filter(|item| !item.is_whitespace())
        .flat_map(char::to_lowercase)
        .collect()
}

/// Return the English/Chinese semantic counterpart of one built-in style.
fn alias(name: &str) -> String {
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
fn find_style<'a>(styles: &'a HashMap<String, StyleSpec>, name: &str) -> Option<&'a StyleSpec> {
    let key = style_key(name);
    styles.get(&key).or_else(|| styles.get(&alias(&key)))
}

/// Escape one CSS font name without changing Unicode glyph names.
fn quote_font(name: &str) -> String {
    format!("\"{}\"", name.replace('\\', "\\\\").replace('"', "\\\""))
}

/// Build a font family with the same East Asian and serif fallbacks as Python.
fn font_css(style: &StyleSpec) -> Option<String> {
    let mut families = Vec::new();
    for family in [&style.western_font, &style.chinese_font]
        .into_iter()
        .flatten()
    {
        if !families.contains(family) {
            families.push(family.clone());
        }
    }
    if families.is_empty() {
        return None;
    }
    let mut values: Vec<String> = families.iter().map(|name| quote_font(name)).collect();
    if style.chinese_font.is_some() {
        values.push("SimSun".into());
    }
    values.push("serif".into());
    Some(values.join(", "))
}

/// Render formatting in the stable declaration order of the reference implementation.
fn style_css(selector: &str, style: &StyleSpec, label: &str, paragraph_metrics: bool) -> String {
    let points = |value: Option<f64>| value.map(|item| format!("{}pt", general(item)));
    let alignment = style.alignment.as_ref().map(|raw| {
        if matches!(raw.as_str(), "both" | "distribute") {
            "justify".into()
        } else {
            raw.clone()
        }
    });
    let values = [
        ("font-size", points(style.font_size)),
        ("font-family", font_css(style)),
        (
            "font-weight",
            style
                .bold
                .map(|item| if item { "bold" } else { "normal" }.into()),
        ),
        (
            "font-style",
            style
                .italic
                .map(|item| if item { "italic" } else { "normal" }.into()),
        ),
        (
            "color",
            style
                .color
                .map(|item| format!("#{:02X}{:02X}{:02X}", item[0], item[1], item[2])),
        ),
        ("line-height", style.line_height.clone()),
        (
            "margin-top",
            if paragraph_metrics {
                points(style.before)
            } else {
                None
            },
        ),
        (
            "margin-bottom",
            if paragraph_metrics {
                points(style.after)
            } else {
                None
            },
        ),
        ("text-align", alignment),
        (
            "text-indent",
            if paragraph_metrics {
                style.first_line.clone()
            } else {
                None
            },
        ),
        (
            "margin-left",
            if paragraph_metrics {
                points(style.left)
            } else {
                None
            },
        ),
        (
            "margin-right",
            if paragraph_metrics {
                points(style.right)
            } else {
                None
            },
        ),
    ];
    let mut declarations = vec![format!(
        "  /* {} from reference-doc/word/styles.xml */",
        label.replace("*/", "* /")
    )];
    declarations.extend(
        values
            .into_iter()
            .filter_map(|(key, value)| value.map(|value| format!("  {key}: {value};"))),
    );
    format!("{selector} {{\n{}\n}}", declarations.join("\n"))
}

/// Combine table cell margins with paragraph spacing as inherited CSS variables.
fn table_padding_css(table: Option<&StyleSpec>, paragraph: &StyleSpec, label: &str) -> String {
    let default = StyleSpec::default();
    let table = table.unwrap_or(&default);
    let values = [
        ("--pmt-table-cell-margin-top", table.cell_top),
        ("--pmt-table-cell-margin-right", table.cell_right),
        ("--pmt-table-cell-margin-bottom", table.cell_bottom),
        ("--pmt-table-cell-margin-left", table.cell_left),
        ("--pmt-table-text-before", paragraph.before),
        ("--pmt-table-text-after", paragraph.after),
    ];
    let mut variables = vec![format!(
        "  /* {label} cell margins and paragraph spacing from reference-doc/word/styles.xml */"
    )];
    variables.extend(
        values
            .into_iter()
            .map(|(key, value)| format!("  {key}: {}pt;", general(value.unwrap_or(0.0)))),
    );
    format!(
        "table {{\n{}\n}}\n\ntable td, table th {{\n  /* Combine reference cell margins, Table Text spacing, and per-table overrides. */\n  padding-top: calc(var(--pmt-table-cell-margin-top, 0pt) + var(--pmt-table-text-before, 0pt));\n  padding-right: var(--pmt-table-cell-margin-right, 0pt);\n  padding-bottom: calc(var(--pmt-table-cell-margin-bottom, 0pt) + var(--pmt-table-text-after, 0pt));\n  padding-left: var(--pmt-table-cell-margin-left, 0pt);\n}}",
        variables.join("\n")
    )
}

/// Map configured built-in styles to their semantic HTML selectors.
fn override_selector(name: &str) -> Option<String> {
    let key = style_key(name);
    // The existing override map supports only these localized names. Broader
    // reference-style aliases must not silently activate previously ignored CSS.
    let key = if matches!(
        key.as_str(),
        "正文" | "正文文本" | "首段" | "题注" | "标题" | "副标题"
    ) || key
        .strip_prefix("标题")
        .is_some_and(|level| matches!(level, "1" | "2" | "3" | "4" | "5" | "6" | "7" | "8" | "9"))
    {
        alias(&key)
    } else {
        key
    };
    match key.as_str() {
        "normal" | "bodytext" => Some(BODY_PARAGRAPH_SELECTOR.into()),
        "firstparagraph" => Some(FIRST_PARAGRAPH_SELECTOR.into()),
        "caption" => Some("figure figcaption, table caption".into()),
        "tablecaption" => Some("table caption".into()),
        "imagecaption" => Some("figure figcaption".into()),
        "tabletext" => Some("table td, table th, table td p, table th p".into()),
        "table" => Some("table, table td, table th".into()),
        "title" => Some("h1.title".into()),
        "subtitle" => Some("p.subtitle".into()),
        _ => key
            .strip_prefix("heading")
            .filter(|level| matches!(*level, "1" | "2" | "3" | "4" | "5" | "6" | "7" | "8" | "9"))
            .map(|level| format!("h{level}")),
    }
}

/// Convert normalized, shared paragraph records into higher-priority CSS rules.
pub fn normalized_override_css(
    records: &[Map<String, Value>],
    reference_xml: &str,
) -> Result<Vec<String>> {
    let styles = read_reference_styles(reference_xml)?;
    override_css(records, find_style(&styles, "Table"))
}

/// Render shared normalized style values, retaining quantized Word lengths.
fn override_css(records: &[Map<String, Value>], table: Option<&StyleSpec>) -> Result<Vec<String>> {
    let mut rules = Vec::new();
    for record in records {
        let Some(name) = record.get("style_name").and_then(Value::as_str) else {
            continue;
        };
        let Some(selector) = override_selector(name) else {
            continue;
        };
        let number = |name: &str| record.get(name).and_then(Value::as_f64);
        let length = |name: &str| number(name).map(|amount| amount / 12700.0);
        let family = record.get("font_family").and_then(Value::as_object);
        let color = record
            .get("font_color_rgb")
            .and_then(Value::as_array)
            .filter(|channels| channels.len() == 3)
            .map(|channels| {
                [
                    channels[0].as_u64().unwrap_or(0) as u8,
                    channels[1].as_u64().unwrap_or(0) as u8,
                    channels[2].as_u64().unwrap_or(0) as u8,
                ]
            });
        let style = StyleSpec {
            font_size: number("font_size_pt"),
            western_font: family
                .and_then(|item| item.get("western"))
                .and_then(Value::as_str)
                .map(str::to_owned),
            chinese_font: family
                .and_then(|item| item.get("chinese"))
                .and_then(Value::as_str)
                .map(str::to_owned),
            bold: record.get("bold").and_then(Value::as_bool),
            color,
            // LayoutLength historically subclasses int, so the HTML renderer
            // emits its point count as a unitless multiple. Keep output stable.
            line_height: record
                .get("line_spacing")
                .and_then(Value::as_f64)
                .map(|value| {
                    if record
                        .get("line_spacing")
                        .is_some_and(|value| value.is_i64() || value.is_u64())
                    {
                        general(value / 12700.0)
                    } else {
                        general(value)
                    }
                }),
            before: number("space_before_pt"),
            after: number("space_after_pt"),
            alignment: record
                .get("alignment_display")
                .and_then(Value::as_str)
                .map(str::to_owned),
            first_line: number("first_line_indent_chars")
                .map(|value| format!("{}em", general(value)))
                .or_else(|| {
                    number("first_line_indent").map(|value| {
                        // Unary negation historically drops LayoutLength's .pt;
                        // hanging overrides therefore render EMUs as plain points.
                        let points = if record.contains_key("hanging_indent_display") {
                            value
                        } else {
                            value / 12700.0
                        };
                        format!("{}pt", general(points))
                    })
                }),
            left: length("left_indent"),
            right: length("right_indent"),
            ..StyleSpec::default()
        };
        let label = format!("docxStyle.{name} override");
        if style_key(name) == "tabletext" {
            rules.push(style_css("table td, table th", &style, &label, false));
            rules.push(style_css(
                "table td p, table th p",
                &style,
                &format!("{label} paragraphs"),
                true,
            ));
            rules.push(table_padding_css(table, &style, &label));
        } else {
            rules.push(style_css(&selector, &style, &label, true));
        }
    }
    Ok(rules)
}

/// Generate CSS from reference styles and raw docxStyle metadata.
pub fn build_reference_style_css(styles_path: &Path, docx_style: Option<&Value>) -> Result<String> {
    let xml = std::fs::read_to_string(styles_path)
        .with_context(|| format!("Cannot read {}", styles_path.display()))?;
    build_reference_style_css_text(&xml, docx_style)
}

/// Generate CSS in memory; callers may cache XML bytes after dependency validation.
pub fn build_reference_style_css_text(xml: &str, docx_style: Option<&Value>) -> Result<String> {
    let records = docx_style
        .filter(|value| !value.is_null())
        .map(normalize_docx_styles)
        .transpose()?
        .unwrap_or_default();
    reference_style_css_text(xml, &records)
}

/// Generate product CSS directly from validated configuration, preserving authored override order.
pub fn build_reference_style_css_with_settings(
    styles_path: &Path,
    settings: &papper_core::metadata::PmtSettings,
) -> Result<String> {
    let xml = std::fs::read_to_string(styles_path)
        .with_context(|| format!("Cannot read {}", styles_path.display()))?;
    let records =
        papper_core::style_values::normalize_docx_style_settings(settings)?.unwrap_or_default();
    reference_style_css_text(&xml, &records)
}

/// Combine inherited reference typography with already normalized project overrides.
fn reference_style_css_text(xml: &str, records: &[Map<String, Value>]) -> Result<String> {
    let styles = read_reference_styles(xml)?;
    let mut rules = Vec::new();
    let default = StyleSpec::default();
    rules.push(style_css(
        ".pmt-page",
        find_style(&styles, "Body Text").unwrap_or(&default),
        "Body Text inherited",
        false,
    ));
    let mut mappings = vec![
        (
            "h1.title".to_owned(),
            "title".to_owned(),
            "Title".to_owned(),
        ),
        ("p.subtitle".into(), "subtitle".into(), "Subtitle".into()),
        (
            BODY_PARAGRAPH_SELECTOR.into(),
            "body text".into(),
            "Body Text".into(),
        ),
        (
            FIRST_PARAGRAPH_SELECTOR.into(),
            "first paragraph".into(),
            "First Paragraph".into(),
        ),
    ];
    for level in 1..=6 {
        mappings.push((
            format!("h{level}"),
            format!("heading {level}"),
            format!("Heading {level}"),
        ));
    }
    mappings.extend(
        [
            ("figure figcaption, table caption", "caption", "Caption"),
            ("table caption", "table caption", "Table Caption"),
            ("figure figcaption", "image caption", "Image Caption"),
            ("table, table td, table th", "table", "Table"),
            (
                "table td, table th, table td p, table th p",
                "table text",
                "Table Text",
            ),
        ]
        .map(|(selector, key, label)| (selector.into(), key.into(), label.into())),
    );
    for (selector, key, label) in mappings {
        if let Some(style) = find_style(&styles, &key) {
            if key == "table text" {
                rules.push(style_css("table td, table th", style, &label, false));
                rules.push(style_css(
                    "table td p, table th p",
                    style,
                    &format!("{label} paragraphs"),
                    true,
                ));
                rules.push(table_padding_css(
                    find_style(&styles, "Table"),
                    style,
                    &label,
                ));
            } else {
                rules.push(style_css(&selector, style, &label, true));
            }
        }
    }
    rules.extend(override_css(records, find_style(&styles, "Table"))?);
    Ok(rules.join("\n\n"))
}

/// Delegate metadata validation to the shared Rust configuration implementation.
fn normalize_docx_styles(raw: &Value) -> Result<Vec<Map<String, Value>>> {
    let mapping = Map::from_iter([("docxStyle".into(), raw.clone())]);
    let settings = papper_core::metadata::PmtSettings::from_mapping(&mapping)?;
    Ok(papper_core::style_values::normalize_docx_style_settings(&settings)?.unwrap_or_default())
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    /// Keep inheritance, explicit false/zero, localized names, and override priority.
    #[test]
    fn localized_style_overrides_follow_reference_inheritance() -> Result<()> {
        let xml = r#"<w:styles xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
          <w:docDefaults><w:pPrDefault><w:pPr><w:spacing w:after="120"/></w:pPr></w:pPrDefault></w:docDefaults>
          <w:style w:type="paragraph" w:styleId="Body"><w:name w:val="正文文本"/><w:rPr><w:sz w:val="24"/><w:b/></w:rPr></w:style>
          <w:style w:type="paragraph" w:styleId="Heading"><w:name w:val="标题 1"/><w:basedOn w:val="Body"/><w:rPr><w:sz w:val="30"/><w:b w:val="false"/></w:rPr></w:style>
          <w:style w:type="table" w:styleId="Table"><w:name w:val="Table"/></w:style>
        </w:styles>"#;
        let css = build_reference_style_css_text(
            xml,
            Some(&json!({"标题 1": {"fontSize": "四号", "paragraphSpacing": {"after": 0}}})),
        )?;
        let inherited = "h1 {\n  /* Heading 1 from reference-doc/word/styles.xml */\n  font-size: 15pt;\n  font-weight: normal;\n  margin-bottom: 6pt;\n}";
        let override_rule = "h1 {\n  /* docxStyle.标题 1 override from reference-doc/word/styles.xml */\n  font-size: 14pt;\n  margin-bottom: 0pt;\n}";
        assert!(css.contains(inherited));
        assert!(css.contains(override_rule));
        assert!(css.find(inherited).unwrap() < css.find(override_rule).unwrap());
        assert!(css.contains(
            "table, table td, table th {\n  /* Table from reference-doc/word/styles.xml */\n}"
        ));
        Ok(())
    }
}
