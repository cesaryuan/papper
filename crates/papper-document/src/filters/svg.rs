//! Embed local SVG child images and rasterize opted-in DOCX illustrations.

use anyhow::{Context, Result, bail};
use base64::Engine as _;
use flate2::read::GzDecoder;
use papper_core::paths::{atomic_write, display_path, pandoc_path};
use percent_encoding::percent_decode_str;
use regex::Regex;
use roxmltree::{Document, Node};
use serde_json::{Value, json};
use sha2::{Digest, Sha256};
use std::fs;
use std::io::Read;
use std::ops::Range;
use std::path::{Path, PathBuf};
use std::sync::{Arc, OnceLock};
use std::time::UNIX_EPOCH;

use super::{
    FilterOptions, image_attribute, image_url, parse_bool, remove_image_attributes,
    set_image_attribute, set_image_url, walk_mut,
};

const XLINK_NAMESPACE: &str = "http://www.w3.org/1999/xlink";
const PNG_ATTRIBUTES: &[&str] = &[
    "to-png",
    "to_png",
    "toPng",
    "to-png-scale",
    "to_png_scale",
    "toPngScale",
];

/// Track child dependencies and lexical XML edits without reserializing unrelated SVG markup.
struct SvgNormalization {
    text: String,
    resources: Vec<Value>,
    embeds_svg: bool,
}

/// Return a stable byte digest for cache validation independent of file timestamps.
fn digest(bytes: &[u8]) -> String {
    Sha256::digest(bytes)
        .iter()
        .map(|byte| format!("{byte:02x}"))
        .collect()
}

/// Decode local URL paths while leaving remote protocols and data URIs unchanged.
pub fn path_from_url(url: &str) -> Option<PathBuf> {
    if url.is_empty() {
        return None;
    }
    let clean = url.split(['?', '#']).next()?;
    let text = if let Some(file) = clean
        .get(..5)
        .filter(|prefix| prefix.eq_ignore_ascii_case("file:"))
        .map(|_| &clean[5..])
    {
        if let Some(local) = file.strip_prefix("///") {
            if cfg!(windows) && local.as_bytes().get(1) == Some(&b':') {
                local.to_string()
            } else {
                format!("/{local}")
            }
        } else {
            file.to_string()
        }
    } else {
        let drive = clean.as_bytes().get(1) == Some(&b':')
            && matches!(clean.as_bytes().get(2), Some(b'/' | b'\\'));
        let scheme = clean
            .find(':')
            .is_some_and(|index| !clean[..index].contains(['/', '\\']));
        if scheme && !drive {
            return None;
        }
        clean.to_string()
    };
    if text.is_empty() {
        return None;
    }
    Some(PathBuf::from(
        percent_decode_str(&text).decode_utf8_lossy().into_owned(),
    ))
}

/// Match both SVG source formats without rasterizing unrelated image files.
fn is_svg(path: &Path) -> bool {
    path.extension()
        .and_then(|extension| extension.to_str())
        .is_some_and(|extension| {
            extension.eq_ignore_ascii_case("svg") || extension.eq_ignore_ascii_case("svgz")
        })
}

/// Resolve the manuscript's ordered resource roots and remove Windows extended prefixes.
fn resolve_source(path: &Path, roots: &[PathBuf]) -> Option<PathBuf> {
    let found = if path.is_absolute() {
        path.is_file().then(|| path.to_path_buf())
    } else {
        roots
            .iter()
            .map(|root| root.join(path))
            .find(|candidate| candidate.is_file())
    };
    found
        .and_then(|path| path.canonicalize().ok())
        .map(|path| PathBuf::from(display_path(&path)))
}

