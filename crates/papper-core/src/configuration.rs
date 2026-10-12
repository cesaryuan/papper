//! Export editor contracts from runtime definitions, with prose kept in typed help data.
//!
//! Domain modules own names, shapes, enums and constraints. This module assembles
//! their editor representation and includes authored templates and language defaults.

use anyhow::{Context, Result};
use serde::Deserialize;
use serde_json::{Map, Value, json};
use std::collections::BTreeMap;

use crate::metadata::{
    ConversionMethod, FooterAlignment, PositiveNumber, SVG_SIZING_FIELDS, SettingField,
    SettingShape, SvgBackend, TableAutofit,
};
use crate::page_margins::PAGE_MARGIN_SIDES;
use crate::style_properties::{STYLE_PROPERTIES, StyleProperty};
use crate::style_values::FirstLineIndentKind;

/// Keep editor prose and examples separate from runtime field contracts.
#[derive(Deserialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
struct EditorHelp {
    settings: BTreeMap<String, FieldHelp>,
    pandoc_metadata: BTreeMap<String, FieldHelp>,
    docx_text_style: BTreeMap<String, FieldHelp>,
    manuscript_frontmatter: BTreeMap<String, FieldHelp>,
}

/// Annotate a property without specifying validation types or enumerated values.
#[derive(Deserialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
struct FieldHelp {
    description: String,
    #[serde(default)]
    suggestions: Vec<String>,
    #[serde(default)]
    children: BTreeMap<String, FieldHelp>,
    item_description: Option<String>,
}

impl FieldHelp {
    /// Create text annotations; suggestions remain examples rather than closed enums.
    fn annotations(&self) -> Value {
        let mut schema = json!({"description": self.description});
        if !self.suggestions.is_empty() {
            schema["x-papper-suggestions"] = json!(self.suggestions);
        }
        schema
    }
}

/// Build an object schema with explicit extension policy.
fn object(properties: Map<String, Value>, additional: Value) -> Value {
    json!({"type": "object", "properties": properties, "additionalProperties": additional})
}

/// Render a single type compactly while retaining unions as JSON Schema arrays.
fn schema_types(types: &[&str]) -> Value {
    if types.len() == 1 {
        json!(types[0])
    } else {
        json!(types)
    }
}

/// Resolve required documentation with a useful error instead of indexing or unwrapping.
fn field_help<'a>(help: &'a BTreeMap<String, FieldHelp>, name: &str) -> Result<&'a FieldHelp> {
    help.get(name)
        .with_context(|| format!("Missing editor help for {name}"))
}

/// Render canonical typography properties recursively from the runtime property definitions.
fn typography_properties(
    properties: &[StyleProperty],
    help: &BTreeMap<String, FieldHelp>,
) -> Result<Map<String, Value>> {
    properties
        .iter()
        .map(|property| {
            let help = field_help(help, property.name)?;
            let mut schema = help.annotations();
            schema["type"] = schema_types(property.types);
            if !property.children.is_empty() {
                schema["properties"] =
                    json!(typography_properties(property.children, &help.children)?);
                schema["additionalProperties"] = json!(false);
            }
            Ok((property.name.into(), schema))
        })
        .collect()
}

/// Describe setting groups exhaustively so each new runtime field needs an editor placement.
fn setting_schema(field: SettingField, help: &FieldHelp, text_style: &Value) -> Result<Value> {
    let mut schema = help.annotations();
    schema["type"] = schema_types(field.shape().types());
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
        | SettingField::DocxHeader
        | SettingField::DocxPageMargins
        | SettingField::DocxPageWidth => ("layout", vec!["docx"]),
    };
    schema["x-papper-section"] = json!(section);
    schema["x-papper-targets"] = json!(targets);
    if let SettingShape::PositiveNumber(_) = field.shape() {
        schema["exclusiveMinimum"] = json!(PositiveNumber::MINIMUM);
    }
    match field {
        SettingField::MathtypeConversionMethod => {
            schema["enum"] = json!(ConversionMethod::ALL);
            let aliases: BTreeMap<_, _> = ConversionMethod::ALL
                .into_iter()
                .flat_map(|method| {
                    method
                        .aliases()
                        .iter()
                        .map(move |alias| (*alias, method.as_str()))
                })
                .collect();
            schema["x-papper-value-aliases"] = json!(aliases);
        }
        SettingField::MathtypeSvgBackend => schema["enum"] = json!(SvgBackend::ALL),
        SettingField::TableAutofit => schema["enum"] = json!(TableAutofit::ALL),
        SettingField::MathtypeTypstMathFont => {
            let mut properties = Map::new();
            for name in crate::metadata::MathFontConfig::FIELD_NAMES {
                let mut child = field_help(&help.children, name)?.annotations();
                child["type"] = json!("string");
                child["minLength"] = json!(1);
                properties.insert(name.into(), child);
            }
            schema["properties"] = json!(properties);
            schema["required"] = json!(crate::metadata::MathFontConfig::FIELD_NAMES);
            schema["additionalProperties"] = json!(false);
            schema["minLength"] = json!(1);
        }
        SettingField::DocxFooter | SettingField::DocxHeader => {
            let mut properties = Map::new();
            for name in crate::metadata::DocxFooter::FIELD_NAMES {
                let mut child = field_help(&help.children, name)?.annotations();
                child["type"] = json!("string");
                if name == "alignment" {
                    child["enum"] = json!(FooterAlignment::ALL);
                }
                properties.insert(name.into(), child);
            }
            schema["properties"] = json!(properties);
            schema["additionalProperties"] = json!(false);
        }
        SettingField::DocxPageMargins => {
            let mut properties = Map::new();
            for side in PAGE_MARGIN_SIDES {
                let mut child = field_help(&help.children, side)?.annotations();
                child["type"] = json!(["string", "number"]);
                properties.insert(side.into(), child);
            }
            schema["properties"] = json!(properties);
            schema["additionalProperties"] = json!(false);
        }
        SettingField::DocxStyle => schema["additionalProperties"] = text_style.clone(),
        _ => {}
    }
    Ok(schema)
}

