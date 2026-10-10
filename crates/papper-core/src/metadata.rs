//! Load and merge deterministic Papper settings separately from Pandoc metadata.

use anyhow::{Context, Result, bail};
use regex::Regex;
use serde::{Deserialize, Serialize};
use serde_json::{Map, Value, json};
use std::collections::{BTreeMap, BTreeSet};
use std::fs;
use std::path::{Component, Path, PathBuf};
use std::sync::OnceLock;
use yaml_rust::parser::{Event, Parser};
use yaml_rust::scanner::{TScalarStyle, TokenType};

mod settings;
pub use settings::{
    ConversionMethod, DocxStyles, LengthInput, LineNumberMode, MathFontConfig, SettingField,
    SettingsOverrides, SettingsValues, SvgBackend, TableAutofit,
};

/// Store canonical configuration while tracking explicitly supplied fields.
#[derive(Clone, Debug)]
pub struct PmtSettings {
    fields: SettingsValues,
    provided: BTreeSet<SettingField>,
    pub pandoc_metadata: Map<String, Value>,
    pub reply: Option<ReplySettings>,
}

/// Keep validated reply-specific overrides separate from base configuration.
#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct ReplySettings {
    pub pmt_overrides: SettingsOverrides,
    pub pandoc_metadata: Map<String, Value>,
}

/// Hold a build's immutable settings and Pandoc-facing metadata snapshot.
#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct EffectiveMetadata {
    pub pmt_settings: PmtSettings,
    pub pandoc_metadata: Map<String, Value>,
    pub has_yaml_header: bool,
}

/// Describe metadata sources in highest-to-lowest priority order.
#[derive(Clone, Debug)]
pub struct MetadataOptions {
    pub style_paths: Vec<PathBuf>,
    pub bundled_style_dir: PathBuf,
    pub allow_missing_header: bool,
    pub reply: bool,
    pub lang_override: Option<String>,
    pub resource_roots: Vec<PathBuf>,
}

impl Default for MetadataOptions {
    /// Supply ordinary project paths without reading configuration from the environment.
    fn default() -> Self {
        Self {
            style_paths: vec![PathBuf::from("style.yml")],
            bundled_style_dir: PathBuf::from("defaults"),
            allow_missing_header: false,
            reply: false,
            lang_override: None,
            resource_roots: Vec::new(),
        }
    }
}

/// Return the canonical Papper name for a supported historical field spelling.
pub fn canonical_field_name(name: &str) -> Option<&'static str> {
    match name {
        "mathtype" => Some("mathtype"),
        "mathtypeConversionMethod"
        | "mathtype-conversion-method"
        | "mathtype_conversion_method" => Some("mathtypeConversionMethod"),
        "mathtypeTypstMathFont" | "mathtype-typst-math-font" | "mathtype_typst_math_font" => {
            Some("mathtypeTypstMathFont")
        }
        "mathtypeSvgBackend" | "mathtype-svg-backend" | "mathtype_svg_backend" => {
            Some("mathtypeSvgBackend")
        }
        "docxEmbedSvgImages"
        | "docx-embed-svg-images"
        | "docx_embed_svg_images"
        | "embedSvgImages"
        | "embed-svg-images" => Some("docxEmbedSvgImages"),
        "docxConvertSvgToPng"
        | "docx-convert-svg-to-png"
        | "docx_convert_svg_to_png"
        | "convertSvgToPng"
        | "convert-svg-to-png" => Some("docxConvertSvgToPng"),
        "docxNativeCrossref" | "docx-native-crossref" | "docx_native_crossref" => {
            Some("docxNativeCrossref")
        }
        "tableAutofit" | "table-autofit" | "table_autofit" => Some("tableAutofit"),
        "docxSvgToPngWidth" | "docx-svg-to-png-width" | "docx_svg_to_png_width" => {
            Some("docxSvgToPngWidth")
        }
        "docxSvgToPngDpi" | "docx-svg-to-png-dpi" | "docx_svg_to_png_dpi" => {
            Some("docxSvgToPngDpi")
        }
        "docxSvgToPngScale" | "docx-svg-to-png-scale" | "docx_svg_to_png_scale" => {
            Some("docxSvgToPngScale")
        }
        "citationNumberRangeDelimiter"
        | "citation-number-range-delimiter"
        | "citation_number_range_delimiter" => Some("citationNumberRangeDelimiter"),
        "docxShowLineNumbers"
        | "docx-show-line-numbers"
        | "docx_show_line_numbers"
        | "show-line-numbers"
        | "showLineNumbers"
        | "show_line_numbers" => Some("docxShowLineNumbers"),
        "docxShowPageNumbers"
        | "docx-show-page-numbers"
        | "docx_show_page_numbers"
        | "show-page-numbers"
        | "showPageNumbers"
        | "show_page_numbers" => Some("docxShowPageNumbers"),
        "docxPageMargins" | "docx-page-margins" | "docx_page_margins" => Some("docxPageMargins"),
        "docxPageWidth" | "docx-page-width" | "docx_page_width" => Some("docxPageWidth"),
        "docxStyle" | "docx-style" | "docx_style" => Some("docxStyle"),
        _ => None,
    }
}

/// Recursively merge mappings while replacing scalars, arrays, and explicit nulls.
pub fn merge_metadata(
    base: &Map<String, Value>,
    overrides: &Map<String, Value>,
) -> Map<String, Value> {
    let mut merged = base.clone();
    for (key, value) in overrides {
        let next = match (merged.get(key), value) {
            (Some(Value::Object(old)), Value::Object(new)) => {
                Value::Object(merge_metadata(old, new))
            }
            _ => value.clone(),
        };
        merged.insert(key.clone(), next);
    }
    merged
}

/// Preserve explicit nulls so manuscript overrides can clear inherited nullable settings.
fn provided_settings_mapping(
    fields: &SettingsValues,
    provided: &BTreeSet<SettingField>,
) -> Map<String, Value> {
    provided
        .iter()
        .map(|field| (field.name().into(), fields.value(*field)))
        .collect()
}

/// Identify Chinese language tags using the existing build-language policy.
pub fn is_chinese_language(language: &Value) -> bool {
    let Some(raw) = language.as_str() else {
        return false;
    };
    let normalized = raw.trim().replace('_', "-").to_lowercase();
    matches!(normalized.as_str(), "zh" | "zhcn" | "zh-hans" | "zhhans")
        || normalized.starts_with("zh-")
}

/// Map common Simplified Chinese aliases to the tag shipped by pandoc-crossref.
pub fn normalize_pandoc_language(language: &Value) -> Value {
    let Some(raw) = language.as_str() else {
        return language.clone();
    };
    let normalized = raw.trim().replace('_', "-").to_lowercase();
    if matches!(normalized.as_str(), "zh-cn" | "zhcn" | "zh-hans" | "zhhans") {
        json!("zh-Hans")
    } else {
        language.clone()
    }
}

/// Accumulate one mapping or sequence before expanding its completed anchors.
struct YamlFrame {
    value: Value,
    anchor: usize,
    pending_key: Option<(String, bool)>,
    merges: Vec<Value>,
}

/// Parse Python-compatible integer forms, including octal and sexagesimal values.
fn yaml_integer(raw: &str) -> Option<Value> {
    static INTEGER: OnceLock<Regex> = OnceLock::new();
    let grammar = INTEGER.get_or_init(|| Regex::new(r"^[-+]?(?:0b[0-1_]+|0[0-7_]+|(?:0|[1-9][0-9_]*)|0x[0-9a-fA-F_]+|[1-9][0-9_]*(?::[0-5]?[0-9])+)$").expect("valid YAML integer grammar"));
    if !grammar.is_match(raw) {
        return None;
    }
    let cleaned = raw.replace('_', "");
    let (negative, digits) = if let Some(digits) = cleaned.strip_prefix('-') {
        (true, digits)
    } else {
        (false, cleaned.strip_prefix('+').unwrap_or(&cleaned))
    };
    let unsigned = if digits.contains(':') {
        digits.split(':').try_fold(0_u64, |total, part| {
            total.checked_mul(60)?.checked_add(part.parse().ok()?)
        })?
    } else if let Some(binary) = digits.strip_prefix("0b") {
        u64::from_str_radix(binary, 2).ok()?
    } else if let Some(hex) = digits.strip_prefix("0x") {
        u64::from_str_radix(hex, 16).ok()?
    } else if digits.starts_with('0') && digits.len() > 1 {
        u64::from_str_radix(digits, 8).ok()?
    } else {
        digits.parse().ok()?
    };
    if negative {
        i64::try_from(i128::from(unsigned).checked_neg()?)
            .ok()
            .map(|number| json!(number))
    } else {
        Some(json!(unsigned))
    }
}

