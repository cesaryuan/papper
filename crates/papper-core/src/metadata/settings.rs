//! Typed Papper settings with legacy JSON confined to configuration boundaries.

use anyhow::{Result, bail};
use serde::de::{MapAccess, Visitor};
use serde::ser::SerializeMap;
use serde::{Deserialize, Deserializer, Serialize, Serializer};
use serde_json::{Map, Number, Value, json};
use std::collections::{BTreeMap, BTreeSet};

use super::{PmtSettings, canonical_field_name};

/// Keep effective primary and calligraphic fonts explicit, regardless of input shorthand.
#[derive(Clone, Debug, Eq, PartialEq, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct MathFontConfig {
    pub font: String,
    pub calligraphic_font: String,
}

impl Default for MathFontConfig {
    /// Describe the complete bundled default instead of hiding a rendering exception.
    fn default() -> Self {
        Self {
            font: "XITS Math".into(),
            calligraphic_font: "New Computer Modern Math".into(),
        }
    }
}

impl MathFontConfig {
    /// Normalize string shorthand and reject incomplete or misspelled object fields.
    pub fn from_value(value: &Value) -> Result<Self> {
        /// Validate each font independently so errors identify the affected configuration field.
        fn name(value: Option<&Value>, field: &str) -> Result<String> {
            let Some(value) = value.and_then(Value::as_str) else {
                bail!("{field} must be a font name or font file path string")
            };
            if value.trim().is_empty() {
                bail!("{field} must not be blank")
            }
            Ok(value.trim().into())
        }
        match value {
            Value::String(_) => {
                let font = name(Some(value), "mathtypeTypstMathFont")?;
                Ok(Self {
                    calligraphic_font: font.clone(),
                    font,
                })
            }
            Value::Object(fields) => {
                for field in fields.keys() {
                    if !matches!(field.as_str(), "font" | "calligraphicFont") {
                        bail!("Unknown mathtypeTypstMathFont field: {field}")
                    }
                }
                Ok(Self {
                    font: name(fields.get("font"), "mathtypeTypstMathFont.font")?,
                    calligraphic_font: name(
                        fields.get("calligraphicFont"),
                        "mathtypeTypstMathFont.calligraphicFont",
                    )?,
                })
            }
            _ => bail!(
                "mathtypeTypstMathFont must be a string or an object with font and calligraphicFont"
            ),
        }
    }
}

impl<'de> Deserialize<'de> for MathFontConfig {
    /// Accept historical string values at JSON boundaries while retaining canonical typed state.
    fn deserialize<D: Deserializer<'de>>(deserializer: D) -> std::result::Result<Self, D::Error> {
        Self::from_value(&Value::deserialize(deserializer)?).map_err(serde::de::Error::custom)
    }
}

/// Identify a supported setting independently of its historical YAML spelling.
#[derive(Clone, Copy, Debug, Eq, PartialEq, Ord, PartialOrd, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub enum SettingField {
    Mathtype,
    MathtypeConversionMethod,
    MathtypeTypstMathFont,
    MathtypeSvgBackend,
    DocxEmbedSvgImages,
    DocxConvertSvgToPng,
    DocxNativeCrossref,
    TableAutofit,
    DocxSvgToPngWidth,
    DocxSvgToPngDpi,
    DocxSvgToPngScale,
    CitationNumberRangeDelimiter,
    DocxShowLineNumbers,
    DocxShowPageNumbers,
    DocxPageMargins,
    DocxPageWidth,
    DocxStyle,
}

impl SettingField {
    /// Enumerate owned settings without treating arbitrary Pandoc metadata as fields.
    pub const ALL: [Self; 17] = [
        Self::Mathtype,
        Self::MathtypeConversionMethod,
        Self::MathtypeSvgBackend,
        Self::MathtypeTypstMathFont,
        Self::DocxNativeCrossref,
        Self::TableAutofit,
        Self::DocxEmbedSvgImages,
        Self::DocxConvertSvgToPng,
        Self::DocxSvgToPngWidth,
        Self::DocxSvgToPngDpi,
        Self::DocxSvgToPngScale,
        Self::CitationNumberRangeDelimiter,
        Self::DocxShowLineNumbers,
        Self::DocxShowPageNumbers,
        Self::DocxPageMargins,
        Self::DocxPageWidth,
        Self::DocxStyle,
    ];

