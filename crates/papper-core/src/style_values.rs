//! Validate typography metadata independently of HTML and DOCX backends.

use anyhow::{Result, bail};
use regex::Regex;
use serde::{Deserialize, Serialize};
use serde_json::{Map, Value, json};
use std::collections::BTreeMap;
use std::sync::OnceLock;

use crate::metadata::PmtSettings;

/// Store layout lengths in integer EMUs, retaining Word's truncation precision.
#[derive(Clone, Copy, Debug, Eq, PartialEq, Serialize, Deserialize)]
#[serde(transparent)]
pub struct LayoutLength(pub i64);

impl LayoutLength {
    /// Quantize a supported physical unit exactly once at the layout boundary.
    pub fn from_unit(amount: f64, unit: &str) -> Self {
        let factor = match unit {
            "cm" => 360000.0,
            "mm" => 36000.0,
            "in" => 914400.0,
            _ => 12700.0,
        };
        Self((amount * factor) as i64)
    }

    /// Return a point value for CSS and platform-independent formatting.
    pub fn pt(self) -> f64 {
        self.0 as f64 / 12700.0
    }
}

/// Keep the unit and display form alongside a normalized line-spacing value.
#[derive(Clone, Debug, PartialEq)]
pub struct LineSpacing {
    pub kind: &'static str,
    pub value: Value,
    pub display: String,
}

/// Return the first present alias, including an explicitly configured null.
pub fn first_present<'a>(mapping: &'a Map<String, Value>, keys: &[&str]) -> Option<&'a Value> {
    keys.iter().find_map(|key| mapping.get(*key))
}

/// Render YAML-like scalar values for user-facing formatting descriptions.
pub fn display_value(value: &Value) -> String {
    match value {
        Value::String(value) => value.clone(),
        Value::Bool(true) => "True".to_string(),
        Value::Bool(false) => "False".to_string(),
        Value::Null => "None".to_string(),
        _ => value.to_string(),
    }
}

/// Treat numeric booleans as Python did at the shared typography boundary.
fn numeric_value(value: &Value) -> Option<f64> {
    value
        .as_f64()
        .or_else(|| value.as_bool().map(|flag| u8::from(flag) as f64))
}

/// Cache the character-count grammar used by numeric typography values.
fn number_pattern() -> &'static Regex {
    static PATTERN: OnceLock<Regex> = OnceLock::new();
    PATTERN.get_or_init(|| {
        Regex::new(r"(?i)^(-?\d+(?:\.\d+)?)\s*(chars?|characters?|ch|字符)?$")
            .expect("valid number grammar")
    })
}

/// Cache the points grammar without accepting lengths in other units.
fn points_pattern() -> &'static Regex {
    static PATTERN: OnceLock<Regex> = OnceLock::new();
    PATTERN.get_or_init(|| {
        Regex::new(r"^(-?\d+(?:\.\d+)?)\s*(pt|磅)?$").expect("valid points grammar")
    })
}

/// Cache the common physical-length grammar shared with page margins.
fn length_pattern() -> &'static Regex {
    static PATTERN: OnceLock<Regex> = OnceLock::new();
    PATTERN.get_or_init(|| {
        Regex::new(r"^(-?\d+(?:\.\d+)?)\s*(pt|磅|cm|厘米|mm|毫米|in|inch|inches|英寸)?$")
            .expect("valid length grammar")
    })
}

/// Parse a number or character-count value, preserving existing accepted units.
pub fn parse_number(value: &Value, field_name: &str) -> Result<f64> {
    if let Some(number) = numeric_value(value) {
        return Ok(number);
    }
    if let Some(raw) = value.as_str()
        && let Some(captures) = number_pattern().captures(raw.trim())
    {
        return Ok(captures[1].parse()?);
    }
    bail!(
        "{field_name} must be a number, got: {}",
        display_value(value)
    )
}

/// Parse a point-valued YAML scalar without silently converting centimeters.
pub fn parse_points(value: &Value, field_name: &str) -> Result<f64> {
    if let Some(number) = numeric_value(value) {
        return Ok(number);
    }
    if let Some(raw) = value.as_str()
        && let Some(captures) = points_pattern().captures(&raw.trim().to_lowercase())
    {
        return Ok(captures[1].parse()?);
    }
    bail!("{field_name} must use points, for example 0pt or 6pt")
}