/// Parse YAML 1.1 float spellings without converting plain scientific notation strings.
fn yaml_float(raw: &str, explicit: bool) -> Result<Option<Value>> {
    static FLOAT: OnceLock<Regex> = OnceLock::new();
    let grammar = FLOAT.get_or_init(|| Regex::new(r"^(?:[-+]?[0-9][0-9_]*\.[0-9_]*(?:[eE][-+][0-9]+)?|\.[0-9][0-9_]*(?:[eE][-+][0-9]+)?|[-+]?[0-9][0-9_]*(?::[0-5]?[0-9])+\.[0-9_]*|[-+]?\.(?:inf|Inf|INF)|\.(?:nan|NaN|NAN))$").expect("valid YAML float grammar"));
    if !explicit && !grammar.is_match(raw) {
        return Ok(None);
    }
    let cleaned = raw.replace('_', "");
    let number = if cleaned.contains(':') {
        let negative = cleaned.starts_with('-');
        let digits = cleaned.trim_start_matches(['-', '+']);
        let number = digits.split(':').try_fold(0.0, |total, part| {
            part.parse::<f64>().map(|number| total * 60.0 + number)
        })?;
        if negative { -number } else { number }
    } else {
        cleaned
            .parse::<f64>()
            .with_context(|| format!("Invalid YAML float: {raw}"))?
    };
    let number = serde_json::Number::from_f64(number).ok_or_else(|| {
        anyhow::anyhow!("Non-finite YAML number cannot be represented in Pandoc metadata: {raw}")
    })?;
    Ok(Some(Value::Number(number)))
}

/// Preserve quoted values while matching the original PyYAML plain-scalar rules.
fn yaml_scalar(raw: &str, style: TScalarStyle, tag: Option<TokenType>) -> Result<Value> {
    let explicit = match tag {
        Some(TokenType::Tag(handle, suffix)) if handle == "!!" => Some(suffix),
        Some(TokenType::Tag(handle, suffix)) => bail!("Unsupported YAML tag: {handle}{suffix}"),
        _ => None,
    };
    if explicit.as_deref() == Some("str") || (explicit.is_none() && style != TScalarStyle::Plain) {
        return Ok(json!(raw));
    }
    let boolean = match raw {
        "yes" | "Yes" | "YES" | "true" | "True" | "TRUE" | "on" | "On" | "ON" => Some(true),
        "no" | "No" | "NO" | "false" | "False" | "FALSE" | "off" | "Off" | "OFF" => Some(false),
        _ => None,
    };
    if explicit.as_deref() == Some("bool") || (explicit.is_none() && boolean.is_some()) {
        return boolean
            .map(|value| json!(value))
            .ok_or_else(|| anyhow::anyhow!("Invalid YAML boolean: {raw}"));
    }
    if explicit.as_deref() == Some("null")
        || (explicit.is_none() && matches!(raw, "" | "~" | "null" | "Null" | "NULL"))
    {
        return Ok(Value::Null);
    }
    if explicit.as_deref() == Some("int") {
        return yaml_integer(raw).ok_or_else(|| anyhow::anyhow!("Invalid YAML integer: {raw}"));
    }
    if explicit.is_none()
        && let Some(integer) = yaml_integer(raw)
    {
        return Ok(integer);
    }
    if (explicit.as_deref() == Some("float") || explicit.is_none())
        && let Some(number) = yaml_float(raw, explicit.is_some())?
    {
        return Ok(number);
    }
    if explicit
        .as_deref()
        .is_some_and(|tag| !matches!(tag, "timestamp" | "merge"))
    {
        bail!("Unsupported YAML tag: !!{}", explicit.unwrap())
    }
    // Dates stay ISO strings at the engine boundary; quoted dates remain strings too.
    Ok(json!(raw))
}

/// Insert completed nodes with last-key-wins behavior and explicit merge-key tracking.
fn attach_yaml_node(
    value: Value,
    anchor: usize,
    merge_key: bool,
    frames: &mut [YamlFrame],
    anchors: &mut BTreeMap<usize, Value>,
    root: &mut Option<Value>,
) -> Result<()> {
    if anchor > 0 {
        anchors.insert(anchor, value.clone());
    }
    if let Some(frame) = frames.last_mut() {
        match &mut frame.value {
            Value::Array(items) => items.push(value),
            Value::Object(mapping) => {
                if let Some((key, is_merge)) = frame.pending_key.take() {
                    if is_merge {
                        frame.merges.push(value);
                    } else {
                        mapping.insert(key, value);
                    }
                } else {
                    let key = match value {
                        Value::String(key) => key,
                        Value::Bool(value) => value.to_string(),
                        Value::Number(value) => value.to_string(),
                        Value::Null => "null".to_string(),
                        _ => bail!("YAML mapping keys must be scalars"),
                    };
                    frame.pending_key = Some((key, merge_key));
                }
            }
            _ => bail!("Invalid YAML container"),
        }
    } else if root.replace(value).is_some() {
        bail!("YAML metadata must contain exactly one document")
    }
    Ok(())
}

/// Expand YAML merge sequences in reverse so their first source has precedence.
fn complete_yaml_frame(mut frame: YamlFrame) -> Result<(Value, usize)> {
    if frame.pending_key.is_some() {
        bail!("YAML mapping is missing a value")
    }
    if let Value::Object(explicit) = frame.value {
        let mut merged = Map::new();
        for merge in frame.merges {
            match merge {
                Value::Object(mapping) => merged.extend(mapping),
                Value::Array(items) => {
                    for item in items.into_iter().rev() {
                        let Value::Object(mapping) = item else {
                            bail!("YAML merge sequences must contain mappings")
                        };
                        merged.extend(mapping);
                    }
                }
                _ => bail!("YAML merge keys must refer to mappings or sequences of mappings"),
            }
        }
        merged.extend(explicit);
        frame.value = Value::Object(merged);
    }
    Ok((frame.value, frame.anchor))
}

/// Parse a mapping with PyYAML-compatible duplicate keys, scalar types, and anchors.
pub fn parse_yaml_mapping(text: &str, source: &Path) -> Result<Map<String, Value>> {
    let result: Result<Map<String, Value>> = (|| {
        let mut parser = Parser::new(text.chars());
        let mut frames = Vec::new();
        let mut anchors = BTreeMap::new();
        let mut root = None;
        loop {
            let (event, _) = parser.next()?;
            match event {
                Event::Scalar(raw, style, anchor, tag) => {
                    let merge_key = (style == TScalarStyle::Plain && raw == "<<")
                        || matches!(&tag, Some(TokenType::Tag(handle, suffix)) if handle == "!!" && suffix == "merge");
                    attach_yaml_node(
                        yaml_scalar(&raw, style, tag)?,
                        anchor,
                        merge_key,
                        &mut frames,
                        &mut anchors,
                        &mut root,
                    )?;
                }
                Event::Alias(anchor) => {
                    let value = anchors.get(&anchor).cloned().ok_or_else(|| {
                        anyhow::anyhow!("Recursive or unresolved YAML alias is not valid metadata")
                    })?;
                    attach_yaml_node(value, 0, false, &mut frames, &mut anchors, &mut root)?;
                }
                Event::SequenceStart(anchor) | Event::MappingStart(anchor) => {
                    if frames.len() >= 256 {
                        bail!("YAML metadata nesting exceeds 256 levels")
                    }
                    let value = if matches!(event, Event::SequenceStart(_)) {
                        Value::Array(Vec::new())
                    } else {
                        Value::Object(Map::new())
                    };
                    frames.push(YamlFrame {
                        value,
                        anchor,
                        pending_key: None,
                        merges: Vec::new(),
                    });
                }
                Event::SequenceEnd | Event::MappingEnd => {
                    let frame = frames
                        .pop()
                        .ok_or_else(|| anyhow::anyhow!("Unexpected YAML container end"))?;
                    let (value, anchor) = complete_yaml_frame(frame)?;
                    attach_yaml_node(value, anchor, false, &mut frames, &mut anchors, &mut root)?;
                }
                Event::StreamEnd => break,
                _ => (),
            }
        }
        let value = root.unwrap_or(Value::Null);
        if is_empty(&value) {
            return Ok(Map::new());
        }
        value
            .as_object()
            .cloned()
            .ok_or_else(|| anyhow::anyhow!("YAML metadata must be a mapping"))
    })();
    result.with_context(|| format!("Invalid YAML metadata in {}", source.display()))
}

/// Read a standalone UTF-8 metadata file with source-aware validation errors.
pub fn parse_yaml_file(path: impl AsRef<Path>) -> Result<Map<String, Value>> {
    let path = path.as_ref();
    parse_yaml_mapping(
        &fs::read_to_string(path)
            .with_context(|| format!("Cannot read metadata file: {}", path.display()))?,
        path,
    )
}

