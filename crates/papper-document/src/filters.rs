//! Native JSON filters replacing Papper's Python filters, retaining Lua execution.

pub mod latex;
pub mod svg;

use anyhow::{Result, bail};
use papper_core::resources::ResourcePaths;
use serde_json::Value;
use std::path::PathBuf;

/// Respect the existing filter log threshold while keeping JSON stdout untouched.
pub(crate) fn warn(message: std::fmt::Arguments<'_>) {
    let level = std::env::var("PANDOC_TEMPLATE_LOG_LEVEL").unwrap_or_else(|_| "INFO".into());
    if !matches!(level.trim().to_uppercase().as_str(), "ERROR" | "CRITICAL") {
        eprintln!("{message}");
    }
}

/// Hold request-local filter controls without mutating the server's environment.
#[derive(Clone, Debug)]
pub struct FilterOptions {
    pub svg_embed_images: bool,
    pub svg_convert_all: bool,
    pub svg_embed_base_dirs: Vec<PathBuf>,
    pub svg_png_base_dirs: Vec<PathBuf>,
    pub svg_embed_dir: PathBuf,
    pub svg_png_dir: PathBuf,
    pub svg_dpi: f64,
    pub svg_scale: f64,
    pub svg_width: Option<u32>,
    pub pmt_version: String,
    pub latex_target_dir: PathBuf,
    pub pandoc_command: Option<PathBuf>,
}

impl Default for FilterOptions {
    /// Supply standalone-filter defaults without scanning fonts or loading an engine.
    fn default() -> Self {
        let cwd = std::env::current_dir().unwrap_or_else(|_| PathBuf::from("."));
        Self {
            svg_embed_images: false,
            svg_convert_all: false,
            svg_embed_base_dirs: vec![cwd.clone()],
            svg_png_base_dirs: vec![cwd.clone()],
            svg_embed_dir: cwd.join("tmp/svg-embedded"),
            svg_png_dir: cwd.join("tmp/svg-png"),
            svg_dpi: 300.0,
            svg_scale: 1.0,
            svg_width: None,
            pmt_version: "unknown".to_string(),
            latex_target_dir: cwd.join("output/latex"),
            pandoc_command: None,
        }
    }
}

impl FilterOptions {
    /// Read the same filter environment used by current Pandoc build integrations.
    pub fn from_environment() -> Self {
        let mut options = Self::default();
        options.svg_embed_images = env_bool("PMT_SVG_EMBED_IMAGES");
        options.svg_convert_all = env_bool("PMT_SVG_TO_PNG_CONVERT_ALL");
        options.svg_embed_base_dirs =
            env_paths("PMT_SVG_EMBED_BASE_DIRS", &options.svg_embed_base_dirs);
        options.svg_png_base_dirs =
            env_paths("PMT_SVG_TO_PNG_BASE_DIRS", &options.svg_png_base_dirs);
        if let Some(path) = std::env::var_os("PMT_SVG_EMBED_DIR") {
            options.svg_embed_dir = path.into();
        }
        if let Some(path) = std::env::var_os("PMT_SVG_TO_PNG_DIR") {
            options.svg_png_dir = path.into();
        }
        options.svg_dpi = env_positive_float("PMT_SVG_TO_PNG_DPI", 300.0);
        options.svg_scale = env_positive_float("PMT_SVG_TO_PNG_SCALE", 1.0);
        options.svg_width = std::env::var("PMT_SVG_TO_PNG_WIDTH")
            .ok()
            .and_then(|raw| raw.trim().parse().ok())
            .filter(|width| *width > 0);
        options.pmt_version = std::env::var("PMT_SVG_TO_PNG_PMT_VERSION")
            .or_else(|_| std::env::var("PMT_SVG_EMBED_PMT_VERSION"))
            .unwrap_or_else(|_| "unknown".to_string());
        options.pandoc_command = std::env::var_os("PAPPER_PANDOC_ENGINE").map(PathBuf::from);
        options
    }
}