/// Parse points or a localized Word font-size name such as 小五 or 四号.
pub fn parse_font_size_points(value: &Value, field_name: &str) -> Result<f64> {
    if let Some(raw) = value.as_str() {
        let cleaned: String = raw
            .chars()
            .filter(|character| !character.is_whitespace())
            .collect();
        let chinese = match cleaned.as_str() {
            "初号" => Some(42.0),
            "小初" | "小初号" => Some(36.0),
            "一号" => Some(26.0),
            "小一" | "小一号" => Some(24.0),
            "二号" => Some(22.0),
            "小二" | "小二号" => Some(18.0),
            "三号" => Some(16.0),
            "小三" | "小三号" => Some(15.0),
            "四号" => Some(14.0),
            "小四" | "小四号" => Some(12.0),
            "五号" => Some(10.5),
            "小五" | "小五号" => Some(9.0),
            "六号" => Some(7.5),
            "小六" | "小六号" => Some(6.5),
            "七号" => Some(5.5),
            "八号" => Some(5.0),
            _ => None,
        };
        if let Some(size) = chinese {
            return Ok(size);
        }
    }
    parse_points(value, field_name).map_err(|_| {
        anyhow::anyhow!(
            "{field_name} must be a point value or Chinese Word size such as 10.5pt, 小五, or 四号"
        )
    })
}

/// Parse signed lengths and truncate to the same EMU precision as Word.
pub fn parse_length(value: &Value, field_name: &str) -> Result<LayoutLength> {
    if let Some(number) = numeric_value(value) {
        return Ok(LayoutLength::from_unit(number, "pt"));
    }
    if let Some(raw) = value.as_str()
        && let Some(captures) = length_pattern().captures(&raw.trim().to_lowercase())
    {
        let unit = match captures
            .get(2)
            .map(|capture| capture.as_str())
            .unwrap_or("pt")
        {
            "pt" | "磅" => "pt",
            "cm" | "厘米" => "cm",
            "mm" | "毫米" => "mm",
            _ => "in",
        };
        return Ok(LayoutLength::from_unit(captures[1].parse()?, unit));
    }
    bail!("{field_name} must be a length such as 12pt, 0.5cm, or 0.2in")
}

/// Parse hex, RGB strings, and three-item RGB arrays into validated channels.
pub fn parse_font_color(value: &Value, field_name: &str) -> Result<[u8; 3]> {
    let channels: Vec<i64> = if let Some(items) = value.as_array().filter(|items| items.len() == 3)
    {
        items
            .iter()
            .map(|item| {
                if let Some(number) = numeric_value(item) {
                    Ok(number as i64)
                } else if let Some(raw) = item.as_str() {
                    Ok(raw.trim().parse()?)
                } else {
                    bail!("{field_name} RGB channels must be numbers")
                }
            })
            .collect::<Result<_>>()?
    } else if let Some(raw) = value.as_str() {
        let cleaned = raw.trim();
        let hex = cleaned.strip_prefix('#').unwrap_or(cleaned);
        if hex.len() == 6 && hex.bytes().all(|byte| byte.is_ascii_hexdigit()) {
            [0, 2, 4]
                .into_iter()
                .map(|index| i64::from_str_radix(&hex[index..index + 2], 16))
                .collect::<std::result::Result<_, _>>()?
        } else {
            static RGB: OnceLock<Regex> = OnceLock::new();
            let expression = RGB.get_or_init(|| {
                Regex::new(r"^rgb\(\s*(\d{1,3})\s*,\s*(\d{1,3})\s*,\s*(\d{1,3})\s*\)$")
                    .expect("valid RGB grammar")
            });
            let Some(captures) = expression.captures(cleaned) else {
                bail!("{field_name} must be a color such as '#1A2B3C' or rgb(26,43,60)")
            };
            (1..=3)
                .map(|index| captures[index].parse())
                .collect::<std::result::Result<_, _>>()?
        }
    } else {
        bail!(
            "{field_name} must be a color value, got: {}",
            display_value(value)
        )
    };
    if channels.iter().any(|channel| !(0..=255).contains(channel)) {
        bail!("{field_name} RGB channels must be between 0 and 255")
    }
    Ok([channels[0] as u8, channels[1] as u8, channels[2] as u8])
}