/// Cache the current front-matter recognition grammar for repeated build requests.
fn header_pattern() -> &'static Regex {
    static PATTERN: OnceLock<Regex> = OnceLock::new();
    PATTERN
        .get_or_init(|| Regex::new(r"(?s)^---\s*\n(.*?)\n---").expect("valid front-matter grammar"))
}

/// Parse metadata from an immutable manuscript snapshot rather than rereading a file.
pub fn parse_yaml_header_text(text: &str, source: &Path) -> Result<Map<String, Value>> {
    let Some(captures) = header_pattern().captures(text) else {
        bail!(
            "No YAML front matter found in markdown file: {}",
            source.display()
        )
    };
    parse_yaml_mapping(&captures[1], source)
        .with_context(|| format!("YAML front matter must be a mapping: {}", source.display()))
}

/// Identify replies only from their own header, resolving paths beside the reply.
pub fn reply_manuscript_text(text: &str, source: &Path) -> Result<Option<PathBuf>> {
    let normalized = text.trim_start_matches('\u{feff}').replace("\r\n", "\n");
    if !header_pattern().is_match(&normalized) {
        return Ok(None);
    }
    let header = parse_yaml_header_text(&normalized, source)?;
    let Some(value) = header.get("reply") else {
        return Ok(None);
    };
    let path = value
        .as_str()
        .filter(|path| !path.trim().is_empty())
        .with_context(|| {
            format!(
                "YAML `reply` must be a non-empty manuscript path: {}",
                source.display()
            )
        })?;
    let path = PathBuf::from(path);
    Ok(Some(if path.is_absolute() {
        path
    } else {
        source.parent().unwrap_or(Path::new(".")).join(path)
    }))
}

/// Parse front matter from an on-disk manuscript using its full source identity.
pub fn parse_yaml_header(path: impl AsRef<Path>) -> Result<Map<String, Value>> {
    let path = path.as_ref();
    parse_yaml_header_text(
        &fs::read_to_string(path)
            .with_context(|| format!("Cannot read manuscript: {}", path.display()))?
            .replace("\r\n", "\n"),
        path,
    )
}

/// Remove a validated front-matter block so Pandoc cannot merge it a second time.
pub fn markdown_without_yaml_header(text: &str) -> &str {
    static PATTERN: OnceLock<Regex> = OnceLock::new();
    let expression = PATTERN.get_or_init(|| {
        Regex::new(r"(?s)\A---[ \t]*\r?\n.*?\r?\n---[ \t]*(?:\r?\n|$)")
            .expect("valid header-removal grammar")
    });
    expression
        .find(text)
        .map(|matched| &text[matched.end()..])
        .unwrap_or(text)
}

/// Convert the accepted bool spellings at the typed configuration boundary.
fn parse_bool(value: &Value, name: &str) -> Result<Value> {
    if let Some(flag) = value.as_bool() {
        return Ok(json!(flag));
    }
    if let Some(number) = value.as_f64()
        && (number == 0.0 || number == 1.0)
    {
        return Ok(json!(number == 1.0));
    }
    if let Some(raw) = value.as_str() {
        match raw.to_lowercase().as_str() {
            "0" | "off" | "f" | "false" | "n" | "no" => return Ok(json!(false)),
            "1" | "on" | "t" | "true" | "y" | "yes" => return Ok(json!(true)),
            _ => (),
        }
    }
    bail!("{name} must be a valid boolean")
}

/// Normalize supported MathType method aliases without loading conversion engines.
fn conversion_method(value: &Value) -> Result<Value> {
    let Some(raw) = value.as_str() else {
        bail!("mathtypeConversionMethod must be a string")
    };
    let normalized = raw.trim().to_lowercase().replace('_', "-");
    let canonical = match normalized.as_str() {
        "rust" | "mathtype-rust" | "mtef" => "rust",
        "rust-sdk" | "sdk" | "sdk-xform-ole" => "rust-sdk",
        "set-data" | "setdata" => "set-data",
        "auto" => "auto",
        "both" => "both",
        _ => bail!(
            "unsupported MathType conversion method; expected one of: rust, rust-sdk, set-data, auto, both"
        ),
    };
    Ok(json!(canonical))
}

/// Validate positive rasterization controls and coerce compatible numeric strings.
fn positive_number(value: &Value, name: &str, integer: bool) -> Result<Value> {
    if value.is_null() {
        return Ok(Value::Null);
    }
    let number = value
        .as_f64()
        .or_else(|| value.as_bool().map(|flag| u8::from(flag) as f64))
        .or_else(|| value.as_str().and_then(|raw| raw.trim().parse().ok()))
        .ok_or_else(|| anyhow::anyhow!("{name} must be a number"))?;
    if !number.is_finite() || number <= 0.0 {
        bail!("{name} must be greater than 0")
    }
    if integer {
        if number.fract() != 0.0 || number > i64::MAX as f64 {
            bail!("{name} must be an integer")
        }
        Ok(json!(number as i64))
    } else {
        Ok(json!(number))
    }
}

/// Validate one canonical field while preserving false, zero, and nullable values.
fn validate_field(name: &str, value: &Value) -> Result<Value> {
    match name {
        "mathtype" | "docxNativeCrossref" | "docxEmbedSvgImages" | "docxConvertSvgToPng" => {
            parse_bool(value, name)
        }
        "docxShowPageNumbers" if value.is_null() => Ok(Value::Null),
        "tableAutofit" => match value.as_str() {
            Some("window" | "content" | "fixed" | "none") => Ok(value.clone()),
            _ => bail!("tableAutofit must be window, content, fixed, or none"),
        },
        "docxShowPageNumbers" => parse_bool(value, name),
        "mathtypeConversionMethod" => conversion_method(value),
        "mathtypeTypstMathFont" => Ok(json!(MathFontConfig::from_value(value)?)),
        "mathtypeSvgBackend" => match value.as_str() {
            Some("ratex" | "typst") => Ok(value.clone()),
            _ => bail!("mathtypeSvgBackend must be ratex or typst"),
        },
        "docxSvgToPngWidth" => positive_number(value, name, true),
        "docxSvgToPngDpi" | "docxSvgToPngScale" => positive_number(value, name, false),
        "citationNumberRangeDelimiter" if value.is_null() || value.is_string() => Ok(value.clone()),
        "citationNumberRangeDelimiter" => bail!("citationNumberRangeDelimiter must be a string"),
        "docxShowLineNumbers" if value.is_string() => Ok(value.clone()),
        "docxShowLineNumbers" => parse_bool(value, name),
        "docxPageMargins" if value.is_null() => Ok(Value::Null),
        "docxPageMargins" => {
            let Some(mapping) = value.as_object() else {
                bail!("docxPageMargins must be a mapping")
            };
            let mut margins = Map::new();
            for (side, length) in mapping {
                if length.is_string() || length.is_number() {
                    margins.insert(side.clone(), length.clone());
                } else if let Some(flag) = length.as_bool() {
                    margins.insert(side.clone(), json!(u8::from(flag)));
                } else {
                    bail!("docxPageMargins.{side} must be a length value")
                }
            }
            Ok(Value::Object(margins))
        }
        "docxPageWidth" if value.is_null() || value.is_string() || value.is_number() => {
            Ok(value.clone())
        }
        "docxPageWidth" if value.is_boolean() => Ok(json!(u8::from(value.as_bool().unwrap()))),
        "docxPageWidth" => bail!("docxPageWidth must be a length value"),
        "docxStyle" if value.is_null() => Ok(Value::Null),
        "docxStyle" => {
            let Some(mapping) = value.as_object() else {
                bail!("docxStyle must be a mapping")
            };
            if let Some((style, _)) = mapping.iter().find(|(_, value)| !value.is_object()) {
                bail!("docxStyle.{style} must be a mapping")
            }
            Ok(value.clone())
        }
        _ => bail!("Unknown Papper setting: {name}"),
    }
}

/// Keep Papper values, Pandoc metadata and optional reviewer overrides separate.
type StyleDomains = (
    Map<String, Value>,
    Map<String, Value>,
    Option<ReplySettings>,
);