/// Generate the same relative cache layout, hashing outside-project absolute resources.
fn cache_path(source: &Path, output_root: &Path, roots: &[PathBuf], extension: &str) -> PathBuf {
    for root in roots {
        let root = root
            .canonicalize()
            .map(|root| PathBuf::from(display_path(&root)))
            .unwrap_or_else(|_| root.clone());
        if let Ok(relative) = source.strip_prefix(root) {
            return output_root.join(relative).with_extension(extension);
        }
    }
    let key = digest(display_path(source).as_bytes());
    output_root.join(format!(
        "{}-{}.{}",
        source.file_stem().unwrap_or_default().to_string_lossy(),
        &key[..12],
        extension
    ))
}

/// Read plain SVG or gzip-compressed SVGZ without changing the original resource.
fn read_svg(source: &Path) -> Result<String> {
    let bytes =
        fs::read(source).with_context(|| format!("Cannot read SVG: {}", source.display()))?;
    let bytes = if source
        .extension()
        .and_then(|extension| extension.to_str())
        .is_some_and(|extension| extension.eq_ignore_ascii_case("svgz"))
    {
        let mut decoded = Vec::new();
        GzDecoder::new(bytes.as_slice()).read_to_end(&mut decoded)?;
        decoded
    } else {
        bytes
    };
    String::from_utf8(bytes).with_context(|| format!("SVG is not UTF-8: {}", source.display()))
}

/// Record both legacy timestamp fields and a digest for same-size, same-time resource edits.
fn file_metadata(path: &Path) -> Result<Value> {
    let metadata = path.metadata()?;
    let modified = metadata.modified()?.duration_since(UNIX_EPOCH)?.as_nanos();
    Ok(
        json!({"path": display_path(path), "mtime_ns": modified as u64, "size": metadata.len(), "sha256": digest(&fs::read(path)?)}),
    )
}

/// Escape XML attribute values without altering already parsed element content.
fn xml_attribute(raw: &str) -> String {
    raw.replace('&', "&amp;")
        .replace('"', "&quot;")
        .replace('<', "&lt;")
        .replace('>', "&gt;")
}

/// Find the opening tag's insertion position without confusing quoted greater-than signs.
fn opening_tag_end(text: &str, node: Node<'_, '_>) -> Result<usize> {
    let mut quote = None;
    for (offset, character) in text[node.range().start..].char_indices() {
        if quote == Some(character) {
            quote = None;
        } else if quote.is_none() && matches!(character, '\'' | '"') {
            quote = Some(character);
        } else if quote.is_none() && character == '>' {
            let index = node.range().start + offset;
            return Ok(
                if text.as_bytes().get(index.wrapping_sub(1)) == Some(&b'/') {
                    index - 1
                } else {
                    index
                },
            );
        }
    }
    bail!("SVG image opening tag is incomplete")
}

/// Replace both href spellings and add a scoped xlink declaration only when needed.
fn href_edits(text: &str, node: Node<'_, '_>, href: &str) -> Result<Vec<(Range<usize>, String)>> {
    let escaped = xml_attribute(href);
    let mut edits = Vec::new();
    let mut has_href = false;
    let mut has_xlink = false;
    for attribute in node
        .attributes()
        .filter(|attribute| attribute.name() == "href")
    {
        if attribute.namespace().is_none() {
            has_href = true;
        } else if attribute.namespace() == Some(XLINK_NAMESPACE) {
            has_xlink = true;
        } else {
            continue;
        }
        edits.push((attribute.range_value(), escaped.clone()));
    }
    let mut additions = String::new();
    if !has_href {
        additions.push_str(&format!(" href=\"{escaped}\""));
    }
    if !has_xlink {
        // A local declaration preserves existing unrelated namespace prefixes.
        additions.push_str(&format!(
            " xmlns:xlink=\"{XLINK_NAMESPACE}\" xlink:href=\"{escaped}\""
        ));
    }
    if !additions.is_empty() {
        let insertion = opening_tag_end(text, node)?;
        edits.push((insertion..insertion, additions));
    }
    Ok(edits)
}

