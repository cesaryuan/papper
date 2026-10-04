//! Fingerprint the directly linked equation engine once during compilation.
//!
//! Cargo reruns this script when equation sources, embedded fonts, the dependency
//! lock, or this integration change. The resulting identity is embedded in the
//! executable so DOCX cache lookup never scans source trees or hashes a DLL.

use sha2::{Digest, Sha256};
use std::collections::BTreeSet;
use std::error::Error;
use std::path::{Path, PathBuf};

/// Embed a content identity for the equation implementation and its compiled target.
fn main() -> Result<(), Box<dyn Error>> {
    let package = PathBuf::from(std::env::var("CARGO_MANIFEST_DIR")?);
    let root = package
        .parent()
        .and_then(Path::parent)
        .ok_or("Missing workspace root")?;
    let mut files = BTreeSet::new();
    for directory in [
        "scripts/mathtype-rust/src",
        "scripts/mathtype-rust/assets",
        "scripts/latex2wmf/src",
        "scripts/latex2wmf/assets",
        "crates/papper-platform/src",
        "crates/papper-document/src/docx/mathtype",
    ] {
        collect_files(&root.join(directory), &mut files)?;
    }
    for relative in [
        "Cargo.toml",
        "Cargo.lock",
        "scripts/mathtype-rust/Cargo.toml",
        "scripts/latex2wmf/Cargo.toml",
        "crates/papper-platform/Cargo.toml",
        "crates/papper-platform/build.rs",
        "crates/papper-document/src/docx/mathtype.rs",
    ] {
        files.insert(root.join(relative));
    }
    let mut fingerprint = Sha256::new();
    for variable in [
        "TARGET",
        "PROFILE",
        "CARGO_CFG_TARGET_FEATURE",
        // Generated and upstream prebuilt specs can differ under the same lockfile.
        "CARGO_FEATURE_GENERATE_MITEX_SPEC",
    ] {
        fingerprint.update(variable.as_bytes());
        fingerprint.update([0]);
        fingerprint.update(std::env::var(variable).unwrap_or_default().as_bytes());
        fingerprint.update([0]);
    }
    for file in files {
        println!("cargo:rerun-if-changed={}", file.display());
        fingerprint.update(
            file.strip_prefix(root)?
                .to_string_lossy()
                .replace('\\', "/")
                .as_bytes(),
        );
        fingerprint.update([0]);
        fingerprint.update(std::fs::read(&file)?);
        fingerprint.update([0]);
    }
    let digest: String = fingerprint
        .finalize()
        .iter()
        .map(|byte| format!("{byte:02x}"))
        .collect();
    println!("cargo:rustc-env=PAPPER_EQUATION_ENGINE_FINGERPRINT={digest}");
    Ok(())
}

/// Collect regular files deterministically while excluding symlinked external trees.
fn collect_files(directory: &Path, files: &mut BTreeSet<PathBuf>) -> Result<(), Box<dyn Error>> {
    // Watch the directory too so adding/removing a source invalidates the fingerprint.
    println!("cargo:rerun-if-changed={}", directory.display());
    for entry in std::fs::read_dir(directory)? {
        let entry = entry?;
        let kind = entry.file_type()?;
        if kind.is_dir() {
            collect_files(&entry.path(), files)?;
        } else if kind.is_file() {
            files.insert(entry.path());
        }
    }
    Ok(())
}
