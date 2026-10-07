//! Adapt Pandoc's rsvg-convert PNG requests to the existing resvg renderer.

use anyhow::{Context, Result, ensure};
use clap::Args;
use std::io::{Read, Write};
use std::path::PathBuf;

/// Accept the PNG subset, including file I/O for manual diagnostics.
#[derive(Args)]
pub(super) struct Options {
    #[arg(short = 'f', long, default_value = "png")]
    format: String,
    #[arg(short = 'a', long)]
    keep_aspect_ratio: bool,
    #[arg(short = 'd', long, default_value_t = 96.0)]
    dpi_x: f64,
    #[arg(short = 'p', long, default_value_t = 96.0)]
    dpi_y: f64,
    #[arg(short = 'o', long)]
    output: Option<PathBuf>,
    input: Option<PathBuf>,
}

impl Options {
    /// Validate the supported contract before rendering or touching the output.
    pub(super) fn convert(self) -> Result<()> {
        ensure!(
            self.format == "png",
            "Only PNG output is supported by Papper's rsvg-convert adapter"
        );
        ensure!(
            self.dpi_x.is_finite() && self.dpi_x > 0.0,
            "DPI must be positive"
        );
        // Pandoc supplies identical DPI values. Reject anisotropic DPI rather than
        // silently producing dimensions that differ from the requested conversion.
        ensure!(
            self.dpi_x == self.dpi_y,
            "Papper's rsvg-convert adapter requires equal --dpi-x and --dpi-y"
        );
        // Intrinsic rendering always preserves aspect ratio; Pandoc's -a therefore
        // needs no additional transform when no explicit size is requested.
        let _ = self.keep_aspect_ratio;
        let (source, input) = if let Some(path) = self.input.filter(|path| path.as_os_str() != "-")
        {
            let input = std::fs::read(&path)
                .with_context(|| format!("Cannot read SVG: {}", path.display()))?;
            let source = std::path::absolute(path)?;
            (source, input)
        } else {
            let mut input = Vec::new();
            std::io::stdin().lock().read_to_end(&mut input)?;
            (std::env::current_dir()?.join("stdin.svg"), input)
        };
        let svg = std::str::from_utf8(&input)?;
        let cache = super::cache::Cache::for_svg(svg, self.dpi_x);
        let png = match cache.as_ref().and_then(super::cache::Cache::read) {
            Some(png) => png,
            None => {
                let png = super::render(&source, svg, self.dpi_x, 1.0, None)?;
                if let Some(cache) = cache {
                    cache.publish(&png);
                }
                png
            }
        };
        if let Some(path) = self.output.filter(|path| path.as_os_str() != "-") {
            std::fs::write(&path, png)
                .with_context(|| format!("Cannot write PNG: {}", path.display()))?;
        } else {
            std::io::stdout().lock().write_all(&png)?;
        }
        Ok(())
    }
}