/// Normalize embedded/local child references while preserving every untouched XML byte.
fn normalize_svg(source: &Path, embed: bool) -> Result<SvgNormalization> {
    let mut text = read_svg(source)?;
    let parsed =
        Document::parse(&text).with_context(|| format!("Invalid SVG XML: {}", source.display()))?;
    let mut edits = Vec::new();
    let mut resources = Vec::new();
    let mut embeds_svg = false;
    for image in parsed
        .descendants()
        .filter(|node| node.is_element() && node.tag_name().name() == "image")
    {
        let href = image
            .attribute("href")
            .filter(|href| !href.is_empty())
            .or_else(|| image.attribute((XLINK_NAMESPACE, "href")));
        let Some(href) = href else { continue };
        if href.trim().to_lowercase().starts_with("data:") {
            continue;
        }
        let Some(child) = path_from_url(href) else {
            continue;
        };
        let candidate = if child.is_absolute() {
            child
        } else {
            source.parent().unwrap_or(Path::new(".")).join(child)
        };
        let Some(child) = resolve_source(&candidate, &[]) else {
            if embed {
                super::warn(format_args!(
                    "[WARN] SVG child image not found, leaving unchanged: {href}"
                ));
            }
            continue;
        };
        let mut metadata = file_metadata(&child)?.as_object().unwrap().clone();
        metadata.insert("href".to_string(), json!(href));
        let replacement = if embed {
            let mime = mime_guess::from_path(&child)
                .first_or_octet_stream()
                .to_string();
            metadata.insert("mime_type".to_string(), json!(mime));
            embeds_svg |= is_svg(&child);
            let encoded = base64::engine::general_purpose::STANDARD.encode(fs::read(&child)?);
            format!("data:{mime};base64,{encoded}")
        } else {
            // usvg resolves decoded Unicode paths reliably on Windows; absolute
            // decoded paths also avoid cross-volume relative-path failures.
            let normalized = child
                .strip_prefix(source.parent().unwrap_or(Path::new(".")))
                .map(pandoc_path)
                .unwrap_or_else(|_| pandoc_path(&child));
            metadata.insert("normalized_href".to_string(), json!(normalized));
            normalized
        };
        if embed || replacement != href {
            edits.extend(href_edits(&text, image, &replacement)?);
        }
        resources.push(Value::Object(metadata));
    }
    drop(parsed);
    edits.sort_by_key(|edit| std::cmp::Reverse(edit.0.start));
    for (range, replacement) in edits {
        text.replace_range(range, &replacement);
    }
    Ok(SvgNormalization {
        text,
        resources,
        embeds_svg,
    })
}

/// Locate sidecar metadata beside the generated illustration.
fn metadata_path(target: &Path) -> PathBuf {
    let mut name = target.as_os_str().to_os_string();
    name.push(".meta.json");
    PathBuf::from(name)
}

/// Reuse complete cache entries only when all source/options/content fields match.
fn cache_matches(target: &Path, expected: &Value) -> bool {
    if !target.is_file() {
        return false;
    }
    let Some(actual) = fs::read(metadata_path(target))
        .ok()
        .and_then(|bytes| serde_json::from_slice::<Value>(&bytes).ok())
    else {
        return false;
    };
    expected.as_object().is_some_and(|mapping| {
        mapping
            .iter()
            .all(|(key, value)| actual.get(key) == Some(value))
    })
}

/// Atomically publish sidecars after the generated illustration is safely available.
fn publish_metadata(target: &Path, metadata: &Value) -> Result<()> {
    atomic_write(
        &metadata_path(target),
        &serde_json::to_vec_pretty(metadata)?,
    )
}