/// Parse boolean spellings shared by filter environment variables and image hints.
pub fn parse_bool(raw: &str) -> bool {
    matches!(
        raw.trim().to_lowercase().as_str(),
        "1" | "true" | "yes" | "y" | "on"
    )
}

/// Parse one opt-in environment switch without importing configuration frameworks.
fn env_bool(name: &str) -> bool {
    std::env::var(name).ok().is_some_and(|raw| parse_bool(&raw))
}

/// Read ordered native search paths without interpreting Windows drive separators as colons.
fn env_paths(name: &str, fallback: &[PathBuf]) -> Vec<PathBuf> {
    let mut paths = Vec::new();
    if let Some(raw) = std::env::var_os(name) {
        for path in std::env::split_paths(&raw).filter(|path| !path.as_os_str().is_empty()) {
            let path = if path.is_absolute() {
                path
            } else {
                std::env::current_dir().unwrap_or_default().join(path)
            };
            if !paths.contains(&path) {
                paths.push(path);
            }
        }
    }
    if paths.is_empty() {
        fallback.to_vec()
    } else {
        paths
    }
}

/// Keep invalid positive scalar overrides from breaking otherwise valid image builds.
fn env_positive_float(name: &str, fallback: f64) -> f64 {
    let Some(raw) = std::env::var(name).ok() else {
        return fallback;
    };
    if let Some(value) = raw
        .parse::<f64>()
        .ok()
        .filter(|value| value.is_finite() && *value > 0.0)
    {
        return value;
    }
    warn(format_args!(
        "[WARN] Invalid {name}={raw:?}; using {fallback}"
    ));
    fallback
}

/// Walk children before their parent, matching Panflute's post-order action semantics.
pub(crate) fn walk_mut(
    node: &mut Value,
    action: &mut impl FnMut(&mut Value) -> Result<()>,
) -> Result<()> {
    match node {
        Value::Array(items) => {
            for item in items {
                walk_mut(item, action)?;
            }
        }
        Value::Object(mapping) => {
            for item in mapping.values_mut() {
                walk_mut(item, action)?;
            }
        }
        _ => (),
    }
    action(node)
}

/// Read one image/link target without interpreting non-AST mappings as elements.
pub(crate) fn image_url(node: &Value) -> Option<&str> {
    node.get("c")?.get(2)?.get(0)?.as_str()
}

/// Rewrite a validated image/link URL while retaining its title and caption.
pub(crate) fn set_image_url(node: &mut Value, url: String) {
    if let Some(target) = node
        .get_mut("c")
        .and_then(|content| content.get_mut(2))
        .and_then(|target| target.get_mut(0))
    {
        *target = Value::String(url);
    }
}

/// Find an image attribute while retaining the original alias and insertion order.
pub(crate) fn image_attribute<'a>(node: &'a Value, names: &[&str]) -> Option<&'a str> {
    let pairs = node.get("c")?.get(0)?.get(2)?.as_array()?;
    names.iter().find_map(|name| {
        pairs
            .iter()
            .find(|pair| pair.get(0).and_then(Value::as_str) == Some(*name))
            .and_then(|pair| pair.get(1))
            .and_then(Value::as_str)
    })
}

/// Set a single filter hint without disturbing identifiers, classes, or other attributes.
pub(crate) fn set_image_attribute(node: &mut Value, name: &str, value: &str) {
    if let Some(pairs) = node
        .get_mut("c")
        .and_then(|content| content.get_mut(0))
        .and_then(|attr| attr.get_mut(2))
        .and_then(Value::as_array_mut)
    {
        if let Some(pair) = pairs
            .iter_mut()
            .find(|pair| pair.get(0).and_then(Value::as_str) == Some(name))
        {
            pair[1] = Value::String(value.to_string());
        } else {
            pairs.push(serde_json::json!([name, value]));
        }
    }
}