/// Separate style domains and migrate legacy spellings without rewriting source files.
fn split_style_mapping(
    raw: &Map<String, Value>,
    source: &Path,
    section: &str,
) -> Result<StyleDomains> {
    let mut explicit = match raw.get("pandocMetadata") {
        None => Map::new(),
        Some(Value::Object(mapping)) => mapping.clone(),
        _ => bail!(
            "`pandocMetadata` in {} ({section}) must be a YAML mapping",
            source.display()
        ),
    };
    let legacy_delimiter = explicit.remove("citation-number-range-delimiter");
    let reply = match raw.get("reply") {
        None | Some(Value::Null) => None,
        Some(Value::Object(_)) if section == "reply" => None,
        Some(Value::Object(reply)) => Some(ReplySettings::from_mapping(reply, source)?),
        _ => bail!(
            "`reply` in {} ({section}) must be a YAML mapping",
            source.display()
        ),
    };
    let mut pmt = Map::new();
    let mut legacy_pandoc = Map::new();
    let mut legacy_body = None;
    for (name, value) in raw {
        if name == "pandocMetadata" || name == "reply" {
            continue;
        }
        if matches!(
            name.as_str(),
            "bodyText" | "body-text" | "body_text" | "docxBodyText" | "docx-body-text"
        ) {
            let Some(body) = value.as_object() else {
                bail!(
                    "`{name}` in {} ({section}) must be a YAML mapping",
                    source.display()
                )
            };
            legacy_body = Some(body.clone());
        } else if let Some(canonical) = canonical_field_name(name) {
            pmt.insert(canonical.to_string(), value.clone());
        } else {
            legacy_pandoc.insert(name.clone(), value.clone());
        }
    }
    if let Some(body) = legacy_body {
        let old = json!({"正文文本": body}).as_object().unwrap().clone();
        let explicit_styles = match pmt.get("docxStyle") {
            None => Map::new(),
            Some(Value::Object(styles)) => styles.clone(),
            _ => bail!("docxStyle must be a mapping when legacy bodyText is configured"),
        };
        pmt.insert(
            "docxStyle".to_string(),
            Value::Object(merge_metadata(&old, &explicit_styles)),
        );
    }
    if let Some(delimiter) = legacy_delimiter.filter(|value| !value.is_null()) {
        pmt.entry("citationNumberRangeDelimiter".to_string())
            .or_insert(delimiter);
        eprintln!(
            "[WARN] Deprecated pandocMetadata.citation-number-range-delimiter in {} ({section}). Move it to top-level citationNumberRangeDelimiter.",
            source.display()
        );
    }
    if !legacy_pandoc.is_empty() {
        let mut keys: Vec<_> = legacy_pandoc.keys().map(String::as_str).collect();
        keys.sort_unstable();
        let mut conflicts: Vec<_> = legacy_pandoc
            .keys()
            .filter(|key| explicit.contains_key(*key))
            .map(String::as_str)
            .collect();
        conflicts.sort_unstable();
        let conflict_note = if conflicts.is_empty() {
            String::new()
        } else {
            format!(
                " Conflicts use pandocMetadata values: {}.",
                conflicts.join(", ")
            )
        };
        eprintln!(
            "[WARN] Deprecated flat Pandoc metadata in {} ({section}): {}. Move these keys under pandocMetadata.{conflict_note}",
            source.display(),
            keys.join(", ")
        );
    }
    Ok((pmt, merge_metadata(&legacy_pandoc, &explicit), reply))
}

/// Match YamlConfigSettingsSource's alias-choice precedence before splitting domains.
fn normalize_style_source_aliases(raw: &Map<String, Value>) -> Map<String, Value> {
    let mut output = Map::new();
    let mut priorities = BTreeMap::new();
    for (name, value) in raw {
        let canonical = canonical_field_name(name).or(match name.as_str() {
            "pandocMetadata" | "pandoc_metadata" => Some("pandocMetadata"),
            _ => None,
        });
        let Some(canonical) = canonical else {
            output.insert(name.clone(), value.clone());
            continue;
        };
        let priority = if name == canonical {
            0
        } else if matches!(name.as_str(), "embedSvgImages" | "convertSvgToPng") {
            3
        } else if matches!(name.as_str(), "embed-svg-images" | "convert-svg-to-png") {
            4
        } else if name == "show-line-numbers" || name == "show-page-numbers" {
            3
        } else if name == "showLineNumbers" || name == "showPageNumbers" {
            4
        } else if name == "show_line_numbers" || name == "show_page_numbers" {
            5
        } else if name.contains('-') {
            1
        } else {
            2
        };
        if priorities.get(canonical).is_none_or(|old| priority < *old) {
            priorities.insert(canonical, priority);
            output.insert(canonical.to_string(), value.clone());
        }
    }
    output
}

impl Default for PmtSettings {
    /// Create typed application defaults with no explicit configuration overrides.
    fn default() -> Self {
        Self {
            fields: SettingsValues::default(),
            provided: BTreeSet::new(),
            pandoc_metadata: Map::new(),
            reply: None,
        }
    }
}

/// Resolve both font paths against their owning style without treating family names as paths.
fn resolve_math_font_paths(fonts: &MathFontConfig, source: &Path) -> Result<MathFontConfig> {
    /// Preserve family names and resolve font files independently for body and calligraphy.
    fn resolve(font: &str, source: &Path) -> Result<String> {
        let font_path = expand_home(font);
        let suffix = font_path
            .extension()
            .and_then(|extension| extension.to_str())
            .unwrap_or("")
            .to_lowercase();
        if font.contains('/')
            || font.contains('\\')
            || matches!(suffix.as_str(), "otf" | "ttf" | "ttc" | "otc")
        {
            let path = if font_path.is_absolute() {
                font_path
            } else {
                source.parent().unwrap_or(Path::new(".")).join(font_path)
            };
            Ok(absolute_path(&path)?.to_string_lossy().into())
        } else {
            Ok(font.into())
        }
    }
    Ok(MathFontConfig {
        font: resolve(&fonts.font, source)?,
        calligraphic_font: resolve(&fonts.calligraphic_font, source)?,
    })
}

impl PmtSettings {
    /// Validate only Papper-owned fields, rejecting unknown application settings.
    pub fn from_mapping(mapping: &Map<String, Value>) -> Result<Self> {
        let mut settings = Self::default();
        for (name, value) in mapping {
            if name == "pandocMetadata" || name == "pandoc_metadata" {
                settings.pandoc_metadata = value
                    .as_object()
                    .cloned()
                    .ok_or_else(|| anyhow::anyhow!("pandocMetadata must be a mapping"))?;
                continue;
            }
            if name == "reply" {
                settings.reply = match value {
                    Value::Null => None,
                    Value::Object(reply) => {
                        Some(ReplySettings::from_mapping(reply, Path::new("style.yml"))?)
                    }
                    _ => bail!("reply must be a mapping"),
                };
                continue;
            }
            let field = SettingField::from_name(name)
                .ok_or_else(|| anyhow::anyhow!("Unknown Papper setting: {name}"))?;
            if !settings.provided.insert(field) {
                bail!(
                    "Duplicate aliases supplied for Papper setting: {}",
                    field.name()
                )
            }
            settings
                .fields
                .assign(field, validate_field(field.name(), value)?)?;
        }
        let controls: Vec<&str> = ["docxSvgToPngWidth", "docxSvgToPngScale", "docxSvgToPngDpi"]
            .into_iter()
            .filter(|name| settings.get(name).is_some_and(|value| !value.is_null()))
            .collect();
        if controls.len() > 1 {
            bail!(
                "Only one of docxSvgToPngWidth, docxSvgToPngScale, docxSvgToPngDpi can be set; got: {}",
                controls.join(", ")
            )
        }
        Ok(settings)
    }

    /// Validate either a style file or a manuscript-local style mapping with shared rules.
    fn from_style_mapping(raw: &Map<String, Value>, source: &Path, section: &str) -> Result<Self> {
        let result: Result<Self> = (|| {
            let raw = normalize_style_source_aliases(raw);
            let (values, pandoc_metadata, reply) = split_style_mapping(&raw, source, section)?;
            let mut settings = Self::from_mapping(&values)?;
            settings.pandoc_metadata = pandoc_metadata;
            settings.reply = reply;
            settings.set_math_font(resolve_math_font_paths(
                &settings.fields.mathtype_typst_math_font,
                source,
            )?)?;
            Ok(settings)
        })();
        result.with_context(|| format!("Invalid {section} settings in {}", source.display()))
    }

    /// Split, validate, and resolve one immutable style-file snapshot.
    pub fn from_yaml_text(text: &str, source: &Path) -> Result<Self> {
        Self::from_style_mapping(&parse_yaml_mapping(text, source)?, source, "style")
    }

    /// Read a style independently of implicit environment and dotenv settings.
    pub fn load(path: impl AsRef<Path>) -> Result<Self> {
        let path = path.as_ref();
        Self::from_yaml_text(
            &fs::read_to_string(path)
                .with_context(|| format!("Cannot read style settings: {}", path.display()))?,
            path,
        )
    }

    /// Look up a configuration value by its canonical name or accepted alias.
    pub fn get(&self, name: &str) -> Option<Value> {
        SettingField::from_name(name).map(|field| self.fields.value(field))
    }

    /// Read a boolean field without treating a missing or null value as true.
    pub fn get_bool(&self, name: &str) -> Option<bool> {
        match SettingField::from_name(name)? {
            SettingField::Mathtype => Some(self.fields.mathtype),
            SettingField::DocxNativeCrossref => Some(self.fields.docx_native_crossref),
            SettingField::DocxEmbedSvgImages => Some(self.fields.docx_embed_svg_images),
            SettingField::DocxConvertSvgToPng => Some(self.fields.docx_convert_svg_to_png),
            SettingField::DocxShowPageNumbers => self.fields.docx_show_page_numbers,
            SettingField::DocxShowLineNumbers => match self.fields.docx_show_line_numbers {
                LineNumberMode::Enabled(value) => Some(value),
                LineNumberMode::Named(_) => None,
            },
            _ => None,
        }
    }