/// Embed child images and mark nested SVG panels for Word's mandatory PNG fallback.
pub fn embed_images(document: &mut Value, options: &FilterOptions) -> Result<()> {
    if !options.svg_embed_images {
        return Ok(());
    }
    walk_mut(document, &mut |node| {
        if node.get("t").and_then(Value::as_str) != Some("Image") {
            return Ok(());
        }
        let Some(url) = image_url(node).map(str::to_string) else {
            return Ok(());
        };
        let Some(path) = path_from_url(&url).filter(|path| is_svg(path)) else {
            return Ok(());
        };
        let Some(source) = resolve_source(&path, &options.svg_embed_base_dirs) else {
            super::warn(format_args!(
                "[WARN] SVG image not found, leaving unchanged: {url}"
            ));
            return Ok(());
        };
        let normalization = normalize_svg(&source, true)?;
        if normalization.resources.is_empty() {
            return Ok(());
        }
        let target = cache_path(
            &source,
            &options.svg_embed_dir,
            &options.svg_embed_base_dirs,
            "svg",
        );
        if target == source {
            bail!(
                "SVG cache destination would overwrite its source: {}",
                source.display()
            )
        }
        let expected = json!({"version": 1, "source": file_metadata(&source)?, "resources": normalization.resources, "pmt_version": options.pmt_version, "converter": "rust-svg-embed-v1"});
        if !cache_matches(&target, &expected) {
            atomic_write(&target, normalization.text.as_bytes())?;
            publish_metadata(&target, &expected)?;
        }
        set_image_url(node, pandoc_path(&target));
        if normalization.embeds_svg {
            set_image_attribute(node, "to-png", "true");
        }
        Ok(())
    })
}

/// Derive per-image pixel width using the current physical/percent width policy.
pub fn image_auto_width(raw: Option<&str>) -> Option<u32> {
    static WIDTH: OnceLock<Regex> = OnceLock::new();
    let expression = WIDTH.get_or_init(|| {
        Regex::new(r"(?i)^([0-9]*\.?[0-9]+)\s*(%|px|cm|mm|in|inch)$")
            .expect("valid image width grammar")
    });
    let captures = expression.captures(raw?.trim())?;
    let number: f64 = captures[1].parse().ok()?;
    let pixels = match captures[2].to_lowercase().as_str() {
        "%" => number * 3000.0 / 100.0,
        "px" => number * 2.0,
        "cm" => number / 2.54 * 500.0,
        "mm" => number / 25.4 * 500.0,
        "in" | "inch" => number * 500.0,
        _ => return None,
    };
    // Python round uses ties-to-even; preserve half-pixel decisions.
    Some(pixels.round_ties_even().clamp(1.0, u32::MAX as f64) as u32)
}

/// Match Python's six-significant-digit `:g` cache names for per-image zoom variants.
fn scale_cache_label(scale: f64) -> String {
    let scientific = format!("{scale:.5e}");
    let (mantissa, exponent) = scientific
        .split_once('e')
        .expect("scientific float has exponent");
    let exponent: i32 = exponent.parse().expect("formatted exponent is numeric");
    let display = if !(-4..6).contains(&exponent) {
        let mantissa = mantissa.trim_end_matches('0').trim_end_matches('.');
        format!(
            "{mantissa}e{}{:02}",
            if exponent < 0 { "-" } else { "+" },
            exponent.abs()
        )
    } else {
        let precision = (5 - exponent).max(0) as usize;
        let value = format!("{scale:.precision$}");
        if value.contains('.') {
            value
                .trim_end_matches('0')
                .trim_end_matches('.')
                .to_string()
        } else {
            value
        }
    };
    display.replace('.', "p").replace('-', "m").replace('+', "")
}

/// Load system fonts once, only after an illustration actually requires rendering.
fn font_database() -> Arc<resvg::usvg::fontdb::Database> {
    static FONTS: OnceLock<Arc<resvg::usvg::fontdb::Database>> = OnceLock::new();
    FONTS
        .get_or_init(|| {
            let mut fonts = resvg::usvg::fontdb::Database::new();
            fonts.load_system_fonts();
            // Match the retained resvg-py platform defaults for generic families.
            // fontdb's library defaults differ from the old binding's UI fonts.
            if cfg!(any(target_os = "windows", target_os = "macos")) {
                fonts.set_serif_family("Times New Roman");
                fonts.set_sans_serif_family("Arial");
                fonts.set_cursive_family("Comic Sans MS");
                fonts.set_fantasy_family("Impact");
                fonts.set_monospace_family("Courier New");
            } else if cfg!(target_os = "linux") {
                fonts.set_serif_family("Liberation Serif");
                fonts.set_sans_serif_family("Liberation Sans");
                fonts.set_cursive_family("Comic Neue");
                fonts.set_fantasy_family("Anton");
                fonts.set_monospace_family("Liberation Mono");
            }
            Arc::new(fonts)
        })
        .clone()
}