/// Render the existing compact human-readable spacing representation.
fn compact_number(number: f64) -> String {
    let rendered = format!("{number}");
    rendered.strip_suffix(".0").unwrap_or(&rendered).to_string()
}

/// Distinguish proportional line spacing from exact point-based line spacing.
pub fn parse_line_spacing(value: &Value, field_name: &str) -> Result<LineSpacing> {
    let value = value
        .as_object()
        .and_then(|mapping| {
            first_present(
                mapping,
                &[
                    "value",
                    "spacing",
                    "lineSpacing",
                    "line-spacing",
                    "line_spacing",
                ],
            )
        })
        .unwrap_or(value);
    let (kind, number) = if let Some(number) = numeric_value(value) {
        ("multiple", number)
    } else if let Some(raw) = value.as_str() {
        let cleaned = raw.trim().to_lowercase();
        let named = match cleaned.as_str() {
            "single" | "1" => Some(1.0),
            "one-half" | "onehalf" | "1.5" => Some(1.5),
            "double" | "2" => Some(2.0),
            _ => None,
        };
        if let Some(number) = named {
            ("multiple", number)
        } else if cleaned.ends_with("pt") || cleaned.ends_with('磅') {
            ("points", parse_points(&Value::String(cleaned), field_name)?)
        } else {
            (
                "multiple",
                parse_number(&Value::String(cleaned), field_name)?,
            )
        }
    } else {
        bail!(
            "{field_name} must be a number or point value, got: {}",
            display_value(value)
        )
    };
    if number <= 0.0 {
        bail!("{field_name} must be greater than 0")
    }
    Ok(LineSpacing {
        kind,
        value: if kind == "points" {
            json!(LayoutLength::from_unit(number, "pt").0)
        } else {
            json!(number)
        },
        display: format!(
            "{}{}",
            compact_number(number),
            if kind == "points" { "pt" } else { "" }
        ),
    })
}

/// Normalize alignment aliases while retaining the original normalized display.
pub fn parse_alignment(value: &Value, field_name: &str) -> Result<(String, String)> {
    let Some(raw) = value.as_str() else {
        bail!("{field_name} must be an alignment string")
    };
    let normalized = raw.trim().to_lowercase();
    let canonical = match normalized.as_str() {
        "left" => "left",
        "center" | "centre" => "center",
        "right" => "right",
        "justify" | "justified" | "both" => "justify",
        "distribute" => "distribute",
        _ => bail!(
            "{field_name} must be one of: both, center, centre, distribute, justified, justify, left, right"
        ),
    };
    Ok((canonical.to_string(), normalized))
}

/// Validate Western/Chinese font families without inventing unspecified slots.
pub fn parse_font_family(value: &Value, field_name: &str) -> Result<BTreeMap<String, String>> {
    if let Some(raw) = value.as_str() {
        if raw.trim().is_empty() {
            bail!("{field_name} must be a non-empty font name")
        }
        return Ok(BTreeMap::from([
            ("western".to_string(), raw.trim().to_string()),
            ("chinese".to_string(), raw.trim().to_string()),
        ]));
    }
    let Some(mapping) = value.as_object() else {
        bail!("{field_name} must be a font name or a mapping")
    };
    let mut families = BTreeMap::new();
    for (target, raw) in mapping {
        if target != "western" && target != "chinese" {
            bail!("{field_name} keys must be western or chinese")
        }
        let Some(family) = raw.as_str().filter(|raw| !raw.trim().is_empty()) else {
            bail!("{field_name}.{target} must be a non-empty font name")
        };
        families.insert(target.clone(), family.trim().to_string());
    }
    if families.is_empty() {
        bail!("{field_name} must set western or chinese")
    }
    Ok(families)
}