    /// Read a string field while preserving explicit blank metadata where supported.
    pub fn get_str(&self, name: &str) -> Option<&str> {
        match SettingField::from_name(name)? {
            SettingField::MathtypeConversionMethod => {
                Some(self.fields.mathtype_conversion_method.as_str())
            }
            SettingField::MathtypeSvgBackend => Some(self.fields.mathtype_svg_backend.as_str()),
            SettingField::TableAutofit => Some(self.fields.table_autofit.as_str()),
            SettingField::CitationNumberRangeDelimiter => {
                self.fields.citation_number_range_delimiter.as_deref()
            }
            SettingField::DocxShowLineNumbers => match &self.fields.docx_show_line_numbers {
                LineNumberMode::Named(value) => Some(value),
                LineNumberMode::Enabled(_) => None,
            },
            SettingField::DocxPageWidth => match &self.fields.docx_page_width {
                Some(LengthInput::Text(value)) => Some(value),
                _ => None,
            },
            _ => None,
        }
    }

    /// Return supplied overrides including explicit nulls, or all non-null fields.
    pub fn to_mapping(&self, exclude_unset: bool) -> Map<String, Value> {
        SettingField::ALL
            .into_iter()
            .filter(|field| !exclude_unset || self.provided.contains(field))
            .filter_map(|field| {
                let value = self.fields.value(field);
                // Project styles use null to retain reference margins or footer
                // settings; dropping it here would restore bundled defaults.
                (exclude_unset || !value.is_null()).then(|| (field.name().into(), value))
            })
            .collect()
    }

    /// Apply validated reply overrides without mutating the reusable base settings.
    pub fn for_reply(&self) -> Result<Self> {
        let Some(reply) = &self.reply else {
            return Ok(self.clone());
        };
        let mut result = Self::from_mapping(&merge_metadata(
            &self.to_mapping(false),
            &reply.pmt_overrides.to_mapping(),
        ))?;
        result.pandoc_metadata = merge_metadata(&self.pandoc_metadata, &reply.pandoc_metadata);
        Ok(result)
    }

    /// Copy only Pandoc-facing metadata and optionally normalize its language tag.
    pub fn for_pandoc(&self, language: Option<&str>) -> Map<String, Value> {
        let mut metadata = self.pandoc_metadata.clone();
        let selected = language
            .map(|language| json!(language))
            .or_else(|| metadata.get("lang").cloned());
        if let Some(language) = selected
            && is_chinese_language(&language)
        {
            metadata.insert("lang".to_string(), normalize_pandoc_language(&language));
        }
        metadata
    }
}

impl ReplySettings {
    /// Validate reply fields as an override layer rather than installing fresh defaults.
    pub fn from_mapping(raw: &Map<String, Value>, source: &Path) -> Result<Self> {
        let (pmt, pandoc_metadata, _) = split_style_mapping(raw, source, "reply")?;
        let mut settings = PmtSettings::from_mapping(&pmt)?;
        settings.set_math_font(resolve_math_font_paths(
            &settings.fields.mathtype_typst_math_font,
            source,
        )?)?;
        Ok(Self {
            pmt_overrides: SettingsOverrides {
                fields: settings.fields,
                provided: settings.provided,
            },
            pandoc_metadata,
        })
    }
}

/// Expand a leading home directory only for explicitly configured file assets.
fn expand_home(path: &str) -> PathBuf {
    if (path == "~" || path.starts_with("~/") || path.starts_with("~\\"))
        && let Some(home) = std::env::var_os("USERPROFILE").or_else(|| std::env::var_os("HOME"))
    {
        return PathBuf::from(home).join(path.get(2..).unwrap_or(""));
    }
    PathBuf::from(path)
}

/// Resolve relative components while accepting a configured font file not yet present.
fn absolute_path(path: &Path) -> Result<PathBuf> {
    if let Ok(canonical) = path.canonicalize() {
        #[cfg(windows)]
        {
            let rendered = canonical.to_string_lossy();
            if let Some(normal) = rendered.strip_prefix("\\\\?\\UNC\\") {
                return Ok(PathBuf::from(format!("\\\\{normal}")));
            }
            if let Some(normal) = rendered.strip_prefix("\\\\?\\") {
                return Ok(PathBuf::from(normal));
            }
        }
        return Ok(canonical);
    }
    let absolute = if path.is_absolute() {
        path.to_path_buf()
    } else {
        std::env::current_dir()?.join(path)
    };
    let mut cleaned = PathBuf::new();
    for component in absolute.components() {
        match component {
            Component::CurDir => (),
            Component::ParentDir => {
                cleaned.pop();
            }
            _ => cleaned.push(component.as_os_str()),
        }
    }
    Ok(cleaned)
}

/// Read language defaults from packaged resources, with compiled-in installation fallback.
fn bundled_settings(chinese: bool, options: &MetadataOptions) -> Result<PmtSettings> {
    let filename = if chinese { "style-cn.yml" } else { "style.yml" };
    let path = options.bundled_style_dir.join(filename);
    if path.is_file() {
        PmtSettings::load(&path)
    } else {
        // Native binaries retain defaults when the authored YAML files are absent on disk.
        let embedded = if chinese {
            include_str!("../../../defaults/style-cn.yml")
        } else {
            include_str!("../../../defaults/style.yml")
        };
        PmtSettings::from_yaml_text(embedded, &path)
    }
}

/// Match Python's empty-value policy when determining CSL fallback behavior.
fn is_empty(value: &Value) -> bool {
    match value {
        Value::Null => true,
        Value::Bool(flag) => !flag,
        Value::Number(number) => number.as_f64() == Some(0.0),
        Value::String(value) => value.is_empty(),
        Value::Array(value) => value.is_empty(),
        Value::Object(value) => value.is_empty(),
    }
}

/// Resolve the bundled CSL only, leaving explicit custom paths and URLs untouched.
fn resolve_bundled_csl(raw: &str, roots: &[PathBuf]) -> String {
    let selected = roots
        .iter()
        .map(|root| root.join(raw))
        .find(|path| path.is_file())
        .or_else(|| roots.last().map(|root| root.join(raw)));
    selected
        .map(|path| path.to_string_lossy().replace('\\', "/"))
        .unwrap_or_else(|| raw.to_string())
}

