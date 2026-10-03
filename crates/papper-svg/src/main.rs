//! Provide SVG pixels and SVGZ decompression to Pandoc's Lua image filters.
//!
//! `papper-svg render --source <svg-path> [--width <pixels>]` reads normalized
//! SVG bytes from stdin and writes PNG bytes to stdout. Lua owns document ASTs,
//! resource selection and caches; this process only supplies resvg rendering.
//! `papper-svg gunzip` decodes one SVGZ stream. This small executable depends on
//! neither Papper's CLI nor its embedded Haskell/MathType/template runtime.

use anyhow::{Context, Result, bail, ensure};
use clap::{Parser, Subcommand};
use std::io::{Read, Write};
use std::path::{Path, PathBuf};
use std::sync::{Arc, OnceLock};

/// Keep this resource helper separate from Papper's product command tree.
#[derive(Parser)]
#[command(version)]
struct Cli {
    #[command(subcommand)]
    command: Operation,
}

/// Select a byte-stream transformation without accepting a document AST.
#[derive(Subcommand)]
enum Operation {
    /// Render stdin SVG to stdout PNG using the source's adjacent resources.
    Render {
        #[arg(long)]
        source: PathBuf,
        #[arg(long, default_value_t = 300.0)]
        dpi: f64,
        #[arg(long, default_value_t = 1.0)]
        scale: f64,
        #[arg(long)]
        width: Option<u32>,
    },
    /// Decompress stdin SVGZ bytes without changing the source image.
    Gunzip,
}

/// Keep diagnostics on stderr so binary output remains safe for pandoc.pipe.
fn main() {
    if let Err(error) = run() {
        eprintln!("[papper-svg] {error:#}");
        std::process::exit(1);
    }
}

/// Transform one resource and publish stdout only after the operation succeeds.
fn run() -> Result<()> {
    let operation = Cli::parse().command;
    let mut input = Vec::new();
    std::io::stdin().lock().read_to_end(&mut input)?;
    let output = match operation {
        Operation::Render {
            source,
            dpi,
            scale,
            width,
        } => {
            ensure!(dpi.is_finite() && dpi > 0.0, "DPI must be positive");
            ensure!(scale.is_finite() && scale > 0.0, "Scale must be positive");
            ensure!(width != Some(0), "Pixel width must be positive");
            render(&source, std::str::from_utf8(&input)?, dpi, scale, width)?
        }
        Operation::Gunzip => {
            let mut decoded = Vec::new();
            flate2::read::GzDecoder::new(input.as_slice()).read_to_end(&mut decoded)?;
            decoded
        }
    };
    std::io::stdout().lock().write_all(&output)?;
    Ok(())
}

/// Match the existing renderer's platform font defaults rather than fontdb defaults.
fn font_database() -> Arc<resvg::usvg::fontdb::Database> {
    static FONTS: OnceLock<Arc<resvg::usvg::fontdb::Database>> = OnceLock::new();
    FONTS
        .get_or_init(|| {
            let mut fonts = resvg::usvg::fontdb::Database::new();
            fonts.load_system_fonts();
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

/// Preserve intrinsic-size rounding and width-over-scale precedence for PNG parity.
fn render(
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
            "Liberation Serif".into()
        } else {
            "Times New Roman".into()
        },
        default_size: resvg::usvg::Size::from_wh(width.unwrap_or(100) as f32, 100.0)
            .context("SVG default viewport is invalid")?,
        fontdb: font_database(),
        ..resvg::usvg::Options::default()
    };
    let tree = resvg::usvg::Tree::from_str(normalized, &options)
        .with_context(|| format!("Cannot render SVG: {}", source.display()))?;
    // Round the intrinsic size before the transform, as the original binding did;
    // millimeter dimensions otherwise shift pixels while keeping the same viewport.
    let original = tree.size().to_int_size();
    let size = if let Some(width) = width {
        original.scale_to_width(width)
    } else {
        original.scale_by(scale as f32)
    }
    .context("SVG render size is invalid")?;
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