/// Return localized built-in style aliases in the existing lookup order.
pub fn candidate_style_names_for(style_name: &str) -> Vec<String> {
    let stripped = style_name.trim();
    let normalized: String = stripped
        .chars()
        .filter(|character| !character.is_whitespace())
        .collect();
    let alias = match normalized.as_str() {
        "正文" => Some("Normal".to_string()),
        "正文文本" => Some("Body Text".to_string()),
        "标题" => Some("Title".to_string()),
        "副标题" => Some("Subtitle".to_string()),
        "题注" => Some("Caption".to_string()),
        "页眉" => Some("Header".to_string()),
        "页脚" => Some("Footer".to_string()),
        "脚注文本" => Some("Footnote Text".to_string()),
        "脚注引用" => Some("Footnote Reference".to_string()),
        "尾注文本" => Some("Endnote Text".to_string()),
        "尾注引用" => Some("Endnote Reference".to_string()),
        "引用" => Some("Quote".to_string()),
        "明显引用" => Some("Intense Quote".to_string()),
        "列表段落" => Some("List Paragraph".to_string()),
        "无间隔" => Some("No Spacing".to_string()),
        _ => normalized
            .strip_prefix("标题")
            .and_then(|level| level.parse::<u8>().ok())
            .filter(|level| (1..=9).contains(level))
            .map(|level| format!("Heading {level}"))
            .or_else(|| {
                normalized
                    .strip_prefix("目录")
                    .and_then(|level| level.parse::<u8>().ok())
                    .filter(|level| (1..=9).contains(level))
                    .map(|level| format!("TOC {level}"))
            }),
    };
    let mut names = vec![style_name.to_string()];
    if let Some(alias) = alias {
        for candidate in [alias.clone(), alias.to_lowercase()] {
            if !names.contains(&candidate) {
                names.push(candidate);
            }
        }
    }
    names
}

/// Treat empty nested style blocks like the previous YAML/Python normalizer.
fn optional_mapping<'a>(
    mapping: &'a Map<String, Value>,
    aliases: &[&str],
    field_name: &str,
) -> Result<Option<&'a Map<String, Value>>> {
    match first_present(mapping, aliases) {
        None | Some(Value::Null) | Some(Value::Bool(false)) => Ok(None),
        Some(Value::String(raw)) if raw.is_empty() => Ok(None),
        Some(Value::Number(number)) if number.as_f64() == Some(0.0) => Ok(None),
        Some(Value::Array(items)) if items.is_empty() => Ok(None),
        Some(Value::Object(value)) => Ok(Some(value)),
        _ => bail!("{field_name} metadata must be a mapping"),
    }
}

/// Find a non-null outer alias before considering its nested fallback.
fn outer_or_nested<'a>(
    outer: &'a Map<String, Value>,
    aliases: &[&str],
    nested: Option<&'a Map<String, Value>>,
    nested_aliases: &[&str],
) -> Option<&'a Value> {
    first_present(outer, aliases)
        .filter(|value| !value.is_null())
        .or_else(|| {
            nested
                .and_then(|mapping| first_present(mapping, nested_aliases))
                .filter(|value| !value.is_null())
        })
}

