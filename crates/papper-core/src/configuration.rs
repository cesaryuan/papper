//! Export editor configuration contracts without loading a document engine or project.
//!
//! Field ownership and canonical enums come from the runtime configuration types.
//! Concise editor help lives beside this module; authored YAML resources supply
//! project examples and language defaults, independently of schema suggestions.

use anyhow::{Context, Result};
use serde_json::{Map, Value, json};

use crate::metadata::{
    ConversionMethod, FooterAlignment, SettingField, SvgBackend, TableAutofit, canonical_field_name,
};

/// Build an object schema, allowing extensible Pandoc and Word-style mappings.
fn object(properties: Value, additional: Value) -> Value {
    json!({"type": "object", "properties": properties, "additionalProperties": additional})
}

/// Convert concise help into JSON Schema annotations, keeping suggestions distinct from enums.
fn help_schema(help: &Value) -> Value {
    let mut schema = json!({"description": help["description"]});
    if let Some(children) = help.get("children").and_then(Value::as_object) {
        let properties: Map<String, Value> = children
            .iter()
            .map(|(key, value)| (key.clone(), help_schema(value)))
            .collect();
        schema["type"] = json!("object");
        schema["properties"] = json!(properties);
        schema["additionalProperties"] = json!(true);
    } else {
        schema["type"] = json!("string");
    }
    // Explicit help types describe open input forms such as physical lengths and colors.
    if let Some(value_type) = help.get("type") {
        schema["type"] = value_type.clone();
    }
    if let Some(values) = help.get("values").and_then(Value::as_array) {
        if values == &vec![json!("true"), json!("false")] {
            if help.get("type").is_none() {
                schema["type"] = json!("boolean");
            }
        } else {
            schema["x-papper-suggestions"] = json!(values);
        }
    }
    if help["sequenceItems"] == true {
        let item = schema.clone();
        schema = json!({"description": help["description"], "type": help.get("type").cloned().unwrap_or(json!("array")), "items": item});
    }
    if let Some(description) = help.get("dynamicValueDescription") {
        schema["additionalProperties"] = json!({"type": "string", "description": description});
        schema["type"] = json!("object");
    }
    schema
}

/// Render documented fields without inferring a closed set of legal Pandoc metadata.
fn help_properties(help: &Value) -> Result<Map<String, Value>> {
    Ok(help
        .as_object()
        .context("Configuration help must be an object")?
        .iter()
        .map(|(key, value)| (key.clone(), help_schema(value)))
        .collect())
}

/// Derive common spelling aliases only when the production parser accepts them.
fn aliases(field: SettingField) -> Vec<String> {
    let mut kebab = String::new();
    for character in field.name().chars() {
        if character.is_uppercase() {
            kebab.push('-');
            kebab.extend(character.to_lowercase());
        } else {
            kebab.push(character);
        }
    }
    let mut candidates = vec![kebab.clone(), kebab.replace('-', "_")];
    candidates.extend(
        [
            "embedSvgImages",
            "embed-svg-images",
            "convertSvgToPng",
            "convert-svg-to-png",
            "show-line-numbers",
            "showLineNumbers",
            "show_line_numbers",
            "show-page-numbers",
            "showPageNumbers",
            "show_page_numbers",
        ]
        .into_iter()
        .map(str::to_owned),
    );
    candidates
        .retain(|alias| alias != field.name() && canonical_field_name(alias) == Some(field.name()));
    candidates.sort();
    candidates.dedup();
    candidates
}