/// Merge language, project, reply, and manuscript metadata from one source snapshot.
pub fn load_effective_metadata_text(
    text: &str,
    source: &Path,
    options: &MetadataOptions,
) -> Result<EffectiveMetadata> {
    let mut project_settings: Option<PmtSettings> = None;
    for style in options
        .style_paths
        .iter()
        .rev()
        .filter(|path| path.exists())
    {
        let current = PmtSettings::load(style)?;
        project_settings = Some(match project_settings {
            None => current,
            Some(previous) => {
                let mut merged = PmtSettings::from_mapping(&merge_metadata(
                    &previous.to_mapping(true),
                    &current.to_mapping(true),
                ))?;
                merged.pandoc_metadata =
                    merge_metadata(&previous.pandoc_metadata, &current.pandoc_metadata);
                // Reply overrides are a separate domain and must survive source-local overlays.
                merged.reply = match (&previous.reply, &current.reply) {
                    (Some(base), Some(overrides)) => Some(ReplySettings {
                        pmt_overrides: serde_json::from_value(Value::Object(merge_metadata(
                            &base.pmt_overrides.to_mapping(),
                            &overrides.pmt_overrides.to_mapping(),
                        )))?,
                        pandoc_metadata: merge_metadata(
                            &base.pandoc_metadata,
                            &overrides.pandoc_metadata,
                        ),
                    }),
                    (base, overrides) => overrides.clone().or_else(|| base.clone()),
                };
                merged
            }
        });
    }
    let normalized_text = text.replace("\r\n", "\n");
    let has_header = header_pattern().is_match(&normalized_text);
    let mut manuscript = if has_header {
        parse_yaml_header_text(&normalized_text, source)?
    } else if options.allow_missing_header {
        Map::new()
    } else {
        bail!(
            "No YAML front matter found in markdown file: {}",
            source.display()
        )
    };
    let canonical_settings = manuscript.remove("papperSettings");
    let alias_settings = manuscript.remove("papper-settings");
    // Remove both spellings from Pandoc metadata; the canonical key wins when both are supplied.
    let inline_style = canonical_settings.or(alias_settings);
    // An explicit null clears the inherited reply section, just like any mapping override.
    let clears_reply = inline_style
        .as_ref()
        .and_then(Value::as_object)
        .is_some_and(|mapping| mapping.get("reply").is_some_and(Value::is_null));
    let inline_settings = match inline_style {
        None => None,
        Some(Value::Object(mapping)) => Some(PmtSettings::from_style_mapping(
            &mapping,
            source,
            "papperSettings",
        )?),
        _ => bail!(
            "`papperSettings` in {} must be a YAML mapping",
            source.display()
        ),
    };
    // The reply path selects build behavior and must not become a Word property
    // or Pandoc template variable; recognition reads the original header separately.
    manuscript.remove("reply");
    if manuscript
        .remove("citation-number-range-delimiter")
        .is_some()
    {
        eprintln!(
            "[WARN] Ignoring deprecated manuscript metadata `citation-number-range-delimiter` in {}. Configure top-level style.yml `citationNumberRangeDelimiter` instead.",
            source.display()
        );
    }
    let mut project_metadata = project_settings
        .as_ref()
        .map(|settings| settings.pandoc_metadata.clone())
        .unwrap_or_default();
    let mut inline_metadata = inline_settings
        .as_ref()
        .map(|settings| settings.pandoc_metadata.clone())
        .unwrap_or_default();
    let mut overrides = merge_metadata(
        &merge_metadata(&project_metadata, &inline_metadata),
        &manuscript,
    );
    if overrides.get("csl").is_none_or(is_empty) {
        overrides.remove("csl");
        project_metadata.remove("csl");
        inline_metadata.remove("csl");
        manuscript.remove("csl");
    }
    let selected_language = options
        .lang_override
        .as_ref()
        .map(|language| json!(language))
        .or_else(|| overrides.get("lang").cloned())
        .unwrap_or(Value::Null);
    if let Some(language) = &options.lang_override
        && !matches!(
            language.trim().replace('_', "-").to_lowercase().as_str(),
            "zh-cn" | "zhcn"
        )
    {
        bail!("Only `--lang zh-cn` and `--lang zhcn` are currently supported for builds.")
    }
    let defaults = bundled_settings(is_chinese_language(&selected_language), options)?;
    let mut settings_mapping = defaults.to_mapping(true);
    let mut pandoc_defaults = defaults.pandoc_metadata.clone();
    let bundled_csl = pandoc_defaults.get("csl").cloned();
    if let Some(project) = &project_settings {
        settings_mapping = merge_metadata(&settings_mapping, &project.to_mapping(true));
        pandoc_defaults = merge_metadata(&pandoc_defaults, &project_metadata);
    }
    if options.reply && !clears_reply {
        for settings in [Some(&defaults), project_settings.as_ref()]
            .into_iter()
            .flatten()
        {
            if let Some(reply) = &settings.reply {
                settings_mapping =
                    merge_metadata(&settings_mapping, &reply.pmt_overrides.to_mapping());
                pandoc_defaults = merge_metadata(&pandoc_defaults, &reply.pandoc_metadata);
            }
        }
    }
    if let Some(inline) = &inline_settings {
        // Apply the manuscript layer after file-based reply styles too, so its
        // explicit values win even when the selected style contains a reply section.
        settings_mapping = merge_metadata(
            &settings_mapping,
            &provided_settings_mapping(&inline.fields, &inline.provided),
        );
        pandoc_defaults = merge_metadata(&pandoc_defaults, &inline_metadata);
        if options.reply
            && let Some(reply) = &inline.reply
        {
            settings_mapping = merge_metadata(
                &settings_mapping,
                &provided_settings_mapping(
                    &reply.pmt_overrides.fields,
                    &reply.pmt_overrides.provided,
                ),
            );
            pandoc_defaults = merge_metadata(&pandoc_defaults, &reply.pandoc_metadata);
        }
    }
    let pmt_settings = PmtSettings::from_mapping(&settings_mapping)?;
    // Project metadata was already applied before reply overrides. Reapplying it here
    // would erase reply-specific values; only the manuscript header has higher priority.
    let mut pandoc_metadata = merge_metadata(&pandoc_defaults, &manuscript);
    if let Some(Value::String(raw)) = bundled_csl
        && pandoc_metadata.get("csl") == Some(&Value::String(raw.clone()))
        && !options.resource_roots.is_empty()
    {
        pandoc_metadata.insert(
            "csl".to_string(),
            json!(resolve_bundled_csl(&raw, &options.resource_roots)),
        );
    }
    if options.lang_override.is_some() || is_chinese_language(&selected_language) {
        pandoc_metadata.insert(
            "lang".to_string(),
            normalize_pandoc_language(&selected_language),
        );
    }
    Ok(EffectiveMetadata {
        pmt_settings,
        pandoc_metadata,
        has_yaml_header: has_header,
    })
}

/// Load a manuscript once before preparing all settings from its immutable contents.
pub fn load_effective_metadata(
    path: impl AsRef<Path>,
    options: &MetadataOptions,
) -> Result<EffectiveMetadata> {
    let path = path.as_ref();
    load_effective_metadata_text(
        &fs::read_to_string(path)
            .with_context(|| format!("Cannot read manuscript: {}", path.display()))?,
        path,
        options,
    )
}

/// Write changed metadata only, preserving mtime and content for repeated builds.
pub fn write_pandoc_metadata(
    metadata: &Map<String, Value>,
    output: impl AsRef<Path>,
) -> Result<PathBuf> {
    let output = output.as_ref();
    let serialized = serde_yaml::to_string(metadata)?;
    if fs::read_to_string(output).ok().as_deref() != Some(&serialized) {
        if let Some(parent) = output
            .parent()
            .filter(|parent| !parent.as_os_str().is_empty())
        {
            fs::create_dir_all(parent)?;
        }
        fs::write(output, serialized)
            .with_context(|| format!("Cannot write metadata: {}", output.display()))?;
    }
    Ok(output.to_path_buf())
}

/// Merge standalone metadata files in their explicit command-line order.
pub fn load_metadata_files(paths: &[PathBuf]) -> Result<Map<String, Value>> {
    let mut metadata = Map::new();
    for path in paths {
        metadata = merge_metadata(&metadata, &parse_yaml_file(path)?);
    }
    Ok(metadata)
}

#[cfg(test)]
mod tests {
    use super::*;

    /// Keep manuscript fields in the Pandoc domain and preserve nested project values.
    #[test]
    fn manuscript_does_not_override_application_settings() -> Result<()> {
        let options = MetadataOptions {
            style_paths: Vec::new(),
            ..MetadataOptions::default()
        };
        let result = load_effective_metadata_text(
            "---\nmathtype: false\nnested: {left: manuscript}\n---\nBody",
            Path::new("paper.md"),
            &options,
        )?;
        assert_eq!(result.pmt_settings.get_bool("mathtype"), Some(true));
        assert_eq!(result.pandoc_metadata["mathtype"], json!(false));
        assert!(result.pandoc_metadata["linkReferences"].as_bool().unwrap());
        Ok(())
    }

    /// Preserve explicit false and normalize historical aliases before reply merging.
    #[test]
    fn reply_uses_only_supplied_fields_and_retains_false() -> Result<()> {
        let settings = PmtSettings::from_yaml_text(
            "docx-show-page-numbers: true\ndocxStyle:\n  正文文本: {firstLineIndentChars: 2, alignment: left}\nreply:\n  docxShowPageNumbers: false\n  docxStyle:\n    正文文本: {firstLineIndentChars: 0}\n",
            Path::new("style.yml"),
        )?;
        let reply = settings.for_reply()?;
        assert_eq!(reply.get_bool("docxShowPageNumbers"), Some(false));
        assert_eq!(
            reply.get("docxStyle").unwrap()["正文文本"]["firstLineIndentChars"],
            json!(0)
        );
        assert_eq!(
            reply.get("docxStyle").unwrap()["正文文本"]["alignment"],
            json!("left")
        );
        assert!(
            settings
                .reply
                .as_ref()
                .unwrap()
                .pmt_overrides
                .get("mathtype")
                .is_none()
        );
        Ok(())
    }

    /// Keep top-level alias precedence independent of YAML key insertion order.
    #[test]
    fn canonical_style_alias_wins_before_legacy_spellings() -> Result<()> {
        let settings = PmtSettings::from_yaml_text(
            "docxShowPageNumbers: true\ndocx-show-page-numbers: false\npandocMetadata: {title: Explicit}\npandoc_metadata: {title: Internal}\nreply:\n  docxShowPageNumbers: true\n  docx-show-page-numbers: false\n",
            Path::new("style.yml"),
        )?;
        assert_eq!(settings.get_bool("docxShowPageNumbers"), Some(true));
        assert_eq!(settings.pandoc_metadata["title"], json!("Explicit"));
        assert_eq!(
            settings.for_reply()?.get_bool("docxShowPageNumbers"),
            Some(false)
        );
        Ok(())
    }

