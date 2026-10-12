//! Shared manuscript attribute definitions for Lua validation and editor integrations.

use anyhow::Result;
use serde::{Deserialize, Serialize};
use std::collections::BTreeMap;

/// Describe an attribute's authored string value, suggestions, and accepted spellings.
#[derive(Debug, Deserialize, Serialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub struct Attribute {
    pub description: String,
    pub value_type: AttributeType,
    #[serde(default, skip_serializing_if = "Vec::is_empty")]
    pub values: Vec<String>,
    #[serde(default, skip_serializing_if = "Vec::is_empty")]
    pub suggestions: Vec<String>,
    #[serde(default, skip_serializing_if = "Vec::is_empty")]
    pub aliases: Vec<String>,
    #[serde(default, skip_serializing_if = "Vec::is_empty")]
    pub targets: Vec<String>,
    #[serde(default, skip_serializing_if = "is_false")]
    pub docx_metadata: bool,
}

/// Classify authoring values without treating free-form examples as an exhaustive enum.
#[derive(Debug, Deserialize, Serialize)]
#[serde(rename_all = "camelCase")]
pub enum AttributeType {
    String,
    Boolean,
    Integer,
    Length,
}

/// Describe one discriminator value and the properties accepted for that element.
#[derive(Debug, Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
pub struct Variant {
    pub description: String,
    #[serde(default, skip_serializing_if = "Vec::is_empty")]
    pub aliases: Vec<String>,
    pub properties: BTreeMap<String, Attribute>,
}

/// Describe a Markdown attribute context, optionally specialized by a discriminator.
#[derive(Debug, Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
pub struct Element {
    pub description: String,
    pub targets: Vec<String>,
    pub properties: BTreeMap<String, Attribute>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub discriminator: Option<String>,
    #[serde(default, skip_serializing_if = "BTreeMap::is_empty")]
    pub variants: BTreeMap<String, Variant>,
}

/// Omit the internal table transport flag when it does not apply to an attribute.
fn is_false(value: &bool) -> bool {
    !value
}

/// Embed the same catalog that the shipped Lua filters read at runtime.
pub fn describe_attributes() -> Result<BTreeMap<String, Element>> {
    Ok(serde_json::from_str(include_str!(
        "../../../pandoc/filters/shared/element-attributes.json"
    ))?)
}
