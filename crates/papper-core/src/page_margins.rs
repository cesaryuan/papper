//! Validate page-margin metadata without importing a DOCX platform backend.

use anyhow::{Result, bail};
use serde_json::Value;
use std::collections::BTreeMap;

use crate::metadata::PmtSettings;
use crate::style_values::{LayoutLength, display_value, parse_length};

/// Preserve normalized EMUs and the user-supplied display text for each margin side.
pub type PageMargins = (BTreeMap<String, LayoutLength>, BTreeMap<String, String>);

/// Normalize physical margin lengths and preserve legacy numeric handling.
pub fn parse_margin_length(value: &Value, field_name: &str) -> Result<LayoutLength> {
    let length = parse_length(value, field_name)?;
    // The original shared validator checked string margins only; keep numeric
    // inputs compatible rather than changing existing configuration semantics.
    let negative_string = value.as_str().is_some_and(|raw| {
        raw.trim()
            .chars()
            .take_while(|character| character.is_ascii_digit() || matches!(character, '-' | '.'))
            .collect::<String>()
            .parse::<f64>()
            .is_ok_and(|amount| amount < 0.0)
    });
    if negative_string {
        bail!("{field_name} must be greater than or equal to 0")
    }
    Ok(length)
}

/// Normalize only configured margin sides, including inside/outside aliases.
pub fn normalize_page_margins(settings: &PmtSettings) -> Result<Option<PageMargins>> {
    let Some(raw) = &settings.fields().docx_page_margins else {
        return Ok(None);
    };
    let mut margins = BTreeMap::new();
    let mut display_values = BTreeMap::new();
    for (side, aliases) in [
        ("top", &["top"][..]),
        ("bottom", &["bottom"][..]),
        ("left", &["left", "inside"][..]),
        ("right", &["right", "outside"][..]),
    ] {
        if let Some(value) = aliases.iter().find_map(|alias| raw.get(*alias)) {
            let value = serde_json::to_value(value)?;
            margins.insert(
                side.to_string(),
                parse_margin_length(&value, &format!("docxPageMargins.{side}"))?,
            );
            display_values.insert(side.to_string(), display_value(&value));
        }
    }
    Ok(Some((margins, display_values)))
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    /// Preserve explicitly supplied margins without filling unrelated page sides.
    #[test]
    fn physical_margin_aliases_leave_unspecified_sides_unchanged() -> Result<()> {
        let settings = PmtSettings::from_mapping(
            json!({"docxPageMargins": {"inside": "2.54cm", "outside": "1in"}})
                .as_object()
                .unwrap(),
        )?;
        let (margins, _) = normalize_page_margins(&settings)?.unwrap();
        assert_eq!(margins.len(), 2);
        assert_eq!(margins["left"].0, margins["right"].0);
        assert_eq!(margins["left"].0, 914400);
        Ok(())
    }
}