    /// Respect YAML aliases and explicit keys overriding merged defaults.
    #[test]
    fn yaml_merge_preserves_nested_data_and_explicit_override() -> Result<()> {
        let metadata = parse_yaml_mapping(
            "defaults: &defaults\n  title: Default\n  authors: [一, two]\narticle:\n  <<: *defaults\n  title: Explicit\n",
            Path::new("metadata.yml"),
        )?;
        assert_eq!(metadata["article"]["title"], json!("Explicit"));
        assert_eq!(metadata["article"]["authors"], json!(["一", "two"]));
        Ok(())
    }

    /// Preserve duplicate-key overrides and distinguish quoted strings from YAML booleans.
    #[test]
    fn yaml_duplicate_keys_and_quoted_scalars_match_existing_projects() -> Result<()> {
        let metadata = parse_yaml_mapping(
            "title: First\ntitle: Final\nflags: [yes, no, ON, off, 'yes', \"off\"]\nquoted: '010'\noctal: 010\nscientific: 1e3\nfloat: 1.0e+3\ndate: 2026-10-02\n",
            Path::new("metadata.yml"),
        )?;
        assert_eq!(metadata["title"], json!("Final"));
        assert_eq!(
            metadata["flags"],
            json!([true, false, true, false, "yes", "off"])
        );
        assert_eq!(metadata["quoted"], json!("010"));
        assert_eq!(metadata["octal"], json!(8));
        assert_eq!(metadata["scientific"], json!("1e3"));
        assert_eq!(metadata["float"], json!(1000.0));
        assert_eq!(metadata["date"], json!("2026-10-02"));
        Ok(())
    }

    /// Keep earlier merge-sequence defaults stronger than later ones and explicit keys strongest.
    #[test]
    fn yaml_merge_sequence_and_quoted_merge_key_preserve_priority() -> Result<()> {
        let metadata = parse_yaml_mapping(
            "first: &first {title: First, author: Alice}\nsecond: &second {title: Second, date: Today}\narticle:\n  <<: [*first, *second]\n  author: Bob\nliteral: {'<<': keep}\n",
            Path::new("metadata.yml"),
        )?;
        assert_eq!(
            metadata["article"],
            json!({"title": "First", "date": "Today", "author": "Bob"})
        );
        assert_eq!(metadata["literal"]["<<"], json!("keep"));
        Ok(())
    }

    /// Choose Chinese bundled formatting and normalize the crossref language alias.
    #[test]
    fn chinese_language_selects_language_defaults() -> Result<()> {
        let options = MetadataOptions {
            style_paths: Vec::new(),
            ..MetadataOptions::default()
        };
        let result = load_effective_metadata_text(
            "---\nlang: zh_CN\ncsl: ''\n---\n正文",
            Path::new("paper.md"),
            &options,
        )?;
        assert_eq!(result.pandoc_metadata["lang"], json!("zh-Hans"));
        assert_eq!(result.pandoc_metadata["figureTitle"], json!("图"));
        assert_eq!(
            result.pmt_settings.get_bool("docxShowLineNumbers"),
            Some(false)
        );
        assert!(!result.pandoc_metadata["csl"].as_str().unwrap().is_empty());
        Ok(())
    }

    /// Merge local/cwd style layers and manuscript fields without resetting omitted settings.
    #[test]
    fn local_style_and_manuscript_override_only_their_own_domains() -> Result<()> {
        let directory = tempfile::tempdir()?;
        let local = directory.path().join("nested");
        fs::create_dir(&local)?;
        let cwd_style = directory.path().join("style.yml");
        let local_style = local.join("style.yml");
        fs::write(
            &cwd_style,
            "mathtype: true\ndocxShowPageNumbers: true\npandocMetadata:\n  figureTitle: Working\n  nested: {left: cwd, right: keep}\n  custom-list: [一, two]\n",
        )?;
        fs::write(
            &local_style,
            "mathtype: false\npandocMetadata:\n  figureTitle: Local\n  nested: {left: local}\n",
        )?;
        let options = MetadataOptions {
            style_paths: vec![local_style, cwd_style],
            ..MetadataOptions::default()
        };
        let result = load_effective_metadata_text(
            "---\nmathtype: true\nfigureTitle: Manuscript\nnested: {left: manuscript}\n---\nBody",
            &local.join("paper.md"),
            &options,
        )?;
        assert_eq!(result.pmt_settings.get_bool("mathtype"), Some(false));
        assert_eq!(
            result.pmt_settings.get_bool("docxShowPageNumbers"),
            Some(true)
        );
        assert_eq!(result.pandoc_metadata["figureTitle"], json!("Manuscript"));
        assert_eq!(
            result.pandoc_metadata["nested"],
            json!({"left": "manuscript", "right": "keep"})
        );
        assert_eq!(result.pandoc_metadata["custom-list"], json!(["一", "two"]));
        Ok(())
    }

    /// Catch lost nested fields, nullable resets, and file reply styles masking manuscript overrides.
    #[test]
    fn manuscript_settings_overrides_files_and_preserves_unspecified_fields() -> Result<()> {
        let directory = tempfile::tempdir()?;
        let style = directory.path().join("style.yml");
        fs::write(
            &style,
            "mathtype: true\ndocxShowPageNumbers: true\ndocxSvgToPngWidth: 640\ndocxPageMargins: {left: 2cm, right: 3cm}\ndocxStyle:\n  Normal:\n    fontSize: 12pt\n    paragraphSpacing: {before: 6pt, after: 12pt}\npandocMetadata:\n  nested: {left: file, right: keep}\n  keywords: [file, inherited]\nreply:\n  mathtype: true\n  docxShowPageNumbers: true\n  docxStyle:\n    Normal: {alignment: right}\n  pandocMetadata:\n    nested: {left: reply, replyOnly: true}\n",
        )?;
        let text = "---\ntitle: Manuscript\npapperSettings:\n  mathtype: false\n  docx-show-page-numbers: null\n  docxSvgToPngWidth: null\n  docxSvgToPngScale: 2\n  docxPageMargins: {left: 1cm}\n  docxStyle:\n    Normal:\n      alignment: left\n      paragraphSpacing: {after: 0pt}\n  pandocMetadata:\n    title: Inline\n    nested: {left: inline}\n    keywords: [inline]\n  reply:\n    tableAutofit: content\n    pandocMetadata:\n      replyLabel: Inline reply\n---\nBody";
        for (settings_key, reply) in [
            ("papperSettings", false),
            ("papperSettings", true),
            ("papper-settings", false),
            ("papper-settings", true),
        ] {
            let text = text.replace("papperSettings", settings_key);
            let result = load_effective_metadata_text(
                &text,
                &directory.path().join("paper.md"),
                &MetadataOptions {
                    style_paths: vec![style.clone()],
                    reply,
                    ..MetadataOptions::default()
                },
            )?;
            assert_eq!(result.pmt_settings.get_bool("mathtype"), Some(false));
            assert_eq!(result.pmt_settings.get_bool("docxShowPageNumbers"), None);
            assert_eq!(
                result.pmt_settings.get("docxSvgToPngWidth"),
                Some(Value::Null)
            );
            assert_eq!(
                result.pmt_settings.get("docxSvgToPngScale"),
                Some(json!(2.0))
            );
            let margins = result.pmt_settings.get("docxPageMargins").unwrap();
            assert_eq!(margins["left"], json!("1cm"));
            assert_eq!(margins["right"], json!("3cm"));
            let styles = result.pmt_settings.get("docxStyle").unwrap();
            assert_eq!(styles["Normal"]["fontSize"], json!("12pt"));
            assert_eq!(styles["Normal"]["alignment"], json!("left"));
            assert_eq!(styles["Normal"]["paragraphSpacing"]["before"], json!("6pt"));
            assert_eq!(styles["Normal"]["paragraphSpacing"]["after"], json!("0pt"));
            assert_eq!(result.pandoc_metadata["title"], json!("Manuscript"));
            assert_eq!(result.pandoc_metadata["nested"]["left"], json!("inline"));
            assert_eq!(result.pandoc_metadata["nested"]["right"], json!("keep"));
            assert_eq!(result.pandoc_metadata["keywords"], json!(["inline"]));
            assert!(!result.pandoc_metadata.contains_key("papperSettings"));
            assert!(!result.pandoc_metadata.contains_key("papper-settings"));
            assert_eq!(result.pandoc_metadata.contains_key("replyLabel"), reply);
            if reply {
                assert_eq!(
                    result.pmt_settings.get("tableAutofit"),
                    Some(json!("content"))
                );
                assert_eq!(result.pandoc_metadata["nested"]["replyOnly"], json!(true));
            }
        }
        Ok(())
    }