/// Normalize a paragraph without setting any formatting absent from metadata.
pub fn normalize_paragraph_style_settings(
    raw: &Map<String, Value>,
    prefix: &str,
) -> Result<Map<String, Value>> {
    let spacing = optional_mapping(
        raw,
        &["paragraphSpacing", "paragraph-spacing", "paragraph_spacing"],
        &format!("{prefix}.paragraphSpacing"),
    )?;
    let indentation = optional_mapping(
        raw,
        &[
            "indentation",
            "indent",
            "paragraphIndent",
            "paragraph-indent",
        ],
        &format!("{prefix}.indentation"),
    )?;
    let font = optional_mapping(
        raw,
        &["font", "textStyle", "text-style", "text_style"],
        &format!("{prefix}.font"),
    )?;
    let chars = outer_or_nested(
        raw,
        &[
            "firstLineIndentChars",
            "first-line-indent-chars",
            "first_line_indent_chars",
        ],
        indentation,
        &[
            "firstLineChars",
            "first-line-chars",
            "first_line_chars",
            "firstLineIndentChars",
            "first-line-indent-chars",
        ],
    );
    let first = outer_or_nested(
        raw,
        &["firstLineIndent", "first-line-indent", "first_line_indent"],
        indentation,
        &["firstLine", "first-line", "first_line", "firstLineIndent"],
    );
    let hanging = outer_or_nested(
        raw,
        &["hangingIndent", "hanging-indent", "hanging_indent"],
        indentation,
        &["hanging", "hangingIndent", "hanging-indent"],
    );
    if chars.is_some() && (first.is_some() || hanging.is_some()) {
        bail!(
            "{prefix} cannot set character first-line indent together with length-based first-line or hanging indent"
        )
    }
    if first.is_some() && hanging.is_some() {
        bail!("{prefix} cannot set both firstLineIndent and hangingIndent")
    }
    let mut normalized = Map::new();
    if let Some(size) = outer_or_nested(
        raw,
        &["fontSize", "font-size", "font_size"],
        font,
        &["size", "fontSize", "font-size"],
    ) {
        let points = parse_font_size_points(size, &format!("{prefix}.fontSize"))?;
        if points <= 0.0 {
            bail!("{prefix}.fontSize must be greater than 0")
        }
        normalized.insert("font_size_pt".to_string(), json!(points));
    }
    if let Some(family) = outer_or_nested(
        raw,
        &[
            "fontFamily",
            "font-family",
            "font_family",
            "fontName",
            "font-name",
            "font_name",
        ],
        font,
        &[
            "family",
            "name",
            "fontFamily",
            "font-family",
            "fontName",
            "font-name",
        ],
    ) {
        normalized.insert(
            "font_family".to_string(),
            json!(parse_font_family(family, &format!("{prefix}.fontFamily"))?),
        );
    }
    if let Some(bold) = outer_or_nested(
        raw,
        &["bold", "fontBold", "font-bold", "font_bold"],
        font,
        &["bold", "fontBold", "font-bold", "font_bold"],
    ) {
        let flag = bold
            .as_bool()
            .or_else(|| {
                bold.as_str()
                    .and_then(|raw| match raw.trim().to_lowercase().as_str() {
                        "true" => Some(true),
                        "false" => Some(false),
                        _ => None,
                    })
            })
            .ok_or_else(|| anyhow::anyhow!("{prefix}.bold must be true or false"))?;
        normalized.insert("bold".to_string(), json!(flag));
    }
    if let Some(color) = outer_or_nested(
        raw,
        &["fontColor", "font-color", "font_color", "color"],
        font,
        &["color", "fontColor", "font-color"],
    ) {
        normalized.insert(
            "font_color_rgb".to_string(),
            json!(parse_font_color(color, &format!("{prefix}.fontColor"))?),
        );
    }
    if let Some(line_spacing) = first_present(raw, &["lineSpacing", "line-spacing", "line_spacing"])
        .filter(|value| !value.is_null())
    {
        let parsed = parse_line_spacing(line_spacing, &format!("{prefix}.lineSpacing"))?;
        normalized.insert("line_spacing".to_string(), parsed.value);
        normalized.insert("line_spacing_display".to_string(), json!(parsed.display));
    }
    if let Some(alignment) = first_present(
        raw,
        &[
            "alignment",
            "align",
            "paragraphAlignment",
            "paragraph-alignment",
        ],
    )
    .filter(|value| !value.is_null())
    {
        let (alignment, display) = parse_alignment(alignment, &format!("{prefix}.alignment"))?;
        normalized.insert("alignment".to_string(), json!(alignment));
        normalized.insert("alignment_display".to_string(), json!(display));
    }
    if let Some(chars) = chars {
        normalized.insert(
            "first_line_indent_chars".to_string(),
            json!(parse_number(
                chars,
                &format!("{prefix}.firstLineIndentChars")
            )?),
        );
    }
    for (key, aliases, nested_aliases) in [
        (
            "left",
            &["leftIndent", "left-indent", "left_indent"][..],
            &["left", "leftIndent", "left-indent"][..],
        ),
        (
            "right",
            &["rightIndent", "right-indent", "right_indent"][..],
            &["right", "rightIndent", "right-indent"][..],
        ),
    ] {
        if let Some(value) = outer_or_nested(raw, aliases, indentation, nested_aliases) {
            normalized.insert(
                format!("{key}_indent"),
                json!(parse_length(value, &format!("{prefix}.indentation.{key}"))?.0),
            );
            normalized.insert(format!("{key}_indent_display"), json!(display_value(value)));
        }
    }
    if let Some(value) = first {
        normalized.insert(
            "first_line_indent".to_string(),
            json!(parse_length(value, &format!("{prefix}.indentation.firstLine"))?.0),
        );
        normalized.insert(
            "first_line_indent_display".to_string(),
            json!(display_value(value)),
        );
    }
    if let Some(value) = hanging {
        normalized.insert(
            "first_line_indent".to_string(),
            json!(-parse_length(value, &format!("{prefix}.indentation.hanging"))?.0),
        );
        normalized.insert(
            "hanging_indent_display".to_string(),
            json!(display_value(value)),
        );
    }
    for (side, aliases) in [
        (
            "before",
            &["before", "spaceBefore", "space-before", "space_before"][..],
        ),
        (
            "after",
            &["after", "spaceAfter", "space-after", "space_after"][..],
        ),
    ] {
        if let Some(value) = spacing
            .and_then(|mapping| first_present(mapping, aliases))
            .filter(|value| !value.is_null())
        {
            normalized.insert(
                format!("space_{side}_pt"),
                json!(parse_points(
                    value,
                    &format!("{prefix}.paragraphSpacing.{side}")
                )?),
            );
        }
    }
    Ok(normalized)
}

