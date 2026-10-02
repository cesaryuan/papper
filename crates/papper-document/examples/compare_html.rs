//! Developer-only HTML migration comparator.
//!
//! Read a JSON case array produced by the Python reference implementation from
//! the first argument, execute the Rust transformation and compare exact output.
//! This program is never used by the installed CLI or its document build path.

use anyhow::{Context, Result, bail};
use papper_document::html::{build_reference_style_css_text, postprocess_html_text};
use serde_json::Value;

/// Compare exact typography and processed HTML against a reference fixture file.
fn main() -> Result<()> {
    let file = std::env::args()
        .nth(1)
        .context("Expected a JSON comparison fixture")?;
    let cases: Value = serde_json::from_str(&std::fs::read_to_string(file)?)?;
    let mut failures = 0;
    for case in cases
        .as_array()
        .context("Fixture must contain a case array")?
    {
        let name = case["name"].as_str().unwrap_or("unnamed");
        let actual = if case["mode"] == "css" {
            build_reference_style_css_text(
                case["input"].as_str().context("Missing XML")?,
                case.get("docx_style"),
            )?
        } else {
            postprocess_html_text(
                case["input"].as_str().context("Missing HTML")?,
                &case["metadata"],
                case["skip"].as_bool().unwrap_or(false),
            )?
        };
        let expected = case["expected"]
            .as_str()
            .context("Missing reference output")?;
        if actual != expected {
            failures += 1;
            let offset = actual
                .chars()
                .zip(expected.chars())
                .position(|(left, right)| left != right)
                .unwrap_or(actual.chars().count().min(expected.chars().count()));
            eprintln!(
                "{name}: mismatch at character {offset}; actual {} bytes, expected {} bytes",
                actual.len(),
                expected.len()
            );
            eprintln!(
                "Actual: {:?}",
                actual
                    .chars()
                    .skip(offset.saturating_sub(50))
                    .take(200)
                    .collect::<String>()
            );
            eprintln!(
                "Expected: {:?}",
                expected
                    .chars()
                    .skip(offset.saturating_sub(50))
                    .take(200)
                    .collect::<String>()
            );
        }
    }
    if failures > 0 {
        bail!("{failures} HTML/CSS migration cases differed")
    }
    println!(
        "All {} exact HTML/CSS comparisons passed",
        cases.as_array().unwrap().len()
    );
    Ok(())
}
