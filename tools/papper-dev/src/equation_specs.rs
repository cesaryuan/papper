//! Require the matching Typst CLI and discard stale upstream specification build outputs.

use anyhow::{Context, Result, ensure};
use std::path::Path;
use std::process::Command;

/// Fail early on missing/mismatched Typst and force MiTeX specs to regenerate for each wheel.
pub fn prepare(root: &Path) -> Result<()> {
    let lock: toml::Value = toml::from_str(&std::fs::read_to_string(root.join("Cargo.lock"))?)?;
    let packages = lock["package"]
        .as_array()
        .context("Cargo.lock omitted packages")?;
    let versions: std::collections::BTreeSet<_> = packages
        .iter()
        .filter(|package| package["name"].as_str() == Some("typst-library"))
        .filter_map(|package| package["version"].as_str())
        .collect();
    ensure!(
        versions.len() == 1,
        "Expected one locked Typst library version"
    );
    let expected = versions.first().unwrap();
    let output = Command::new("typst").arg("--version").output().context(
        "Release wheels require Typst; run uv run --script tools/ci/prepare-typst.py first",
    )?;
    let version = String::from_utf8(output.stdout)?;
    ensure!(
        output.status.success() && version.split_whitespace().nth(1) == Some(*expected),
        "Release wheels require Typst {expected}, found {:?}; run uv run --script tools/ci/prepare-typst.py and add .pmt/typst/bin to PATH",
        version.trim()
    );
    println!(
        "[papper package] Generating MiTeX specifications with {}",
        version.trim()
    );
    // CI may restore a prebuilt-spec build from a machine without Typst. Remove
    // only this release dependency so its build script must run with `generate`.
    super::execute(
        Command::new("cargo")
            .args(["clean", "--release", "-p", "mitex-spec-gen"])
            .current_dir(root),
        "discard cached MiTeX release specifications",
    )
}