/// Describe known Pandoc fields while allowing other metadata owned by Pandoc and its filters.
fn metadata_properties(help: &BTreeMap<String, FieldHelp>) -> Map<String, Value> {
    help.iter()
        .map(|(name, help)| {
            let mut schema = help.annotations();
            schema["type"] = match name.as_str() {
                "figPrefix" | "tblPrefix" | "secPrefix" | "eqnPrefix" => json!(["string", "array"]),
                "sectionsDepth" | "chaptersDepth" => json!("integer"),
                "linkReferences" | "autoSectionLabels" | "autoEqnLabels" | "numberSections"
                | "chapters" | "subfigGrid" | "link-citations" => json!("boolean"),
                _ => json!("string"),
            };
            schema["x-papper-section"] =
                json!(if ["csl", "reference-section-title", "link-citations"]
                    .contains(&name.as_str())
                {
                    "bibliography"
                } else {
                    "references"
                });
            (name.clone(), schema)
        })
        .collect()
}

/// Describe manuscript metadata input, including author sequences and shared affiliation maps.
fn manuscript_properties(help: &BTreeMap<String, FieldHelp>) -> Map<String, Value> {
    help.iter()
        .map(|(name, help)| {
            let mut schema = help.annotations();
            schema["type"] = match name.as_str() {
                "keywords" | "classoption" | "authors" | "author" => json!("array"),
                "bibliography" => json!(["string", "array"]),
                "toc" | "lof" | "lot" => json!("boolean"),
                "affiliations" | "affiliation" => json!("object"),
                _ => json!("string"),
            };
            if !help.children.is_empty() {
                let properties = help
                    .children
                    .iter()
                    .map(|(name, help)| {
                        let mut child = help.annotations();
                        child["type"] = match name.as_str() {
                            "corresponding" => json!(["boolean", "string"]),
                            "affiliation" | "affiliations" => json!(["string", "array"]),
                            _ => json!("string"),
                        };
                        (name.clone(), child)
                    })
                    .collect();
                let mut item = object(properties, json!(true));
                item["required"] = json!(["name"]);
                schema["items"] = item;
            }
            if let Some(description) = &help.item_description {
                schema["additionalProperties"] =
                    json!({"type": "string", "description": description});
            }
            (name.clone(), schema)
        })
        .collect()
}

/// Export a self-contained editor contract without starting a build or reading project files.
pub fn describe_configuration(version: &str) -> Result<Value> {
    let help: EditorHelp = serde_json::from_str(include_str!("configuration_help.json"))?;
    let text_style = object(
        typography_properties(STYLE_PROPERTIES, &help.docx_text_style)?,
        json!(false),
    );
    let mut settings = Map::new();
    for field in SettingField::ALL {
        settings.insert(
            field.name().into(),
            setting_schema(
                field,
                field_help(&help.settings, field.name())?,
                &text_style,
            )?,
        );
    }
    let metadata = metadata_properties(&help.pandoc_metadata);
    let mut metadata_schema = object(metadata.clone(), json!(true));
    metadata_schema["description"] =
        json!("传给 Pandoc、pandoc-crossref 和 citeproc 的元数据；允许其他合法元数据");
    settings.insert("pandocMetadata".into(), metadata_schema.clone());
    let mut reply = object(settings.clone(), json!(false));
    reply["type"] = json!(["object", "null"]);
    reply["description"] = json!("审稿回复构建的覆盖配置；null 清除继承的回复配置");
    settings.insert("reply".into(), reply);
    let mut style = object(settings, json!(false));
    style["description"] = json!(
        "本稿件的格式与构建覆盖配置，高于 style.yml；相对字体文件路径以当前 Markdown 所在目录为基准"
    );
    let mut manuscript = metadata;
    manuscript.extend(manuscript_properties(&help.manuscript_frontmatter));
    manuscript.insert("papperSettings".into(), style.clone());
    let standard: Value = serde_yaml::from_str(include_str!("../../../defaults/style.yml"))?;
    let chinese: Value = serde_yaml::from_str(include_str!("../../../defaults/style-cn.yml"))?;
    Ok(json!({
        "contractVersion": 1, "papperVersion": version, "capabilities": ["config.describe", "elementAttributes"],
        "elementAttributes": crate::element_attributes::describe_attributes()?,
        "schemas": {"style": style, "pandocMetadata": metadata_schema, "docxTextStyle": text_style,
            "manuscriptFrontmatter": object(manuscript, json!(true))},
        "rules": [
            {"scope": "style", "kind": "atMostOne", "paths": SVG_SIZING_FIELDS.map(|field| [field.name()])},
            {"scope": "docxTextStyle", "kind": "atMostOne", "paths": FirstLineIndentKind::ALL.map(FirstLineIndentKind::path)}
        ],
        "templates": {"styleProject": include_str!("../../../template/style-project.yml")},
        "defaults": {"standard": standard, "chinese": chinese}
    }))
}
