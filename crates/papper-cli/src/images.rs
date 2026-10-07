//! Prepare request-local controls for Pandoc's Lua image filters.

use anyhow::{Context, Result};
use papper_core::metadata::PmtSettings;
use papper_core::paths::display_path;
use papper_core::resources::{ResourcePaths, svg_renderer_id};
use sha2::{Digest, Sha256};
use std::collections::BTreeMap;
use std::io::Read;
use std::path::{Path, PathBuf};
use std::process::Command;

/// Share typed image settings across manuscript and reply builds without AST processes.
pub(crate) fn filter_environment(
    resources: &ResourcePaths,
    settings: &PmtSettings,
    roots: &[PathBuf],
    embedded_dir: &Path,
    png_dir: &Path,
    rsvg_cache_dir: &Path,
) -> Result<BTreeMap<String, Option<String>>> {
    let fields = settings.fields();
    let roots = serde_json::to_string(roots)?;
    let mut environment = BTreeMap::from([
        (
            "PMT_SVG_EMBED_IMAGES".into(),
            Some((fields.docx_embed_svg_images && !fields.docx_convert_svg_to_png).to_string()),
        ),
        (
            "PMT_SVG_TO_PNG_CONVERT_ALL".into(),
            Some(fields.docx_convert_svg_to_png.to_string()),
        ),
        ("PMT_SVG_EMBED_BASE_DIRS".into(), Some(roots.clone())),
        ("PMT_SVG_TO_PNG_BASE_DIRS".into(), Some(roots)),
        ("PMT_SVG_EMBED_DIR".into(), Some(display_path(embedded_dir))),
        ("PMT_SVG_TO_PNG_DIR".into(), Some(display_path(png_dir))),
        // rsvg-convert is launched by Pandoc itself, so give it the same
        // project-persistent cache used across independent DOCX builds.
        (
            "PAPPER_SVG_CACHE_DIR".into(),
            Some(display_path(rsvg_cache_dir)),
        ),
        ("PAPPER_SVG_FONT_ID".into(), None),
        (
            "PMT_SVG_EMBED_PMT_VERSION".into(),
            Some(env!("CARGO_PKG_VERSION").into()),
        ),
        (
            "PMT_SVG_TO_PNG_PMT_VERSION".into(),
            Some(env!("CARGO_PKG_VERSION").into()),
        ),
        (
            "PMT_SVG_TO_PNG_DPI".into(),
            Some(fields.docx_svg_to_png_dpi.unwrap_or(300.0).to_string()),
        ),
        (
            "PMT_SVG_TO_PNG_SCALE".into(),
            Some(fields.docx_svg_to_png_scale.unwrap_or(1.0).to_string()),
        ),
        (
            "PMT_SVG_TO_PNG_WIDTH".into(),
            fields.docx_svg_to_png_width.map(|width| width.to_string()),
        ),
        (
            "PAPPER_SVG_RENDERER_ID".into(),
            Some(svg_renderer_id().into()),
        ),
    ]);
    if let Some(renderer) = resources.svg_renderer() {
        if !std::env::var("PAPPER_SVG_CACHE").is_ok_and(|value| value == "0") {
            match renderer_font_identity(&renderer) {
                Ok(identity) => {
                    environment.insert("PAPPER_SVG_FONT_ID".into(), Some(identity));
                }
                // Cache preparation must not break conversions with older custom
                // helpers or temporarily unreadable fonts; text images simply miss.
                Err(error) => eprintln!("[papper-svg] Text image cache unavailable: {error:#}"),
            }
        }
        if std::env::var_os("PAPPER_SVG_RENDERER")
            .is_some_and(|configured| Path::new(&configured) == renderer.as_path())
        {
            environment.insert(
                "PAPPER_SVG_RENDERER_ID".into(),
                Some(custom_renderer_identity(&renderer)?),
            );
        }
        environment.insert("PAPPER_SVG_RENDERER".into(), Some(display_path(&renderer)));
        if let Some(converter) = resources.svg_converter() {
            // Pandoc hard-codes rsvg-convert for the PNG fallback. Override its
            // lookup only in this DOCX child, preserving the authored SVG itself.
            let directory = converter
                .parent()
                .context("SVG converter directory is missing")?;
            let mut paths = vec![directory.to_path_buf()];
            if let Some(inherited) = std::env::var_os("PATH") {
                paths.extend(std::env::split_paths(&inherited).filter(|path| path != directory));
            }
            environment.insert(
                "PATH".into(),
                Some(
                    std::env::join_paths(paths)?
                        .into_string()
                        .map_err(|_| anyhow::anyhow!("SVG converter PATH is not valid Unicode"))?,
                ),
            );
        }
    }
    Ok(environment)
}

/// Inspect the renderer's fonts once per build without showing a helper console.
fn renderer_font_identity(renderer: &Path) -> Result<String> {
    let mut command = Command::new(renderer);
    command.arg("font-identity");
    #[cfg(windows)]
    {
        use std::os::windows::process::CommandExt;
        command.creation_flags(0x08000000);
    }
    let result = command.output()?;
    anyhow::ensure!(
        result.status.success(),
        "{}",
        String::from_utf8_lossy(&result.stderr).trim()
    );
    let identity = String::from_utf8(result.stdout)?.trim().to_owned();
    anyhow::ensure!(
        identity.len() == 64 && identity.bytes().all(|byte| byte.is_ascii_hexdigit()),
        "SVG renderer returned an invalid font identity"
    );
    Ok(identity)
}

/// Invalidate caches when a caller replaces an explicit helper, even at the same path.
fn custom_renderer_identity(renderer: &Path) -> Result<String> {
    // Bundled helpers use their build-time ID; only caller-supplied binaries need
    // this once-per-request digest, without launching them for each image.
    let mut file = std::fs::File::open(renderer)
        .with_context(|| format!("Cannot fingerprint SVG renderer: {}", renderer.display()))?;
    let mut digest = Sha256::new();
    let mut buffer = [0; 64 * 1024];
    loop {
        let count = file.read(&mut buffer)?;
        if count == 0 {
            break;
        }
        digest.update(&buffer[..count]);
    }
    Ok(format!(
        "custom-{}",
        papper_platform::native::hex(&digest.finalize())
    ))
}
