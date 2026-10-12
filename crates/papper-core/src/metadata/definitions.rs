//! Share small configuration contracts between runtime validation and editor export.
//!
//! These definitions describe owned fields and numeric controls. Domain parsers
//! still handle coercion, physical units, typography and configuration precedence.

use anyhow::{Result, bail};
use serde_json::{Value, json};

use super::SettingField;

/// Describe preferred input shapes without rejecting historical scalar coercions.
#[derive(Clone, Copy, Debug)]
pub enum SettingShape {
    Boolean,
    String,
    NullableBoolean,
    NullableString,
    BooleanOrString,
    StringOrObject,
    NullableObject,
    NullableLength,
    PositiveNumber(PositiveNumber),
}

impl SettingShape {
    /// Supply schema types from the same nullable contract used by the runtime.
    pub fn types(self) -> &'static [&'static str] {
        match self {
            Self::Boolean => &["boolean"],
            Self::String => &["string"],
            Self::NullableBoolean => &["boolean", "null"],
            Self::NullableString => &["string", "null"],
            Self::BooleanOrString => &["boolean", "string"],
            Self::StringOrObject => &["string", "object"],
            Self::NullableObject => &["object", "null"],
            Self::NullableLength => &["string", "number", "null"],
            Self::PositiveNumber(PositiveNumber::Integer) => &["integer", "null"],
            Self::PositiveNumber(PositiveNumber::Decimal) => &["number", "null"],
        }
    }

    /// Preserve explicit clears only for nullable owned settings.
    pub fn allows_null(self) -> bool {
        self.types().contains(&"null")
    }
}

/// Distinguish pixel counts from decimal DPI/scale values at one validation boundary.
#[derive(Clone, Copy, Debug)]
pub enum PositiveNumber {
    Integer,
    Decimal,
}

impl PositiveNumber {
    /// Shared exclusive lower bound for rasterization controls.
    pub const MINIMUM: f64 = 0.0;

    /// Normalize legacy boolean and numeric-string input while rejecting invalid controls.
    pub fn normalize(self, value: &Value, name: &str) -> Result<Value> {
        let number = value
            .as_f64()
            .or_else(|| value.as_bool().map(|flag| u8::from(flag) as f64))
            .or_else(|| value.as_str().and_then(|raw| raw.trim().parse().ok()))
            .ok_or_else(|| anyhow::anyhow!("{name} must be a number"))?;
        if !number.is_finite() || number <= Self::MINIMUM {
            bail!("{name} must be greater than {}", Self::MINIMUM)
        }
        match self {
            Self::Integer if number.fract() != 0.0 || number > i64::MAX as f64 => {
                bail!("{name} must be an integer")
            }
            Self::Integer => Ok(json!(number as i64)),
            Self::Decimal => Ok(json!(number)),
        }
    }
}

impl SettingField {
    /// Define preferred field types and nullable semantics once for parsing and export.
    pub fn shape(self) -> SettingShape {
        use SettingShape::*;
        match self {
            Self::Mathtype
            | Self::DocxNativeCrossref
            | Self::DocxEmbedSvgImages
            | Self::DocxConvertSvgToPng => Boolean,
            Self::MathtypeConversionMethod | Self::MathtypeSvgBackend | Self::TableAutofit => {
                String
            }
            Self::MathtypeTypstMathFont => StringOrObject,
            Self::DocxSvgToPngWidth => PositiveNumber(self::PositiveNumber::Integer),
            Self::DocxSvgToPngDpi | Self::DocxSvgToPngScale => {
                PositiveNumber(self::PositiveNumber::Decimal)
            }
            Self::CitationNumberRangeDelimiter => NullableString,
            Self::DocxShowLineNumbers => BooleanOrString,
            Self::DocxShowPageNumbers => NullableBoolean,
            Self::DocxFooter | Self::DocxHeader | Self::DocxPageMargins | Self::DocxStyle => {
                NullableObject
            }
            Self::DocxPageWidth => NullableLength,
        }
    }
}

/// Mutually exclusive sizing controls, ordered as in the established runtime diagnostic.
pub const SVG_SIZING_FIELDS: [SettingField; 3] = [
    SettingField::DocxSvgToPngWidth,
    SettingField::DocxSvgToPngScale,
    SettingField::DocxSvgToPngDpi,
];
