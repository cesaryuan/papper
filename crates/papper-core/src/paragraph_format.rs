//! Resolve fenced-Div formatting with the same property vocabulary as docxStyle.

use anyhow::{Context, Result, bail};
use serde_json::{Map, Value, json};

use crate::style_properties::STYLE_PROPERTIES;
use crate::style_values::{normalize_paragraph_style_settings, parse_font_family};

/// Keep canonical inputs for Word setters and normalized values for HTML rendering.
pub struct ParagraphFormat {
    pub raw: Map<String, Value>,
    pub normalized: Map<String, Value>,
}

/// Validate every scope before merging, so an inner override cannot conceal an invalid value.
pub fn resolve_scopes(payload: &str) -> Result<ParagraphFormat> {
    let scopes: Vec<Map<String, Value>> =
        serde_json::from_str(payload).context("Invalid local paragraph formatting")?;
    let mut raw = Map::new();
    for attributes in scopes {
        let mut scope = expand_attributes(attributes)?;
        let normalized = normalize_paragraph_style_settings(&scope, "paragraph format")?;
        if normalized
            .get("first_line_indent_chars")
            .and_then(Value::as_f64)
            .is_some_and(|value| value < 0.0)
        {
            bail!("paragraph format.firstLineIndentChars must be greater than or equal to 0");
        }
        // A font-family shorthand sets both slots; expand it before nested slot overrides.
        if let Some(family) = scope.get("fontFamily") {
            scope.insert(
                "fontFamily".into(),
                json!(parse_font_family(family, "paragraph format.fontFamily")?),
            );
        }
        let indentation = scope.get("indentation").and_then(Value::as_object);
        if scope.contains_key("firstLineIndentChars")
            || indentation.is_some_and(|value| {
                value.contains_key("firstLine") || value.contains_key("hanging")
            })
        {
            // An inner indent mode replaces the inherited mode, rather than conflicting with it.
            raw.remove("firstLineIndentChars");
            if let Some(Value::Object(indent)) = raw.get_mut("indentation") {
                indent.remove("firstLine");
                indent.remove("hanging");
            }
        }
        for (name, value) in scope {
            if let Value::Object(children) = value {
                let parent = raw.entry(name).or_insert_with(|| json!({}));
                parent
                    .as_object_mut()
                    .context("Invalid paragraph format group")?
                    .extend(children);
            } else {
                raw.insert(name, value);
            }
        }
    }
    let normalized = normalize_paragraph_style_settings(&raw, "paragraph format")?;
    Ok(ParagraphFormat { raw, normalized })
}

/// Expand dotted attribute names, rejecting ambiguous shorthand/group combinations.
fn expand_attributes(attributes: Map<String, Value>) -> Result<Map<String, Value>> {
    let mut result = Map::new();
    for (name, value) in &attributes {
        if let Some((parent, child)) = name.split_once('.') {
            if attributes.contains_key(parent) {
                bail!("paragraph format cannot combine `{parent}` with `{name}` in the same block");
            }
            let group = result.entry(parent.to_owned()).or_insert_with(|| json!({}));
            group
                .as_object_mut()
                .context("Invalid paragraph format group")?
                .insert(child.into(), value.clone());
        } else {
            let Some(property) = STYLE_PROPERTIES
                .iter()
                .find(|property| property.name == name)
            else {
                bail!("Unknown paragraph format property `{name}`");
            };
            if !property.children.is_empty() && !property.types.contains(&"string") {
                bail!(
                    "paragraph format.{name} requires dotted attributes, such as `{name}.{}`",
                    property.children[0].name
                );
            }
            result.insert(name.clone(), value.clone());
        }
    }
    Ok(result)
}