    /// Resolve aliases only at compatibility boundaries.
    pub fn from_name(name: &str) -> Option<Self> {
        match canonical_field_name(name)? {
            "mathtype" => Some(Self::Mathtype),
            "mathtypeConversionMethod" => Some(Self::MathtypeConversionMethod),
            "mathtypeTypstMathFont" => Some(Self::MathtypeTypstMathFont),
            "mathtypeSvgBackend" => Some(Self::MathtypeSvgBackend),
            "docxEmbedSvgImages" => Some(Self::DocxEmbedSvgImages),
            "docxConvertSvgToPng" => Some(Self::DocxConvertSvgToPng),
            "docxNativeCrossref" => Some(Self::DocxNativeCrossref),
            "tableAutofit" => Some(Self::TableAutofit),
            "docxSvgToPngWidth" => Some(Self::DocxSvgToPngWidth),
            "docxSvgToPngDpi" => Some(Self::DocxSvgToPngDpi),
            "docxSvgToPngScale" => Some(Self::DocxSvgToPngScale),
            "citationNumberRangeDelimiter" => Some(Self::CitationNumberRangeDelimiter),
            "docxShowLineNumbers" => Some(Self::DocxShowLineNumbers),
            "docxShowPageNumbers" => Some(Self::DocxShowPageNumbers),
            "docxPageMargins" => Some(Self::DocxPageMargins),
            "docxPageWidth" => Some(Self::DocxPageWidth),
            "docxStyle" => Some(Self::DocxStyle),
            _ => None,
        }
    }

    /// Return the canonical wire spelling used by existing project files.
    pub fn name(self) -> &'static str {
        match self {
            Self::Mathtype => "mathtype",
            Self::MathtypeConversionMethod => "mathtypeConversionMethod",
            Self::MathtypeTypstMathFont => "mathtypeTypstMathFont",
            Self::MathtypeSvgBackend => "mathtypeSvgBackend",
            Self::DocxEmbedSvgImages => "docxEmbedSvgImages",
            Self::DocxConvertSvgToPng => "docxConvertSvgToPng",
            Self::DocxNativeCrossref => "docxNativeCrossref",
            Self::TableAutofit => "tableAutofit",
            Self::DocxSvgToPngWidth => "docxSvgToPngWidth",
            Self::DocxSvgToPngDpi => "docxSvgToPngDpi",
            Self::DocxSvgToPngScale => "docxSvgToPngScale",
            Self::CitationNumberRangeDelimiter => "citationNumberRangeDelimiter",
            Self::DocxShowLineNumbers => "docxShowLineNumbers",
            Self::DocxShowPageNumbers => "docxShowPageNumbers",
            Self::DocxPageMargins => "docxPageMargins",
            Self::DocxPageWidth => "docxPageWidth",
            Self::DocxStyle => "docxStyle",
        }
    }
}

/// Select the default authored-table layout, with none preserving existing attributes.
#[derive(Clone, Copy, Debug, Eq, PartialEq, Serialize, Deserialize)]
#[serde(rename_all = "lowercase")]
pub enum TableAutofit {
    Window,
    Content,
    Fixed,
    None,
}

impl TableAutofit {
    /// Return the shared Lua filter's canonical mode spelling.
    pub fn as_str(self) -> &'static str {
        match self {
            Self::Window => "window",
            Self::Content => "content",
            Self::Fixed => "fixed",
            Self::None => "none",
        }
    }
}

/// Select a formula conversion strategy after historical aliases are normalized.
#[derive(Clone, Copy, Debug, Eq, PartialEq, Serialize, Deserialize)]
#[serde(rename_all = "kebab-case")]
pub enum ConversionMethod {
    Auto,
    Rust,
    RustSdk,
    SetData,
    Both,
}

impl ConversionMethod {
    /// Preserve the canonical external strategy spelling without allocating JSON.
    pub fn as_str(self) -> &'static str {
        match self {
            Self::Auto => "auto",
            Self::Rust => "rust",
            Self::RustSdk => "rust-sdk",
            Self::SetData => "set-data",
            Self::Both => "both",
        }
    }
}

/// Select the supported SVG equation renderer without accepting arbitrary names.
#[derive(Clone, Copy, Debug, Eq, PartialEq, Serialize, Deserialize)]
#[serde(rename_all = "lowercase")]
pub enum SvgBackend {
    Ratex,
    Typst,
}

