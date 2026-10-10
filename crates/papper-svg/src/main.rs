//! Provide SVG pixels and SVGZ decompression to Pandoc's Lua image filters.
//!
//! `papper-svg render --source <svg-path> [--width <pixels>]` reads normalized
//! SVG bytes from stdin and writes PNG bytes to stdout. Lua owns document ASTs,
//! resource selection and explicit rasterization caches; this command supplies
//! Skia CPU rendering without a document AST. usvg normalizes CSS, physical units,
//! font outlines and local resources before the Skia SVG DOM draws pixels.
//! `papper-svg gunzip` decodes one SVGZ stream. This small executable depends on
//! neither Papper's CLI nor its embedded Haskell/MathType/template runtime.
//! `papper-svg rsvg-convert` accepts Pandoc's PNG fallback arguments; the bundled
//! `rsvg-convert` launcher forwards those requests without a shell or librsvg.
//! Fallback PNGs use an optional persistent cache; `font-identity` fingerprints
//! system fonts on demand for text images, leaving other builds free of font scans.

mod cache;
mod renderer;
mod rsvg;

use anyhow::{Result, ensure};
use clap::{Parser, Subcommand};
use std::io::{Read, Write};
use std::path::{Path, PathBuf};
use std::sync::{Arc, OnceLock};
use std::time::Instant;

/// Report opt-in stage timings on stderr without contaminating PNG byte streams.
struct RenderTrace {
    enabled: bool,
    started: Instant,
}

impl RenderTrace {
    /// Identify the source when diagnosing slow parsing, fonts, or rasterization.
    fn new(source: &Path) -> Self {
        let trace = Self {
            enabled: std::env::var("PAPPER_SVG_TRACE").is_ok_and(|value| value == "1"),
            started: Instant::now(),
        };
        if trace.enabled {
            eprintln!("[papper-svg] Rendering {}", source.display());
        }
        trace
    }

    /// Print cumulative elapsed time at meaningful rendering boundaries.
    fn stage(&self, stage: &str) {
        if self.enabled {
            eprintln!(
                "[papper-svg] {stage}: {:.3}s",
                self.started.elapsed().as_secs_f64()
            );
        }
    }
}

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
    /// Fingerprint font bytes and selection order for request-local cache controls.
    FontIdentity,
    /// Accept the rsvg-convert PNG interface used by Pandoc's DOCX writer.
    RsvgConvert(rsvg::Options),
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
    if let Operation::FontIdentity = operation {
        println!("{}", cache::font_identity()?);
        return Ok(());
    }
    if let Operation::RsvgConvert(options) = operation {
        return options.convert();
    }
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
        Operation::RsvgConvert(_) => unreachable!(),
        Operation::FontIdentity => unreachable!(),
    };
    std::io::stdout().lock().write_all(&output)?;
    Ok(())
}

/// Match the existing renderer's platform font defaults rather than fontdb defaults.
fn font_database() -> Arc<usvg::fontdb::Database> {
    static FONTS: OnceLock<Arc<usvg::fontdb::Database>> = OnceLock::new();
    FONTS
        .get_or_init(|| {
            let mut fonts = usvg::fontdb::Database::new();
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

/// Avoid loading system fonts for vector-only SVGs, conservatively retaining nested-image support.
fn requires_fonts(svg: &str) -> bool {
    roxmltree::Document::parse(svg).map_or(true, |document| {
        document
            .descendants()
            .any(|node| node.is_element() && matches!(node.tag_name().name(), "text" | "image"))
    })
}

/// Preserve intrinsic-size rounding and width-over-scale precedence for PNG parity.
fn render(
    source: &Path,
    normalized: &str,
    dpi: f64,
    scale: f64,
    width: Option<u32>,
) -> Result<Vec<u8>> {
    renderer::render(source, normalized, dpi, scale, width)
}
