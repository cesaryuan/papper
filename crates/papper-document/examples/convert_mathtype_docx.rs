//! Development runner for complete native MathType package conversion.
//!
//! Usage: convert_mathtype_docx INPUT OUTPUT EFFECTIVE_METADATA_JSON PROJECT
//! It converts hidden marker-bound Word equations through directly linked Rust
//! libraries without starting Python or controlling an Office application.

use anyhow::{Context, Result};
use papper_core::metadata::EffectiveMetadata;
use papper_core::resources::ResourcePaths;
use papper_document::docx::convert_marked_docx;
use std::path::PathBuf;

/// Read prepared effective metadata and expose the conversion report for artifact tests.
fn main() -> Result<()> {
    let arguments: Vec<_> = std::env::args_os().skip(1).collect();
    anyhow::ensure!(
        arguments.len() == 4,
        "Usage: convert_mathtype_docx INPUT OUTPUT EFFECTIVE_METADATA_JSON PROJECT"
    );
    let metadata: EffectiveMetadata =
        serde_json::from_slice(&std::fs::read(PathBuf::from(&arguments[2]))?)
            .context("Invalid effective metadata JSON")?;
    let report = convert_marked_docx(
        &PathBuf::from(&arguments[0]),
        &PathBuf::from(&arguments[1]),
        &ResourcePaths::discover()?,
        &metadata,
        &PathBuf::from(&arguments[3]),
    )?;
    println!(
        "{}",
        serde_json::json!({"total":report.total,"converted":report.converted,"failures":report.failures,"cache_hits":report.cache_hits})
    );
    Ok(())
}