impl SvgBackend {
    /// Return the existing renderer spelling at environment and cache boundaries.
    pub fn as_str(self) -> &'static str {
        match self {
            Self::Ratex => "ratex",
            Self::Typst => "typst",
        }
    }
}

/// Preserve boolean switches and localized line-number spellings accepted by existing projects.
#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
#[serde(untagged)]
pub enum LineNumberMode {
    Enabled(bool),
    Named(String),
}

/// Retain physical-unit text or numeric points without carrying a generic JSON value.
#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
#[serde(untagged)]
pub enum LengthInput {
    Text(String),
    Number(Number),
}

/// Retain authored Word style order and its extensible typography attributes.
#[derive(Clone, Debug, Default)]
pub struct DocxStyles(Vec<(String, Map<String, Value>)>);

impl DocxStyles {
    /// Visit named styles in the same order as the original project configuration.
    pub fn iter(&self) -> impl Iterator<Item = &(String, Map<String, Value>)> {
        self.0.iter()
    }
}

impl Serialize for DocxStyles {
    /// Preserve project style order when returning to a YAML or Pandoc metadata object.
    fn serialize<S: Serializer>(&self, serializer: S) -> std::result::Result<S::Ok, S::Error> {
        let mut map = serializer.serialize_map(Some(self.0.len()))?;
        for (name, attributes) in &self.0 {
            map.serialize_entry(name, attributes)?;
        }
        map.end()
    }
}

impl<'de> Deserialize<'de> for DocxStyles {
    /// Decode named styles without replacing authored order with a sorted map.
    fn deserialize<D: Deserializer<'de>>(deserializer: D) -> std::result::Result<Self, D::Error> {
        /// Collect configured styles while preserving the serialized object order.
        struct StylesVisitor;
        impl<'de> Visitor<'de> for StylesVisitor {
            type Value = DocxStyles;

            /// Describe the configuration shape when a serializer supplies another type.
            fn expecting(&self, formatter: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
                formatter.write_str("a mapping of Word style names to attributes")
            }

            /// Keep style names and their attribute mappings in encounter order.
            fn visit_map<A: MapAccess<'de>>(
                self,
                mut map: A,
            ) -> std::result::Result<Self::Value, A::Error> {
                let mut styles = Vec::new();
                while let Some(entry) = map.next_entry::<String, Map<String, Value>>()? {
                    styles.push(entry);
                }
                Ok(DocxStyles(styles))
            }
        }
        deserializer.deserialize_map(StylesVisitor)
    }
}

/// Store every owned setting in its domain type; callers receive only a shared reference.
#[derive(Clone, Debug)]
pub struct SettingsValues {
    pub mathtype: bool,
    pub mathtype_conversion_method: ConversionMethod,
    pub mathtype_typst_math_font: MathFontConfig,
    pub mathtype_svg_backend: SvgBackend,
    pub docx_embed_svg_images: bool,
    pub docx_convert_svg_to_png: bool,
    pub docx_native_crossref: bool,
    pub table_autofit: TableAutofit,
    pub docx_svg_to_png_width: Option<i64>,
    pub docx_svg_to_png_dpi: Option<f64>,
    pub docx_svg_to_png_scale: Option<f64>,
    pub citation_number_range_delimiter: Option<String>,
    pub docx_show_line_numbers: LineNumberMode,
    pub docx_show_page_numbers: Option<bool>,
    pub docx_page_margins: Option<BTreeMap<String, LengthInput>>,
    pub docx_page_width: Option<LengthInput>,
    pub docx_style: Option<DocxStyles>,
}

impl Default for SettingsValues {
    /// Match established configuration defaults without constructing a mutable value dictionary.
    fn default() -> Self {
        Self {
            mathtype: false,
            mathtype_conversion_method: ConversionMethod::Auto,
            mathtype_typst_math_font: MathFontConfig::default(),
            mathtype_svg_backend: SvgBackend::Typst,
            docx_embed_svg_images: true,
            docx_convert_svg_to_png: false,
            docx_native_crossref: false,
            table_autofit: TableAutofit::None,
            docx_svg_to_png_width: None,
            docx_svg_to_png_dpi: None,
            docx_svg_to_png_scale: None,
            citation_number_range_delimiter: None,
            docx_show_line_numbers: LineNumberMode::Named("continuous".into()),
            docx_show_page_numbers: None,
            docx_page_margins: None,
            docx_page_width: None,
            docx_style: None,
        }
    }
}

