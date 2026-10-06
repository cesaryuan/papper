//! Translate normalized project docxStyle overrides into later CSS rules.

use super::css::*;
use super::model::*;
use super::table::*;
use super::word::*;
use super::{BODY_PARAGRAPH_SELECTOR, FIRST_PARAGRAPH_SELECTOR};
use anyhow::Result;
use serde_json::{Map, Value};

/// Share configured caption typography while keeping spacing on its outer element.
pub(super) fn caption_override_css(selector: &str, style: &StyleSpec, label: &str) -> String {
    let paragraph_selector = selector
        .split(',')
        .map(|part| format!("{} p", part.trim()))
        .collect::<Vec<_>>()
        .join(", ");
    let mut writer = CssWriter::default();
    let source = CssSource::configured(label);
    for (selector, mode) in [
        (selector.to_owned(), CssStyleMode::Paragraph),
        (paragraph_selector.clone(), CssStyleMode::CaptionParagraph),
    ] {
        let target = StyleTarget {
            id: String::new(),
            selector,
            mode,
        };
        writer.style(&source, &target, style);
    }
    // Pandoc may wrap a subfigure caption in p; outer spacing must not be doubled.
    let target = StyleTarget {
        id: String::new(),
        selector: paragraph_selector,
        mode: CssStyleMode::ParagraphReset,
    };
    writer.style(&source, &target, &StyleSpec::zero_paragraph_spacing());
    writer.finish()
}
/// Map configured built-in styles to their semantic HTML selectors.
pub(super) fn override_selector(name: &str) -> Option<String> {
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
        "tabletext" => Some("table td, table th, table td > p, table th > p".into()),
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
pub(super) fn override_css(
    records: &[Map<String, Value>],
    table: Option<&StyleSpec>,
) -> Result<Vec<String>> {
    let mut rules = Vec::new();
    for record in records {
        let Some(name) = record.get("style_name").and_then(Value::as_str) else {
            continue;
        };
        let Some(selector) = override_selector(name) else {
            continue;
        };
        let style = normalized_style_spec(record);
        let label = format!("docxStyle.{name} override");
        if style_key(name) == "tabletext" {
            rules.push(CssWriter::configured_style(
                "table td, table th",
                &style,
                name,
                false,
            ));
            rules.push(CssWriter::configured_style(
                "table td > p, table th > p",
                &style,
                name,
                true,
            ));
            rules.push(table_padding_css(table, &style, &label));
        } else {
            if selector.contains("caption") {
                rules.push(caption_override_css(&selector, &style, name));
            } else {
                rules.push(CssWriter::configured_style(&selector, &style, name, true));
            }
        }
    }
    Ok(rules)
}
/// Delegate metadata validation to the shared Rust configuration implementation.
pub(super) fn normalize_docx_styles(raw: &Value) -> Result<Vec<Map<String, Value>>> {
    let mapping = Map::from_iter([("docxStyle".into(), raw.clone())]);
    let settings = papper_core::metadata::PmtSettings::from_mapping(&mapping)?;
    Ok(papper_core::style_values::normalize_docx_style_settings(&settings)?.unwrap_or_default())
}

/// Convert validated override records once for semantic CSS and Word-style inheritance.
pub(super) fn normalized_style_spec(record: &Map<String, Value>) -> StyleSpec {
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
    StyleSpec {
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
    }
}