    /// Keep canonical header precedence independent of key order and exclude both spellings from output.
    #[test]
    fn manuscript_settings_canonical_key_wins_over_alias() -> Result<()> {
        let options = MetadataOptions {
            style_paths: Vec::new(),
            ..MetadataOptions::default()
        };
        let canonical = "papperSettings: {mathtype: false, pandocMetadata: {title: Canonical}}";
        let alias = "papper-settings: {mathtype: true, pandocMetadata: {title: Alias}}";
        for header in [
            format!("{canonical}\n{alias}"),
            format!("{alias}\n{canonical}"),
        ] {
            let result = load_effective_metadata_text(
                &format!("---\n{header}\n---\nBody"),
                Path::new("paper.md"),
                &options,
            )?;
            assert_eq!(result.pmt_settings.get_bool("mathtype"), Some(false));
            assert_eq!(result.pandoc_metadata["title"], json!("Canonical"));
            assert!(!result.pandoc_metadata.contains_key("papperSettings"));
            assert!(!result.pandoc_metadata.contains_key("papper-settings"));
        }
        Ok(())
    }

    /// Clear inherited reply configuration and nullable reply fields without discarding other fields.
    #[test]
    fn manuscript_style_reply_nulls_clear_inherited_values() -> Result<()> {
        let directory = tempfile::tempdir()?;
        let style = directory.path().join("style.yml");
        fs::write(
            &style,
            "docxShowPageNumbers: true\npandocMetadata: {subtitle: Base}\nreply:\n  docxShowPageNumbers: false\n  pandocMetadata: {subtitle: Reply}\n",
        )?;
        let options = MetadataOptions {
            style_paths: vec![style],
            reply: true,
            ..MetadataOptions::default()
        };
        for (reply_style, page_numbers, subtitle) in [
            ("null", Some(true), "Base"),
            ("{docxShowPageNumbers: null}", None, "Reply"),
        ] {
            let result = load_effective_metadata_text(
                &format!("---\npapperSettings:\n  reply: {reply_style}\n---\nBody"),
                &directory.path().join("paper.md"),
                &options,
            )?;
            assert_eq!(
                result.pmt_settings.get_bool("docxShowPageNumbers"),
                page_numbers
            );
            assert_eq!(result.pandoc_metadata["subtitle"], json!(subtitle));
        }
        Ok(())
    }

    /// Keep standalone inline styles functional, with manuscript-relative fonts and language defaults.
    #[test]
    fn manuscript_style_selects_language_and_resolves_fonts_beside_markdown() -> Result<()> {
        let directory = tempfile::tempdir()?;
        let result = load_effective_metadata_text(
            "---\npapperSettings:\n  mathtypeTypstMathFont:\n    font: fonts/body.otf\n    calligraphicFont: fonts/script.ttf\n  pandocMetadata: {lang: zh-CN, csl: ''}\n---\nBody",
            &directory.path().join("paper.md"),
            &MetadataOptions {
                style_paths: Vec::new(),
                ..MetadataOptions::default()
            },
        )?;
        assert_eq!(result.pandoc_metadata["lang"], json!("zh-Hans"));
        assert_eq!(result.pandoc_metadata["figureTitle"], json!("图"));
        assert!(!result.pandoc_metadata["csl"].as_str().unwrap().is_empty());
        let fonts = &result.pmt_settings.fields().mathtype_typst_math_font;
        assert_eq!(
            Path::new(&fonts.font),
            directory.path().join("fonts/body.otf")
        );
        assert_eq!(
            Path::new(&fonts.calligraphic_font),
            directory.path().join("fonts/script.ttf")
        );
        Ok(())
    }

    /// Reject malformed inline styles and mutually exclusive controls introduced by merging layers.
    #[test]
    fn manuscript_style_reports_invalid_configuration() {
        let directory = tempfile::tempdir().unwrap();
        let style = directory.path().join("style.yml");
        fs::write(&style, "docxSvgToPngWidth: 640\n").unwrap();
        let options = MetadataOptions {
            style_paths: vec![style],
            ..MetadataOptions::default()
        };
        for (value, expected) in [
            ("false", "must be a YAML mapping"),
            ("null", "must be a YAML mapping"),
            ("[one, two]", "must be a YAML mapping"),
            ("{mathtype: invalid}", "must be a valid boolean"),
            ("{pandocMetadata: []}", "must be a YAML mapping"),
            ("{docxSvgToPngScale: 2}", "Only one of"),
        ] {
            let error = load_effective_metadata_text(
                &format!("---\npapperSettings: {value}\n---\nBody"),
                &directory.path().join("paper.md"),
                &options,
            )
            .unwrap_err();
            assert!(format!("{error:#}").contains(expected), "{error:#}");
        }
    }

    /// Keep explicit false/null precedence intact when effective settings cross a JSON boundary.
    #[test]
    fn persisted_settings_preserve_explicit_presence_and_reply_overrides() -> Result<()> {
        let settings = PmtSettings::from_yaml_text(
            "mathtype: false\ndocxShowPageNumbers: null\nreply:\n  mathtype: true\n  docxShowPageNumbers: false\n",
            Path::new("style.yml"),
        )?;
        let restored: PmtSettings = serde_json::from_value(serde_json::to_value(&settings)?)?;
        assert!(!restored.fields().mathtype);
        assert!(restored.was_provided("mathtype"));
        assert!(restored.was_provided("docxShowPageNumbers"));
        assert_eq!(restored.fields().docx_show_page_numbers, None);
        assert!(!restored.was_provided("docxNativeCrossref"));
        let reply = restored.for_reply()?;
        assert!(reply.fields().mathtype);
        assert_eq!(reply.fields().docx_show_page_numbers, Some(false));
        Ok(())
    }

    /// Reject invalid persisted configuration that previously bypassed project-file validation.
    #[test]
    fn persisted_settings_reject_invalid_configuration() -> Result<()> {
        let settings = PmtSettings::default();
        for invalid in [
            json!({"docxSvgToPngDpi": 0}),
            json!({"mathtype": []}),
            json!({"mathtypeConversionMethod": "unknown"}),
            json!({"docxSvgToPngDpi": 300, "docxSvgToPngWidth": 600}),
        ] {
            let mut wire = serde_json::to_value(&settings)?;
            wire["values"]
                .as_object_mut()
                .unwrap()
                .extend(invalid.as_object().unwrap().clone());
            assert!(serde_json::from_value::<PmtSettings>(wire).is_err());
        }
        Ok(())
    }

    /// Preserve both style-relative font paths and reply overrides through effective metadata serialization.
    #[test]
    fn math_font_objects_resolve_both_paths_and_survive_roundtrip() -> Result<()> {
        let directory = tempfile::tempdir()?;
        let source = directory.path().join("style.yml");
        let text = "mathtypeTypstMathFont:\n  font: fonts/body.otf\n  calligraphicFont: fonts/script.ttf\nreply:\n  mathtypeTypstMathFont:\n    font: fonts/reply.otf\n    calligraphicFont: New Computer Modern Math\n";
        let settings = PmtSettings::from_yaml_text(text, &source)?;
        let restored: PmtSettings = serde_json::from_value(serde_json::to_value(&settings)?)?;
        let fonts = &restored.fields().mathtype_typst_math_font;
        assert_eq!(
            Path::new(&fonts.font),
            directory.path().join("fonts/body.otf")
        );
        assert_eq!(
            Path::new(&fonts.calligraphic_font),
            directory.path().join("fonts/script.ttf")
        );
        let reply = restored.for_reply()?;
        assert_eq!(
            Path::new(&reply.fields().mathtype_typst_math_font.font),
            directory.path().join("fonts/reply.otf")
        );
        assert_eq!(
            reply.fields().mathtype_typst_math_font.calligraphic_font,
            "New Computer Modern Math"
        );
        let string = PmtSettings::from_yaml_text("mathtypeTypstMathFont: ' XITS Math '", &source)?;
        let object = PmtSettings::from_yaml_text(
            "mathtypeTypstMathFont: {font: XITS Math, calligraphicFont: XITS Math}",
            &source,
        )?;
        assert_eq!(
            string.fields().mathtype_typst_math_font,
            object.fields().mathtype_typst_math_font
        );
        Ok(())
    }

    /// Reject incomplete or misspelled font objects rather than silently changing rendered glyphs.
    #[test]
    fn math_font_configuration_rejects_invalid_objects() {
        for font in [
            json!({"font": "XITS Math"}),
            json!({"calligraphicFont": "New Computer Modern Math"}),
            json!({"font": "XITS Math", "calligraphicFont": " "}),
            json!({"font": 12, "calligraphicFont": "XITS Math"}),
            json!({"font": "XITS Math", "calligraphicFont": "XITS Math", "scriptFont": "XITS Math"}),
            json!(null),
            json!(false),
        ] {
            assert!(
                PmtSettings::from_mapping(
                    json!({"mathtypeTypstMathFont": font}).as_object().unwrap()
                )
                .is_err()
            );
        }
    }
}