/// Render native SVG bytes at the same width-over-zoom precedence as resvg-py.
fn render_png(
    source: &Path,
    normalized: &str,
    dpi: f64,
    scale: f64,
    width: Option<u32>,
) -> Result<Vec<u8>> {
    let options = resvg::usvg::Options {
        resources_dir: source.parent().map(Path::to_path_buf),
        dpi: dpi as f32,
        font_size: 16.0,
        font_family: if cfg!(target_os = "linux") {
            "Liberation Serif".to_string()
        } else {
            "Times New Roman".to_string()
        },
        default_size: resvg::usvg::Size::from_wh(width.unwrap_or(100) as f32, 100.0)
            .context("SVG default viewport is invalid")?,
        fontdb: font_database(),
        ..resvg::usvg::Options::default()
    };
    let tree = resvg::usvg::Tree::from_str(normalized, &options)
        .with_context(|| format!("Cannot render SVG: {}", source.display()))?;
    // The original binding rounded the intrinsic size before computing both
    // the target size and transform; physical millimeter sizes otherwise shift
    // every rendered pixel even when output dimensions happen to agree.
    let original = tree.size().to_int_size();
    let scaled = if let Some(width) = width {
        original.scale_to_width(width)
    } else {
        original.scale_by(scale as f32)
    }
    .context("SVG render size is invalid")?;
    let size = scaled;
    // Bound allocations before tiny-skia receives dimensions derived from user SVGs.
    if u64::from(size.width()) * u64::from(size.height()) > 200_000_000 {
        bail!("SVG rasterization exceeds 200 million pixels")
    }
    let mut pixmap = resvg::tiny_skia::Pixmap::new(size.width(), size.height())
        .context("Could not allocate SVG image buffer")?;
    let transform = resvg::tiny_skia::Transform::from_scale(
        size.width() as f32 / original.width() as f32,
        size.height() as f32 / original.height() as f32,
    );
    resvg::render(&tree, transform, &mut pixmap.as_mut());
    Ok(pixmap.encode_png()?)
}

/// Create or reuse a PNG after recording all decoded child-image dependencies.
fn ensure_png(
    source: &Path,
    target: &Path,
    options: &FilterOptions,
    scale: f64,
    width: Option<u32>,
) -> Result<()> {
    let normalization = normalize_svg(source, false)?;
    let file = file_metadata(source)?;
    let expected = json!({"version": 2, "source": display_path(source), "source_mtime_ns": file["mtime_ns"], "source_size": file["size"], "source_sha256": file["sha256"], "resources": normalization.resources, "dpi": options.svg_dpi, "scale": scale, "width": width, "pmt_version": options.pmt_version, "converter": "resvg-rust-0.47-v2"});
    if !cache_matches(target, &expected) {
        let bytes = render_png(source, &normalization.text, options.svg_dpi, scale, width)?;
        atomic_write(target, &bytes)?;
        publish_metadata(target, &expected)?;
    }
    Ok(())
}

