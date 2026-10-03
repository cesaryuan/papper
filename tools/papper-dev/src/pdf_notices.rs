//! Stage the licenses and locked source records for the Rust PDF backend.

use anyhow::{Context, Result};
use serde_json::{Value, json};
use std::collections::BTreeMap;
use std::path::{Path, PathBuf};
use std::process::Command;

/// Retain upstream notices for the linked PDF engine and its required Office module.
pub fn stage(files: &mut BTreeMap<String, PathBuf>, root: &Path, prefix: &str) -> Result<()> {
    let output = Command::new("cargo")
        .args(["metadata", "--locked", "--format-version", "1"])
        .current_dir(root)
        .output()?;
    anyhow::ensure!(
        output.status.success(),
        "Could not inspect PDF dependencies: {}",
        String::from_utf8_lossy(&output.stderr)
    );
    let metadata: Value = serde_json::from_slice(&output.stdout)?;
    let packages = metadata["packages"]
        .as_array()
        .context("Cargo package metadata missing")?;
    let nodes = metadata["resolve"]["nodes"]
        .as_array()
        .context("Cargo resolution missing")?;
    let mut sources = Vec::new();
    for name in ["pdf_oxide", "office_oxide"] {
        let package = packages
            .iter()
            .find(|package| package["name"] == name)
            .with_context(|| format!("The locked CLI has no {name} dependency"))?;
        let manifest = Path::new(
            package["manifest_path"]
                .as_str()
                .context("PDF crate manifest missing")?,
        );
        let source = manifest.parent().context("PDF crate directory missing")?;
        for required in ["LICENSE-MIT", "LICENSE-APACHE"] {
            let path = source.join(required);
            anyhow::ensure!(path.is_file(), "PDF crate {name} omitted {required}");
            files.insert(format!("{prefix}/bin/pdf-notices/{name}/{required}"), path);
        }
        for optional in ["NOTICE", "TRADEMARKS.md"] {
            let path = source.join(optional);
            if path.is_file() {
                files.insert(format!("{prefix}/bin/pdf-notices/{name}/{optional}"), path);
            }
        }
        let node = nodes
            .iter()
            .find(|node| node["id"] == package["id"])
            .context("Linked PDF features missing")?;
        let version = package["version"]
            .as_str()
            .context("PDF crate version missing")?;
        sources.push(json!({
            "crate": name, "crate_version": version,
            "license": package["license"], "repository": package["repository"],
            "crate_source": format!("https://crates.io/api/v1/crates/{name}/{version}/download"),
            "features": node["features"],
        }));
    }
    let record = root.join(".pmt/native-wheel/pdf-notices/SOURCES.json");
    let mut bytes = serde_json::to_vec_pretty(&json!({
        "packages": sources, "linkage": "Rust PDF engine linked into papper",
    }))?;
    bytes.push(b'\n');
    papper_core::paths::write_if_changed(&record, &bytes)?;
    files.insert(format!("{prefix}/bin/pdf-notices/SOURCES.json"), record);
    Ok(())
}
