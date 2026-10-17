//! Share canonical typography property names and preferred input forms with editors.

use anyhow::{Result, bail};
use serde_json::{Map, Value};

/// Describe a public typography property without embedding its domain parser.
pub struct StyleProperty {
    pub name: &'static str,
    pub types: &'static [&'static str],
    pub children: &'static [StyleProperty],
}

/// Canonical font slots supported by Word and HTML formatting.
pub const FONT_PROPERTIES: &[StyleProperty] = &[
    StyleProperty {
        name: "western",
        types: &["string"],
        children: &[],
    },
    StyleProperty {
        name: "chinese",
        types: &["string"],
        children: &[],
    },
];

/// Canonical physical indentation controls.
pub const INDENT_PROPERTIES: &[StyleProperty] = &[
    StyleProperty {
        name: "left",
        types: &["string", "number"],
        children: &[],
    },
    StyleProperty {
        name: "right",
        types: &["string", "number"],
        children: &[],
    },
    StyleProperty {
        name: "firstLine",
        types: &["string", "number"],
        children: &[],
    },
    StyleProperty {
        name: "hanging",
        types: &["string", "number"],
        children: &[],
    },
];

/// Canonical paragraph spacing controls, expressed in points.
pub const SPACING_PROPERTIES: &[StyleProperty] = &[
    StyleProperty {
        name: "before",
        types: &["string", "number"],
        children: &[],
    },
    StyleProperty {
        name: "after",
        types: &["string", "number"],
        children: &[],
    },
];

/// Compact border declarations and shared paragraph-to-border distance.
pub const BORDER_PROPERTIES: &[StyleProperty] = &[
    StyleProperty {
        name: "all",
        types: &["string"],
        children: &[],
    },
    StyleProperty {
        name: "top",
        types: &["string"],
        children: &[],
    },
    StyleProperty {
        name: "bottom",
        types: &["string"],
        children: &[],
    },
    StyleProperty {
        name: "left",
        types: &["string"],
        children: &[],
    },
    StyleProperty {
        name: "right",
        types: &["string"],
        children: &[],
    },
    StyleProperty {
        name: "between",
        types: &["string"],
        children: &[],
    },
    StyleProperty {
        name: "space",
        types: &["string", "number"],
        children: &[],
    },
];

/// Public paragraph and character properties; named Word styles remain extensible.
pub const STYLE_PROPERTIES: &[StyleProperty] = &[
    StyleProperty {
        name: "borders",
        types: &["string", "object"],
        children: BORDER_PROPERTIES,
    },
    StyleProperty {
        name: "fontFamily",
        types: &["string", "object"],
        children: FONT_PROPERTIES,
    },
    StyleProperty {
        name: "fontSize",
        types: &["string", "number"],
        children: &[],
    },
    StyleProperty {
        name: "fontColor",
        types: &["string", "array"],
        children: &[],
    },
    StyleProperty {
        name: "bold",
        types: &["boolean"],
        children: &[],
    },
    StyleProperty {
        name: "lineSpacing",
        types: &["string", "number"],
        children: &[],
    },
    StyleProperty {
        name: "alignment",
        types: &["string"],
        children: &[],
    },
    StyleProperty {
        name: "firstLineIndentChars",
        types: &["string", "number"],
        children: &[],
    },
    StyleProperty {
        name: "indentation",
        types: &["object"],
        children: INDENT_PROPERTIES,
    },
    StyleProperty {
        name: "paragraphSpacing",
        types: &["object"],
        children: SPACING_PROPERTIES,
    },
];

/// Reject unknown property names early; specialized parsers still validate values and units.
pub fn validate_property_names(
    mapping: &Map<String, Value>,
    properties: &[StyleProperty],
    prefix: &str,
) -> Result<()> {
    for (name, value) in mapping {
        let Some(property) = properties.iter().find(|property| property.name == name) else {
            bail!("Unknown typography property `{prefix}.{name}`; use exact camelCase names")
        };
        if !property.children.is_empty()
            && let Some(children) = value.as_object()
        {
            validate_property_names(children, property.children, &format!("{prefix}.{name}"))?;
        }
    }
    Ok(())
}