/// Rasterize requested SVG images and remove conversion hints from every image node.
pub fn rasterize_images(document: &mut Value, options: &FilterOptions) -> Result<()> {
    walk_mut(document, &mut |node| {
        if node.get("t").and_then(Value::as_str) != Some("Image") {
            return Ok(());
        }
        let requested =
            image_attribute(node, &["to-png", "to_png", "toPng"]).is_some_and(parse_bool);
        let scale_override = image_attribute(node, &["to-png-scale", "to_png_scale", "toPngScale"])
            .and_then(|raw| raw.parse::<f64>().ok())
            .filter(|scale| scale.is_finite() && *scale > 0.0);
        let width = options
            .svg_width
            .or_else(|| image_auto_width(image_attribute(node, &["width"])));
        let has_override = scale_override.is_some() && width.is_none();
        if width.is_some() && scale_override.is_some() {
            super::warn(format_args!(
                "[WARN] Ignoring to-png-scale because docxSvgToPngWidth is set"
            ));
        }
        let scale = if has_override {
            scale_override.unwrap()
        } else {
            options.svg_scale
        };
        remove_image_attributes(node, PNG_ATTRIBUTES);
        let Some(url) = image_url(node).map(str::to_string) else {
            return Ok(());
        };
        let Some(path) = path_from_url(&url).filter(|path| is_svg(path)) else {
            return Ok(());
        };
        if !options.svg_convert_all && !requested {
            return Ok(());
        }
        let Some(source) = resolve_source(&path, &options.svg_png_base_dirs) else {
            super::warn(format_args!(
                "[WARN] SVG image not found, leaving unchanged: {url}"
            ));
            return Ok(());
        };
        let mut target = cache_path(
            &source,
            &options.svg_png_dir,
            &options.svg_png_base_dirs,
            "png",
        );
        if has_override && scale != options.svg_scale {
            let scale_text = scale_cache_label(scale);
            target.set_file_name(format!(
                "{}.scale-{scale_text}.png",
                target.file_stem().unwrap_or_default().to_string_lossy()
            ));
        }
        ensure_png(&source, &target, options, scale, width)?;
        set_image_url(node, pandoc_path(&target));
        Ok(())
    })
}

#[cfg(test)]
mod tests {
    use super::*;

    /// Build an actual Pandoc image node with its visible caption and optional PNG request.
    fn document(url: &str, attributes: Value) -> Value {
        json!({"pandoc-api-version": [1, 23, 1], "meta": {}, "blocks": [{"t": "Para", "c": [{"t": "Image", "c": [["", [], attributes], [{"t": "Str", "c": "Figure"}], [url, "title"]]}]}]})
    }

    /// Embed linked child bytes, invalidate edited panels, and preserve both original sources.
    #[test]
    fn embedding_preserves_sources_and_invalidates_child_content() -> Result<()> {
        let directory = tempfile::tempdir()?;
        let source = directory.path().join("layout.svg");
        let child = directory.path().join("panel.png");
        let original = "<svg xmlns=\"http://www.w3.org/2000/svg\" width=\"20\" height=\"20\"><image href=\"panel.png\" width=\"20\" height=\"20\"/></svg>";
        fs::write(&source, original)?;
        fs::write(&child, b"first")?;
        let options = FilterOptions {
            svg_embed_images: true,
            svg_embed_base_dirs: vec![directory.path().into()],
            svg_embed_dir: directory.path().join("cache"),
            ..FilterOptions::default()
        };
        let mut first = document("layout.svg", json!([]));
        embed_images(&mut first, &options)?;
        let target = PathBuf::from(image_url(&first["blocks"][0]["c"][0]).unwrap());
        assert!(fs::read_to_string(&target)?.contains("data:image/png;base64,Zmlyc3Q="));
        fs::write(&child, b"second")?;
        let mut second = document("layout.svg", json!([]));
        embed_images(&mut second, &options)?;
        assert!(fs::read_to_string(target)?.contains("data:image/png;base64,c2Vjb25k"));
        assert_eq!(fs::read_to_string(source)?, original);
        assert_eq!(fs::read(child)?, b"second");
        Ok(())
    }

