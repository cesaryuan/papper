//! Parse CSS-like paragraph borders into the same native values for Word and HTML.

use anyhow::{Result, bail};
use serde::{Deserialize, Serialize};
use serde_json::{Map, Value};
use std::collections::BTreeMap;

use crate::style_values::{parse_font_color, parse_points};

/// Word's line borders; decorative page-art borders do not apply to paragraphs.
pub const LINE_STYLES: &[&str] = &[
    "single",
    "thick",
    "double",
    "dotted",
    "dashed",
    "dotDash",
    "dotDotDash",
    "triple",
    "thinThickSmallGap",
    "thickThinSmallGap",
    "thinThickThinSmallGap",
    "thinThickMediumGap",
    "thickThinMediumGap",
    "thinThickThinMediumGap",
    "thinThickLargeGap",
    "thickThinLargeGap",
    "thinThickThinLargeGap",
    "wave",
    "doubleWave",
    "dashSmallGap",
    "dashDotStroked",
    "threeDEmboss",
    "threeDEngrave",
    "outset",
    "inset",
    "none",
    "nil",
];

/// Schema order matters when writing w:pBdr; bar is retained only from references or a reset.
pub const BORDER_SIDES: [&str; 6] = ["top", "left", "bottom", "right", "between", "bar"];
/// The four outer edges affected by the common shorthand and text distance.
pub const OUTER_SIDES: [&str; 4] = ["top", "left", "bottom", "right"];

/// A complete side declaration, with width measured in eighths of a point.
#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
pub struct BorderLine {
    pub style: String,
    pub width: u8,
    pub color: String,
    /// Preserve reference-only theme/effect attributes when changing just text distance.
    #[serde(default, skip_serializing_if = "BTreeMap::is_empty")]
    pub inherited_attributes: BTreeMap<String, String>,
}

impl BorderLine {
    /// Map every Word line style to a supported CSS line pattern.
    pub fn css(&self) -> String {
        let pattern = match self.style.as_str() {
            "none" | "nil" => return "none".into(),
            "dotted" => "dotted",
            "dashed" | "dotDash" | "dotDotDash" | "dashSmallGap" | "dashDotStroked" => "dashed",
            "double" | "triple" | "doubleWave" => "double",
            style if style.starts_with("thinThick") || style.starts_with("thickThin") => "double",
            "threeDEmboss" | "outset" => "outset",
            "threeDEngrave" | "inset" => "inset",
            _ => "solid",
        };
        let color = if self.color == "auto" {
            "currentColor".into()
        } else {
            format!("#{}", self.color)
        };
        format!("{}pt {pattern} {color}", f64::from(self.width) / 8.0)
    }
}

/// Preserve unspecified sides and distances through reference/style/local inheritance.
#[derive(Clone, Debug, Default, PartialEq, Serialize, Deserialize)]
pub struct ParagraphBorders {
    pub lines: BTreeMap<String, BorderLine>,
    pub spaces: BTreeMap<String, u8>,
}

impl ParagraphBorders {
    /// Override supplied sides and distances without resetting the other inherited edges.
    pub fn overlay(&mut self, other: &Self) {
        self.lines.extend(other.lines.clone());
        self.spaces.extend(other.spaces.clone());
    }

    /// Identify visible boundaries without treating an explicit reset as a visible border.
    pub fn visible(&self) -> bool {
        self.lines
            .values()
            .any(|line| !matches!(line.style.as_str(), "none" | "nil"))
    }
}

/// Expand all/none before nested scopes merge, preventing shorthand from masking inner sides.
pub fn expand_settings(value: &Value, prefix: &str) -> Result<Map<String, Value>> {
    let mut result = Map::new();
    if let Some(raw) = value.as_str() {
        parse_line(raw, prefix)?;
        let sides = if matches!(raw.trim(), "none" | "nil") {
            &BORDER_SIDES[..5]
        } else {
            &OUTER_SIDES[..]
        };
        for side in sides {
            result.insert((*side).into(), value.clone());
        }
        if matches!(raw.trim(), "none" | "nil") {
            result.insert("space".into(), Value::from(0));
        }
        return Ok(result);
    }
    let Some(mapping) = value.as_object() else {
        bail!("{prefix} must be a border string or mapping");
    };
    if let Some(all) = mapping.get("all").filter(|value| !value.is_null()) {
        let Some(raw) = all.as_str() else {
            bail!("{prefix}.all must be a border string");
        };
        parse_line(raw, &format!("{prefix}.all"))?;
        for side in OUTER_SIDES {
            result.insert(side.into(), all.clone());
        }
    }
    for (name, value) in mapping {
        if !matches!(
            name.as_str(),
            "all" | "top" | "bottom" | "left" | "right" | "between" | "space"
        ) {
            bail!("Unknown paragraph border property `{prefix}.{name}`");
        }
        if name != "all" && !value.is_null() {
            result.insert(name.clone(), value.clone());
        }
    }
    Ok(result)
}

