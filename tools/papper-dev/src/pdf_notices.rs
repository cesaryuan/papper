//! Stage license and source records for MuPDF linked through the locked Rust crate.

use anyhow::{Context, Result};
use serde_json::{Value, json};
use std::collections::BTreeMap;
use std::path::{Path, PathBuf};
use std::process::Command;

/// Copy notices from the actual resolved dependency, without downloading a Python wheel.
pub fn stage(files: &mut BTreeMap<String, PathBuf>, root: &Path, prefix: &str) -> Result<()> {
    let output = Command::new("cargo")
        .args(["metadata", "--locked", "--format-version", "1"])
        .current_dir(root)
        .output()?;
    anyhow::ensure!(
        output.status.success(),
        "Could not inspect linked MuPDF dependency: {}",
        String::from_utf8_lossy(&output.stderr)
    );
    let metadata: Value = serde_json::from_slice(&output.stdout)?;
    let package = metadata["packages"]
        .as_array()
        .context("Cargo package metadata missing")?
        .iter()
        .find(|package| package["name"] == "mupdf-sys")
        .context("The locked CLI has no mupdf-sys dependency")?;
    let manifest = Path::new(
        package["manifest_path"]
            .as_str()
            .context("MuPDF manifest missing")?,
    );
    let source = manifest.parent().context("MuPDF crate directory missing")?;
    let engine = source.join("mupdf");
    anyhow::ensure!(
        engine.join("COPYING").is_file(),
        "Linked MuPDF source omitted its license"
    );
    for relative in [
        "COPYING",
        "README",
        "AUTHORS",
        "thirdparty/freetype/LICENSE.TXT",
        "thirdparty/freetype/docs/FTL.TXT",
        "thirdparty/freetype/docs/GPLv2.TXT",
        "thirdparty/harfbuzz/COPYING",
        "thirdparty/jbig2dec/LICENSE",
        "thirdparty/jbig2dec/COPYING",
        "thirdparty/lcms2/LICENSE",
        "thirdparty/libjpeg/README.ijg",
        "thirdparty/libjpeg/README",
        "thirdparty/openjpeg/LICENSE",
        "thirdparty/zlib/LICENSE",
    ] {
        let path = engine.join(relative);
        if path.is_file() {
            files.insert(format!("{prefix}/bin/mupdf-notices/{relative}"), path);
        }
    }
    let header = std::fs::read_to_string(engine.join("include/mupdf/fitz/version.h"))?;
    let version = header
        .lines()
        .find_map(|line| line.strip_prefix("#define FZ_VERSION "))
        .context("MuPDF engine version missing")?
        .trim_matches('"');
    let node = metadata["resolve"]["nodes"]
        .as_array()
        .and_then(|nodes| nodes.iter().find(|node| node["id"] == package["id"]))
        .context("Linked MuPDF features missing")?;
    let provenance = json!({
        "crate": package["name"], "crate_version": package["version"],
        "license": package["license"], "repository": package["repository"],
        "crate_source": format!("https://crates.io/api/v1/crates/mupdf-sys/{}/download", package["version"].as_str().context("MuPDF crate version missing")?),
        "engine_version": version, "features": node["features"],
        "linkage": "native engine linked into papper; no separately loaded MuPDF library",
    });
    let record = root.join(".pmt/native-wheel/pdf-notices/SOURCES.json");
    let mut bytes = serde_json::to_vec_pretty(&provenance)?;
    bytes.push(b'\n');
    papper_core::paths::write_if_changed(&record, &bytes)?;
    files.insert(format!("{prefix}/bin/mupdf-notices/SOURCES.json"), record);
    Ok(())
}