impl SettingsValues {
    /// Decode one already validated canonical field into its concrete Rust type.
    pub(super) fn assign(&mut self, field: SettingField, value: Value) -> Result<()> {
        macro_rules! assign {
            ($target:ident) => {
                self.$target = serde_json::from_value(value)?
            };
        }
        match field {
            SettingField::Mathtype => assign!(mathtype),
            SettingField::MathtypeConversionMethod => assign!(mathtype_conversion_method),
            SettingField::MathtypeTypstMathFont => assign!(mathtype_typst_math_font),
            SettingField::MathtypeSvgBackend => assign!(mathtype_svg_backend),
            SettingField::DocxEmbedSvgImages => assign!(docx_embed_svg_images),
            SettingField::DocxConvertSvgToPng => assign!(docx_convert_svg_to_png),
            SettingField::DocxNativeCrossref => assign!(docx_native_crossref),
            SettingField::TableAutofit => assign!(table_autofit),
            SettingField::DocxSvgToPngWidth => assign!(docx_svg_to_png_width),
            SettingField::DocxSvgToPngDpi => assign!(docx_svg_to_png_dpi),
            SettingField::DocxSvgToPngScale => assign!(docx_svg_to_png_scale),
            SettingField::CitationNumberRangeDelimiter => assign!(citation_number_range_delimiter),
            SettingField::DocxShowLineNumbers => assign!(docx_show_line_numbers),
            SettingField::DocxShowPageNumbers => assign!(docx_show_page_numbers),
            SettingField::DocxPageMargins => assign!(docx_page_margins),
            SettingField::DocxPageWidth => assign!(docx_page_width),
            SettingField::DocxStyle => assign!(docx_style),
        }
        Ok(())
    }

    /// Materialize a single legacy value at a serializer or document-format boundary.
    pub(super) fn value(&self, field: SettingField) -> Value {
        match field {
            SettingField::Mathtype => json!(self.mathtype),
            SettingField::MathtypeConversionMethod => json!(self.mathtype_conversion_method),
            SettingField::MathtypeTypstMathFont => json!(self.mathtype_typst_math_font),
            SettingField::MathtypeSvgBackend => json!(self.mathtype_svg_backend),
            SettingField::DocxEmbedSvgImages => json!(self.docx_embed_svg_images),
            SettingField::DocxConvertSvgToPng => json!(self.docx_convert_svg_to_png),
            SettingField::DocxNativeCrossref => json!(self.docx_native_crossref),
            SettingField::TableAutofit => json!(self.table_autofit),
            SettingField::DocxSvgToPngWidth => json!(self.docx_svg_to_png_width),
            SettingField::DocxSvgToPngDpi => json!(self.docx_svg_to_png_dpi),
            SettingField::DocxSvgToPngScale => json!(self.docx_svg_to_png_scale),
            SettingField::CitationNumberRangeDelimiter => {
                json!(self.citation_number_range_delimiter)
            }
            SettingField::DocxShowLineNumbers => json!(self.docx_show_line_numbers),
            SettingField::DocxShowPageNumbers => json!(self.docx_show_page_numbers),
            SettingField::DocxPageMargins => json!(self.docx_page_margins),
            SettingField::DocxPageWidth => json!(self.docx_page_width),
            SettingField::DocxStyle => json!(self.docx_style),
        }
    }

    /// Generate canonical wire values only when a configuration boundary needs them.
    pub(super) fn mapping(&self) -> Map<String, Value> {
        SettingField::ALL
            .into_iter()
            .map(|field| (field.name().into(), self.value(field)))
            .collect()
    }
}

/// Keep supplied reply fields typed while omitted settings remain absent from its override layer.
#[derive(Clone, Debug)]
pub struct SettingsOverrides {
    pub(super) fields: SettingsValues,
    pub(super) provided: BTreeSet<SettingField>,
}

impl SettingsOverrides {
    /// Return an explicitly supplied override without exposing mutable configuration storage.
    pub fn get(&self, name: &str) -> Option<Value> {
        let field = SettingField::from_name(name)?;
        self.provided
            .contains(&field)
            .then(|| self.fields.value(field))
    }

