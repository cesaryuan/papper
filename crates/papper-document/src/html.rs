//! HTML typography and manuscript post-processing without a Python runtime.

pub mod postprocess;
pub mod styles;

pub use postprocess::{
    postprocess_html_text, postprocess_html_text_with_style_settings,
    postprocess_html_text_with_styles,
};
pub use styles::{
    build_reference_style_css, build_reference_style_css_text,
    build_reference_style_css_with_settings,
};

use anyhow::Result;
use papper_core::metadata::EffectiveMetadata;
use serde_json::{Map, Value, json};
use std::path::Path;

/// Append generated defaults before user header includes, preserving override order.
pub fn append_reference_style_block(metadata: &mut Map<String, Value>, css: &str) {
    let mut includes = match metadata.remove("header-includes") {
        None | Some(Value::Null) => Vec::new(),
        Some(Value::Array(values)) => values,
        Some(value) => vec![value],
    };
    includes.insert(
        0,
        Value::String(format!(
            "<style id=\"pmt-reference-styles\">\n{css}\n</style>"
        )),
    );
    metadata.insert("header-includes".into(), Value::Array(includes));
}

/// Apply the shared A4 page defaults and explicit quantized Word page margins.
pub fn apply_html_page_metadata(effective: &mut EffectiveMetadata) -> Result<()> {
    for (name, value) in [
        ("html-page-width", "210mm"),
        ("html-page-height", "297mm"),
        ("html-page-margin-top", "1.905cm"),
        ("html-page-margin-bottom", "1.905cm"),
        ("html-page-margin-left", "1.905cm"),
        ("html-page-margin-right", "1.905cm"),
    ] {
        effective.pandoc_metadata.insert(name.into(), json!(value));
    }
    if let Some((margins, _)) =
        papper_core::page_margins::normalize_page_margins(&effective.pmt_settings)?
    {
        for (side, length) in margins {
            let rendered = format!("{:.4}", length.pt());
            let compact = rendered.trim_end_matches('0').trim_end_matches('.');
            effective.pandoc_metadata.insert(
                format!("html-page-margin-{side}"),
                json!(format!("{compact}pt")),
            );
        }
    }
    Ok(())
}

/// Prepare the same title, equation, page, and reference typography for CLI/server.
pub fn prepare_html_metadata(
    effective: &mut EffectiveMetadata,
    styles_path: &Path,
    project_name: &str,
) -> Result<()> {
    let nonempty = |name: &str| {
        effective
            .pandoc_metadata
            .get(name)
            .is_some_and(|value| match value {
                Value::Null => false,
                Value::String(text) => !text.is_empty(),
                Value::Bool(value) => *value,
                Value::Array(values) => !values.is_empty(),
                Value::Object(values) => !values.is_empty(),
                Value::Number(value) => value.as_f64() != Some(0.0),
            })
    };
    if !nonempty("title") && !nonempty("pagetitle") {
        effective
            .pandoc_metadata
            .insert("pagetitle".into(), json!(project_name));
    }
    for (name, value) in [
        ("equationNumberTeX", json!("\\\\tag")),
        ("eqnIndexTemplate", json!("$$i$$")),
        ("eqnBlockInlineMath", json!(false)),
        ("tableEqns", json!(false)),
    ] {
        effective.pandoc_metadata.insert(name.into(), value);
    }
    let css = build_reference_style_css_with_settings(styles_path, &effective.pmt_settings)?;
    append_reference_style_block(&mut effective.pandoc_metadata, &css);
    apply_html_page_metadata(effective)
}