    /// Render only opted-in illustrations and honor width while clearing DOCX-only hints.
    #[test]
    fn rasterization_uses_requested_width_and_leaves_ordinary_svg_alone() -> Result<()> {
        let directory = tempfile::tempdir()?;
        fs::write(
            directory.path().join("figure.svg"),
            "<svg xmlns=\"http://www.w3.org/2000/svg\" width=\"100\" height=\"50\"><rect width=\"100\" height=\"50\" fill=\"red\"/></svg>",
        )?;
        let options = FilterOptions {
            svg_png_base_dirs: vec![directory.path().into()],
            svg_png_dir: directory.path().join("png"),
            ..FilterOptions::default()
        };
        let mut untouched = document("figure.svg", json!([]));
        rasterize_images(&mut untouched, &options)?;
        assert_eq!(
            image_url(&untouched["blocks"][0]["c"][0]),
            Some("figure.svg")
        );
        let mut converted = document(
            "figure.svg",
            json!([["to-png", "true"], ["width", "50%"], ["to-png-scale", "2"]]),
        );
        rasterize_images(&mut converted, &options)?;
        let image = &converted["blocks"][0]["c"][0];
        let bytes = fs::read(image_url(image).unwrap())?;
        assert!(bytes.starts_with(b"\x89PNG\r\n\x1a\n"));
        assert_eq!(u32::from_be_bytes(bytes[16..20].try_into().unwrap()), 1500);
        assert_eq!(u32::from_be_bytes(bytes[20..24].try_into().unwrap()), 750);
        assert_eq!(image_attribute(image, &["width"]), Some("50%"));
        assert_eq!(image_attribute(image, &["to-png", "to-png-scale"]), None);
        Ok(())
    }

    /// Rasterize nested SVG panels for Word and reuse unchanged cache files without rewrites.
    #[test]
    fn nested_svg_fallback_reuses_complete_cache_entries() -> Result<()> {
        let directory = tempfile::tempdir()?;
        fs::write(
            directory.path().join("panel.svg"),
            "<svg xmlns=\"http://www.w3.org/2000/svg\" width=\"20\" height=\"10\"><rect width=\"20\" height=\"10\" fill=\"blue\"/></svg>",
        )?;
        fs::write(
            directory.path().join("layout.svg"),
            "<svg xmlns=\"http://www.w3.org/2000/svg\" width=\"20\" height=\"10\"><image href=\"panel.svg\" width=\"20\" height=\"10\"/></svg>",
        )?;
        let options = FilterOptions {
            svg_embed_images: true,
            svg_embed_base_dirs: vec![directory.path().into()],
            svg_png_base_dirs: vec![directory.path().into()],
            svg_embed_dir: directory.path().join("embedded"),
            svg_png_dir: directory.path().join("png"),
            ..FilterOptions::default()
        };
        let mut first = document("layout.svg", json!([]));
        embed_images(&mut first, &options)?;
        let image = &first["blocks"][0]["c"][0];
        assert_eq!(image_attribute(image, &["to-png"]), Some("true"));
        let embedded = PathBuf::from(image_url(image).unwrap());
        let embedded_time = embedded.metadata()?.modified()?;
        rasterize_images(&mut first, &options)?;
        let png = PathBuf::from(image_url(&first["blocks"][0]["c"][0]).unwrap());
        let pixels = fs::read(&png)?;
        let png_time = png.metadata()?.modified()?;
        let mut repeated = document("layout.svg", json!([]));
        embed_images(&mut repeated, &options)?;
        rasterize_images(&mut repeated, &options)?;
        assert_eq!(
            image_url(&repeated["blocks"][0]["c"][0]),
            image_url(&first["blocks"][0]["c"][0])
        );
        assert_eq!(fs::read(&png)?, pixels);
        assert_eq!(png.metadata()?.modified()?, png_time);
        assert_eq!(embedded.metadata()?.modified()?, embedded_time);
        assert_eq!(
            image_attribute(&repeated["blocks"][0]["c"][0], &["to-png"]),
            None
        );
        Ok(())
    }
}