/// Describe each owned field exhaustively so a new runtime field requires an export decision.
fn setting_schema(field: SettingField, help: &Value, text_style: &Value) -> Value {
    let mut schema = help_schema(help);
    let (section, targets) = match field {
        SettingField::Mathtype
        | SettingField::MathtypeConversionMethod
        | SettingField::MathtypeSvgBackend
        | SettingField::MathtypeTypstMathFont => ("math", vec!["docx"]),
        SettingField::TableAutofit => ("tables", vec!["html", "docx"]),
        SettingField::DocxEmbedSvgImages
        | SettingField::DocxConvertSvgToPng
        | SettingField::DocxSvgToPngWidth
        | SettingField::DocxSvgToPngDpi
        | SettingField::DocxSvgToPngScale => ("images", vec!["docx"]),
        SettingField::DocxNativeCrossref => ("references", vec!["docx"]),
        SettingField::CitationNumberRangeDelimiter => {
            ("bibliography", vec!["html", "docx", "latex"])
        }
        SettingField::DocxStyle => ("docx", vec!["html", "docx"]),
        SettingField::DocxShowLineNumbers
        | SettingField::DocxShowPageNumbers
        | SettingField::DocxFooter
        | SettingField::DocxPageMargins
        | SettingField::DocxPageWidth => ("layout", vec!["docx"]),
    };
    schema["x-papper-section"] = json!(section);
    schema["x-papper-targets"] = json!(targets);
    schema["x-papper-aliases"] = json!(aliases(field));
    match field {
        SettingField::Mathtype
        | SettingField::DocxNativeCrossref
        | SettingField::DocxEmbedSvgImages
        | SettingField::DocxConvertSvgToPng => {
            schema["type"] = json!("boolean");
        }
        SettingField::MathtypeConversionMethod => {
            schema["enum"] = json!([
                ConversionMethod::Rust,
                ConversionMethod::RustSdk,
                ConversionMethod::Mathtype,
                ConversionMethod::Auto,
                ConversionMethod::Both,
            ]);
            schema["x-papper-value-aliases"] = json!({
                "set-data": "mathtype", "setdata": "mathtype", "set_data": "mathtype",
                "sdk": "rust-sdk", "sdk-xform-ole": "rust-sdk"
            });
        }
        SettingField::MathtypeSvgBackend => {
            schema["enum"] = json!([SvgBackend::Ratex, SvgBackend::Typst])
        }
        SettingField::TableAutofit => {
            schema["enum"] = json!([
                TableAutofit::Window,
                TableAutofit::Content,
                TableAutofit::Fixed,
                TableAutofit::None,
            ])
        }
        SettingField::MathtypeTypstMathFont => {
            schema["type"] = json!(["string", "object"]);
            schema["required"] = json!(["font", "calligraphicFont"]);
            schema["additionalProperties"] = json!(false);
            schema["minLength"] = json!(1);
            schema["properties"]["font"]["minLength"] = json!(1);
            schema["properties"]["calligraphicFont"]["minLength"] = json!(1);
        }
        SettingField::DocxSvgToPngWidth
        | SettingField::DocxSvgToPngDpi
        | SettingField::DocxSvgToPngScale => {
            schema["type"] = json!(if field == SettingField::DocxSvgToPngWidth {
                "integer"
            } else {
                "number"
            });
            schema["exclusiveMinimum"] = json!(0);
        }
        SettingField::CitationNumberRangeDelimiter => schema["type"] = json!(["string", "null"]),
        SettingField::DocxShowLineNumbers => schema["type"] = json!(["boolean", "string"]),
        SettingField::DocxShowPageNumbers => schema["type"] = json!(["boolean", "null"]),
        SettingField::DocxFooter => {
            schema["type"] = json!(["object", "null"]);
            schema["additionalProperties"] = json!(false);
            schema["properties"]["alignment"]["enum"] = json!([
                FooterAlignment::Left,
                FooterAlignment::Center,
                FooterAlignment::Right,
            ]);
        }
        SettingField::DocxPageMargins => schema["type"] = json!(["object", "null"]),
        SettingField::DocxPageWidth => schema["type"] = json!(["string", "number", "null"]),
        SettingField::DocxStyle => {
            schema["type"] = json!(["object", "null"]);
            schema["additionalProperties"] = text_style.clone();
        }
    }
    schema
}