    /// Retain the historical non-null reply merge representation at the YAML/JSON boundary.
    pub fn to_mapping(&self) -> Map<String, Value> {
        SettingField::ALL
            .into_iter()
            .filter(|field| self.provided.contains(field))
            .filter_map(|field| {
                let value = self.fields.value(field);
                (!value.is_null()).then(|| (field.name().into(), value))
            })
            .collect()
    }
}

impl Serialize for SettingsOverrides {
    /// Preserve the existing pmt_overrides wire object without storing it internally.
    fn serialize<S: Serializer>(&self, serializer: S) -> std::result::Result<S::Ok, S::Error> {
        self.to_mapping().serialize(serializer)
    }
}

impl<'de> Deserialize<'de> for SettingsOverrides {
    /// Validate incoming override wire data through the same project configuration boundary.
    fn deserialize<D: Deserializer<'de>>(deserializer: D) -> std::result::Result<Self, D::Error> {
        let mapping = Map::<String, Value>::deserialize(deserializer)?;
        let settings = PmtSettings::from_mapping(&mapping).map_err(serde::de::Error::custom)?;
        Ok(Self {
            fields: settings.fields,
            provided: settings.provided,
        })
    }
}

/// Keep the established effective-metadata JSON structure for tools and recorded comparisons.
#[derive(Serialize, Deserialize)]
struct SettingsWire {
    values: Map<String, Value>,
    provided: BTreeSet<String>,
    pandoc_metadata: Map<String, Value>,
    reply: Option<super::ReplySettings>,
}

impl Serialize for PmtSettings {
    /// Serialize legacy wire keys from typed state, including configured null values.
    fn serialize<S: Serializer>(&self, serializer: S) -> std::result::Result<S::Ok, S::Error> {
        SettingsWire {
            values: self.fields.mapping(),
            provided: self
                .provided
                .iter()
                .map(|field| field.name().into())
                .collect(),
            pandoc_metadata: self.pandoc_metadata.clone(),
            reply: self.reply.clone(),
        }
        .serialize(serializer)
    }
}

impl<'de> Deserialize<'de> for PmtSettings {
    /// Reject malformed persisted settings rather than bypassing project validation on load.
    fn deserialize<D: Deserializer<'de>>(deserializer: D) -> std::result::Result<Self, D::Error> {
        let wire = SettingsWire::deserialize(deserializer)?;
        let mut settings = Self::from_mapping(&wire.values).map_err(serde::de::Error::custom)?;
        settings.provided = wire
            .provided
            .into_iter()
            .map(|name| {
                SettingField::from_name(&name).ok_or_else(|| {
                    serde::de::Error::custom(format!("Unknown Papper setting: {name}"))
                })
            })
            .collect::<std::result::Result<_, _>>()?;
        settings.pandoc_metadata = wire.pandoc_metadata;
        settings.reply = wire.reply;
        Ok(settings)
    }
}

impl PmtSettings {
    /// Read domain fields without allowing callers to bypass their validation invariants.
    pub fn fields(&self) -> &SettingsValues {
        &self.fields
    }

    /// Read the set of explicitly supplied fields without permitting untracked mutation.
    pub fn provided(&self) -> &BTreeSet<SettingField> {
        &self.provided
    }

    /// Check configuration presence independently of nullable values or defaults.
    pub fn was_provided(&self, name: &str) -> bool {
        SettingField::from_name(name).is_some_and(|field| self.provided.contains(&field))
    }

    /// Apply an explicit CLI formula switch while tracking its configuration precedence.
    pub fn set_mathtype(&mut self, enabled: bool) {
        self.fields.mathtype = enabled;
        self.provided.insert(SettingField::Mathtype);
    }

    /// Change derived line-number policy without making an absent project field explicit.
    pub fn set_line_numbers(&mut self, mode: LineNumberMode) {
        self.fields.docx_show_line_numbers = mode;
    }

    /// Validate a font override before publishing it, preserving the previous value on failure.
    pub fn set_math_font(&mut self, font: MathFontConfig) -> Result<()> {
        self.fields.mathtype_typst_math_font = MathFontConfig::from_value(&json!(font))?;
        Ok(())
    }
}