/// Strip DOCX-only rasterization hints from every image, including unchanged images.
pub(crate) fn remove_image_attributes(node: &mut Value, names: &[&str]) {
    if let Some(pairs) = node
        .get_mut("c")
        .and_then(|content| content.get_mut(0))
        .and_then(|attr| attr.get_mut(2))
        .and_then(Value::as_array_mut)
    {
        pairs.retain(|pair| {
            !pair
                .get(0)
                .and_then(Value::as_str)
                .is_some_and(|name| names.contains(&name))
        });
    }
}

/// Apply one native JSON filter with environment-compatible standalone controls.
pub fn apply_filter(
    kind: &str,
    document: Value,
    format: &str,
    resources: &ResourcePaths,
) -> Result<Value> {
    let mut options = FilterOptions::from_environment();
    if matches!(
        kind.trim_end_matches(".py").replace('-', "_").as_str(),
        "svg_embed_images" | "svg_embed_images_filter"
    ) {
        // Standalone embedding and rasterization maintain independent cache
        // versions; an unrelated PNG setting must not invalidate embedding.
        options.pmt_version =
            std::env::var("PMT_SVG_EMBED_PMT_VERSION").unwrap_or_else(|_| "unknown".into());
    }
    apply_filter_with_options(kind, document, format, resources, &options)
}

/// Apply request-local controls so concurrent builds never share mutable filter settings.
pub fn apply_filter_with_options(
    kind: &str,
    mut document: Value,
    format: &str,
    resources: &ResourcePaths,
    options: &FilterOptions,
) -> Result<Value> {
    let normalized = kind.trim_end_matches(".py").replace('-', "_");
    match normalized.as_str() {
        "svg_embed_images" | "svg_embed_images_filter" => {
            svg::embed_images(&mut document, options)?
        }
        "svg_to_png" | "svg_to_png_filter" => svg::rasterize_images(&mut document, options)?,
        "to_mathbfit" => walk_mut(&mut document, &mut |node| {
            if node.get("t").and_then(Value::as_str) == Some("Math")
                && let Some(Value::String(math)) =
                    node.get_mut("c").and_then(|content| content.get_mut(1))
            {
                for command in [
                    r"\boldsymbol{",
                    r"\bm{",
                    r"\symbf{",
                    r"\mathbold{",
                    r"\pmb{",
                    r"\mathbfup{",
                ] {
                    *math = math.replace(command, r"\mathbfit{");
                }
            }
            Ok(())
        })?,
        "emf_to_pdf" => walk_mut(&mut document, &mut |node| {
            if node.get("t").and_then(Value::as_str) == Some("Image")
                && let Some(url) = image_url(node).and_then(|url| url.strip_suffix(".emf"))
            {
                set_image_url(node, format!("{url}.pdf"));
            }
            Ok(())
        })?,
        "resource_move" => latex::move_resources(&mut document, options)?,
        "table_convert" => latex::convert_tables(&mut document, format, resources, options)?,
        _ => bail!("Unknown native Pandoc filter: {kind}"),
    }
    Ok(document)
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    /// Keep math replacements within equations and preserve caption text verbatim.
    #[test]
    fn math_and_emf_filters_preserve_unrelated_nodes() -> Result<()> {
        let resources = ResourcePaths::discover()?;
        let document = json!({"pandoc-api-version": [1, 23, 1], "meta": {}, "blocks": [{"t": "Para", "c": [{"t": "Str", "c": "\\bm{text}"}, {"t": "Math", "c": [{"t": "InlineMath"}, "\\bm{x}+\\boldsymbol{y}+\\mathbf{z}"]}, {"t": "Image", "c": [["", [], []], [], ["figure.emf", "title"]]}]}]});
        let result = apply_filter("to_mathbfit", document, "docx", &resources)?;
        let result = apply_filter("emf_to_pdf", result, "latex", &resources)?;
        assert_eq!(result["blocks"][0]["c"][0]["c"], json!("\\bm{text}"));
        assert_eq!(
            result["blocks"][0]["c"][1]["c"][1],
            json!("\\mathbfit{x}+\\mathbfit{y}+\\mathbf{z}")
        );
        assert_eq!(
            result["blocks"][0]["c"][2]["c"][2],
            json!(["figure.pdf", "title"])
        );
        Ok(())
    }
}