/// Export a versioned, self-contained editor contract with raw templates and language defaults.
pub fn describe_configuration(version: &str) -> Result<Value> {
    let help: Value = serde_json::from_str(include_str!("configuration_help.json"))?;
    let mut text_properties = help_properties(&help["docxTextStyle"])?;
    text_properties.get_mut("fontFamily").unwrap()["type"] = json!(["string", "object"]);
    for name in ["fontSize", "lineSpacing", "firstLineIndentChars"] {
        text_properties.get_mut(name).unwrap()["type"] = json!(["string", "number"]);
    }
    text_properties.get_mut("fontColor").unwrap()["type"] = json!(["string", "array"]);
    let text_style = object(json!(text_properties), json!(true));
    let mut settings = Map::new();
    for field in SettingField::ALL {
        let field_help = help["settings"]
            .get(field.name())
            .with_context(|| format!("Missing editor help for {}", field.name()))?;
        settings.insert(
            field.name().into(),
            setting_schema(field, field_help, &text_style),
        );
    }
    let mut metadata = help_properties(&help["pandocMetadata"])?;
    for (key, schema) in &mut metadata {
        schema["x-papper-section"] = json!(if ["csl", "reference-section-title", "link-citations"]
            .contains(&key.as_str())
        {
            "bibliography"
        } else {
            "references"
        });
    }
    for name in ["figPrefix", "tblPrefix", "secPrefix", "eqnPrefix"] {
        metadata.get_mut(name).unwrap()["type"] = json!(["string", "array"]);
    }
    for name in ["sectionsDepth", "chaptersDepth"] {
        metadata.get_mut(name).unwrap()["type"] = json!("integer");
    }
    let mut metadata_schema = object(json!(metadata), json!(true));
    metadata_schema["description"] =
        json!("传给 Pandoc、pandoc-crossref 和 citeproc 的元数据；允许其他合法元数据");
    settings.insert("pandocMetadata".into(), metadata_schema.clone());
    let reply = object(json!(settings), json!(true));
    settings.insert("reply".into(), json!({"description": "审稿回复构建的覆盖配置；null 清除继承的回复配置", "type": ["object", "null"], "properties": reply["properties"], "additionalProperties": true}));
    let mut style = object(json!(settings), json!(true));
    style["description"] = json!(
        "本稿件的格式与构建覆盖配置，高于 style.yml；相对字体文件路径以当前 Markdown 所在目录为基准"
    );
    let mut manuscript = metadata;
    manuscript.extend(help_properties(&help["manuscriptFrontmatter"])?);
    manuscript.insert("papperSettings".into(), style.clone());
    for name in ["keywords", "classoption"] {
        manuscript.get_mut(name).unwrap()["type"] = json!("array");
    }
    manuscript.get_mut("bibliography").unwrap()["type"] = json!(["string", "array"]);
    for name in ["authors", "author"] {
        manuscript.get_mut(name).unwrap()["items"]["required"] = json!(["name"]);
    }
    let standard: Value = serde_yaml::from_str(include_str!("../../../defaults/style.yml"))?;
    let chinese: Value = serde_yaml::from_str(include_str!("../../../defaults/style-cn.yml"))?;
    Ok(json!({
        "contractVersion": 1,
        "papperVersion": version,
        "capabilities": ["config.describe"],
        "schemas": {"style": style, "pandocMetadata": metadata_schema,
            "docxTextStyle": text_style, "manuscriptFrontmatter": object(json!(manuscript), json!(true))},
        "rules": [
            {"scope": "style", "kind": "atMostOne", "paths": [["docxSvgToPngWidth"], ["docxSvgToPngDpi"], ["docxSvgToPngScale"]]},
            {"scope": "docxTextStyle", "kind": "atMostOne", "paths": [["firstLineIndentChars"], ["indentation", "firstLine"], ["indentation", "hanging"]]}
        ],
        "templates": {"styleProject": include_str!("../../../template/style-project.yml")},
        "defaults": {"standard": standard, "chinese": chinese}
    }))
}
