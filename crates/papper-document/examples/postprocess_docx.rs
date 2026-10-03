//! Development runner for standalone OOXML compatibility checks.
//!
//! Usage: cargo run -p papper-document --example postprocess_docx -- INPUT OUTPUT JSON
//! JSON is an EffectiveMetadata document. The runner executes the native package
//! editor directly so tests can supply crafted documents without invoking Pandoc.

use anyhow::{Context, Result};
use papper_core::metadata::EffectiveMetadata;
use papper_document::docx::{DocxPostprocessOptions, postprocess_docx};
use std::path::PathBuf;

/// Load a prepared metadata snapshot and process one document independently of the CLI.
fn main() -> Result<()> {
    let arguments: Vec<_> = std::env::args_os().skip(1).collect();
    anyhow::ensure!(
        arguments.len() == 3,
        "Usage: postprocess_docx INPUT OUTPUT EFFECTIVE_METADATA_JSON"
    );
    let metadata: EffectiveMetadata =
        serde_json::from_slice(&std::fs::read(PathBuf::from(&arguments[2]))?)
            .context("Invalid effective metadata JSON")?;
    postprocess_docx(
        &PathBuf::from(&arguments[0]),
        &PathBuf::from(&arguments[1]),
        &metadata,
        &DocxPostprocessOptions {
            skip_author_info: true,
            ..Default::default()
        },
    )
}