/// Normalize every configured DOCX style while preserving metadata order.
pub fn normalize_docx_style_settings(
    settings: &PmtSettings,
) -> Result<Option<Vec<Map<String, Value>>>> {
    let Some(style_map) = &settings.fields().docx_style else {
        return Ok(None);
    };
    let mut normalized = Vec::new();
    for (name, raw) in style_map.iter() {
        if name.trim().is_empty() {
            bail!("docxStyle style names must be non-empty strings")
        }
        let mut entry = Map::new();
        entry.insert("style_name".to_string(), json!(name));
        entry.insert(
            "candidate_style_names".to_string(),
            json!(candidate_style_names_for(name)),
        );
        entry.extend(normalize_paragraph_style_settings(
            raw,
            &format!("docxStyle.{name}"),
        )?);
        normalized.push(entry);
    }
    Ok(Some(normalized))
}

#[cfg(test)]
mod tests {
    use super::*;

    /// Preserve localized sizing, explicit false, and physical indentation in output styles.
    #[test]
    fn normalize_localized_paragraph_and_explicit_false() -> Result<()> {
        let raw = json!({"fontSize": "小五", "font": {"family": {"western": "Times New Roman", "chinese": "宋体"}, "bold": true}, "bold": false, "indentation": {"hanging": "0.5cm"}, "lineSpacing": "18pt", "paragraphSpacing": {"before": 0, "after": "6pt"}, "alignment": "centre"});
        let result =
            normalize_paragraph_style_settings(raw.as_object().unwrap(), "docxStyle.正文文本")?;
        assert_eq!(result["font_size_pt"], json!(9.0));
        assert_eq!(result["bold"], json!(false));
        assert_eq!(result["first_line_indent"], json!(-180000));
        assert_eq!(result["line_spacing"], json!(228600));
        assert_eq!(result["font_family"]["chinese"], json!("宋体"));
        assert_eq!(result["alignment"], json!("center"));
        Ok(())
    }

    /// Reject conflicting indentation before it can corrupt paragraph formatting.
    #[test]
    fn reject_conflicting_indentation() {
        let raw = json!({"firstLineIndentChars": 2, "indentation": {"hanging": "0.5cm"}});
        assert!(
            normalize_paragraph_style_settings(raw.as_object().unwrap(), "docxStyle.Body Text")
                .unwrap_err()
                .to_string()
                .contains("cannot set character first-line indent")
        );
    }
}