/// Validate shorthand sides and point distances, retaining explicit cancellation.
pub fn parse_borders(value: &Value, prefix: &str) -> Result<ParagraphBorders> {
    // A whole-group reset also clears between; all only expands to the four outer sides.
    let expanded = expand_settings(value, prefix)?;
    let mut result = ParagraphBorders::default();
    for (side, value) in expanded {
        if side == "space" {
            let points = parse_points(&value, &format!("{prefix}.space"))?;
            if !(0.0..=31.0).contains(&points) || points.fract() != 0.0 {
                bail!("{prefix}.space must be a whole number of points from 0pt to 31pt");
            }
            for side in OUTER_SIDES {
                result.spaces.insert(side.into(), points as u8);
            }
        } else {
            let Some(raw) = value.as_str() else {
                bail!("{prefix}.{side} must be a border string");
            };
            result
                .lines
                .insert(side.clone(), parse_line(raw, &format!("{prefix}.{side}"))?);
        }
    }
    if BORDER_SIDES[..5].iter().all(|side| {
        result
            .lines
            .get(*side)
            .is_some_and(|line| matches!(line.style.as_str(), "none" | "nil"))
    }) {
        // Legacy mirrored-page bar borders must not survive a whole paragraph reset.
        result
            .lines
            .insert("bar".into(), parse_line("nil", prefix)?);
    }
    Ok(result)
}

/// Parse width/style/color in any order, keeping rgb(...) as one token even with spaces.
pub fn parse_line(raw: &str, prefix: &str) -> Result<BorderLine> {
    static TOKENS: std::sync::LazyLock<regex::Regex> = std::sync::LazyLock::new(|| {
        regex::Regex::new(r"rgb\([^)]*\)|[^\s]+").expect("valid border token grammar")
    });
    let mut style = None;
    let mut width = None;
    let mut color = None;
    for token in TOKENS.find_iter(raw).map(|token| token.as_str()) {
        if token == "solid" || LINE_STYLES.contains(&token) {
            if style.is_some() {
                bail!("{prefix} contains more than one border style");
            }
            style = Some(if token == "solid" { "single" } else { token }.to_owned());
        } else if token.starts_with('#') || token.starts_with("rgb(") || token == "auto" {
            if color.is_some() {
                bail!("{prefix} contains more than one border color");
            }
            color = Some(if token == "auto" {
                "auto".into()
            } else {
                // CSS-style three-digit hex is common in border shorthand; expand it here
                // without broadening the existing fontColor configuration contract.
                let color = if let Some(hex) = token.strip_prefix('#').filter(|hex| {
                    hex.len() == 3 && hex.bytes().all(|byte| byte.is_ascii_hexdigit())
                }) {
                    format!(
                        "#{}",
                        hex.chars()
                            .flat_map(|character| [character; 2])
                            .collect::<String>()
                    )
                } else {
                    token.to_owned()
                };
                let [r, g, b] = parse_font_color(&Value::from(color), prefix)?;
                format!("{r:02X}{g:02X}{b:02X}")
            });
        } else {
            if width.is_some() {
                bail!("{prefix} contains an unexpected border token `{token}`");
            }
            let points = parse_points(&Value::from(token), prefix).map_err(|_| anyhow::anyhow!("{prefix}: unknown border token `{token}`; use a Word line style, point width or RGB color"))?;
            let eighths = points * 8.0;
            if !(2.0..=96.0).contains(&eighths) || (eighths - eighths.round()).abs() > 1e-8 {
                bail!("{prefix} width must be 0.25pt to 12pt in steps of 0.125pt");
            }
            width = Some(eighths as u8);
        }
    }
    if raw.trim().is_empty() {
        bail!("{prefix} must not be empty");
    }
    let style = style.unwrap_or_else(|| "single".into());
    if matches!(style.as_str(), "none" | "nil") && (width.is_some() || color.is_some()) {
        bail!("{prefix}: use `none` or `nil` alone to remove a border");
    }
    Ok(BorderLine {
        style,
        width: width.unwrap_or(4),
        color: color.unwrap_or_else(|| "000000".into()),
        inherited_attributes: BTreeMap::new(),
    })
}
