//! Benchmark Skia's software SVG renderer without modifying Papper's backend.
//!
//! Run `cargo run --release --manifest-path tools/papper-dev/benchmarks/svg-skia/
//! Cargo.toml -- --source <svg> --output <png> --repeat 3` from the repository.
//! The program uses installed fonts and inline image resources, preserves native
//! SVG dimensions and transparency, and renders to a CPU raster surface. Each
//! repetition reparses and renders the original bytes, then encodes a full PNG.
//! JSON timings separate font initialization, parsing, rendering and encoding.
//! External file/HTTP resources and viewport-relative dimensions are outside
//! this experiment's scope; production adoption requires separate validation.

use anyhow::{Context, Result, ensure};
use clap::Parser;
use skia_safe::{Color, EncodedImageFormat, FontMgr, surfaces, svg::Dom};
use std::{path::PathBuf, time::Instant};

/// Select an original SVG and a PNG destination for a reproducible CPU benchmark.
#[derive(Parser)]
struct Options {
    #[arg(long)]
    source: PathBuf,
    #[arg(long)]
    output: PathBuf,
    #[arg(long, default_value_t = 3)]
    repeat: usize,
}

/// Report errors on stderr and reserve stdout for machine-readable timings.
fn main() {
    if let Err(error) = run() {
        eprintln!("[svg-skia-benchmark] {error:#}");
        std::process::exit(1);
    }
}

/// Render each fresh DOM on a software surface and save the final repetition.
fn run() -> Result<()> {
    let options = Options::parse();
    ensure!(options.repeat > 0, "Repeat count must be positive");
    let bytes = std::fs::read(&options.source).context("Read original SVG")?;
    let started = Instant::now();
    let fonts = FontMgr::default();
    let fonts_ms = started.elapsed().as_secs_f64() * 1000.0;
    eprintln!(
        "[svg-skia-benchmark] CPU raster: {}",
        options.source.display()
    );
    for repetition in 1..=options.repeat {
        let started = Instant::now();
        let mut dom = Dom::from_bytes(&bytes, fonts.clone()).context("Parse SVG")?;
        let size = dom.root().intrinsic_size();
        ensure!(
            size.width.is_finite()
                && size.height.is_finite()
                && size.width > 0.0
                && size.height > 0.0
                && size.width < i32::MAX as f32
                && size.height < i32::MAX as f32,
            "Benchmark requires positive intrinsic pixel dimensions"
        );
        let (width, height) = (size.width.ceil() as i32, size.height.ceil() as i32);
        dom.set_container_size(size);
        let parsed = Instant::now();
        // Raster surfaces guarantee CPU execution, even on machines with a GPU.
        let mut surface =
            surfaces::raster_n32_premul((width, height)).context("Allocate CPU raster surface")?;
        surface.canvas().clear(Color::TRANSPARENT);
        dom.render(surface.canvas());
        let rendered = Instant::now();
        let png = surface
            .image_snapshot()
            .encode(None, EncodedImageFormat::PNG, None)
            .context("Encode PNG")?;
        let encoded = Instant::now();
        if repetition == options.repeat {
            std::fs::write(&options.output, png.as_bytes()).context("Write PNG")?;
        }
        println!(
            "{}",
            serde_json::json!({
                "source": options.source, "output": options.output,
                "renderer": "skia-safe 0.153.3 CPU", "repetition": repetition,
                "width": width, "height": height, "png_bytes": png.size(),
                "fonts_ms": fonts_ms,
                "parse_ms": (parsed - started).as_secs_f64() * 1000.0,
                "render_ms": (rendered - parsed).as_secs_f64() * 1000.0,
                "encode_ms": (encoded - rendered).as_secs_f64() * 1000.0,
                "total_ms": (encoded - started).as_secs_f64() * 1000.0,
            })
        );
    }
    Ok(())
}
