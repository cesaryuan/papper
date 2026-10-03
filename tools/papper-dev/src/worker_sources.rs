//! Prepare pinned Pandoc sources with the tracked Reader/Writer registry overrides.
//!
//! Cabal compiles a private source copy under .pmt, not the upstream package cache.
//! Wheels keep these overrides and source hashes so the linked profile is reproducible.

use anyhow::{Context, Result, ensure};
use flate2::read::GzDecoder;
use serde_json::Value;
use sha2::{Digest, Sha256};
use std::collections::BTreeSet;
use std::io::Read;
use std::path::{Path, PathBuf};
use std::process::Command;
use std::time::Duration;

/// Load the versioned profile alongside its maintained upstream module overrides.
fn profile(root: &Path) -> Result<(PathBuf, Value)> {
    let directory = root.join("scripts/pandoc-server/vendor/pandoc");
    let metadata = serde_json::from_slice(&std::fs::read(directory.join("SOURCES.json"))?)?;
    Ok((directory, metadata))
}

/// Require a source identity field instead of silently resolving an unpinned version.
fn field<'a>(profile: &'a Value, name: &str) -> Result<&'a str> {
    profile[name]
        .as_str()
        .with_context(|| format!("Pandoc source profile omitted {name}"))
}

/// Hash upstream archives and actual override bytes using the same identity format.
fn sha256(bytes: &[u8]) -> String {
    Sha256::digest(bytes)
        .iter()
        .map(|byte| format!("{byte:02x}"))
        .collect()
}

/// Reuse a matching Hackage archive without editing Cabal's downloaded source cache.
fn cabal_archive(name: &str, version: &str, expected: &str) -> Option<Vec<u8>> {
    let output = Command::new("cabal")
        .args(["path", "--output-format=json"])
        .output()
        .ok()?;
    if !output.status.success() {
        return None;
    }
    let paths: Value = serde_json::from_slice(&output.stdout).ok()?;
    let directory = Path::new(paths["remote-repo-cache"].as_str()?);
    for repository in std::fs::read_dir(directory).ok()?.flatten() {
        let archive = repository
            .path()
            .join(name)
            .join(version)
            .join(format!("{name}-{version}.tar.gz"));
        if let Ok(bytes) = std::fs::read(archive)
            && sha256(&bytes) == expected
        {
            return Some(bytes);
        }
    }
    None
}

/// Cache only the verified upstream tarball, with a bounded network fallback.
fn upstream_archive(root: &Path, profile: &Value) -> Result<Vec<u8>> {
    let name = field(profile, "name")?;
    let version = field(profile, "version")?;
    let expected = field(profile, "source_sha256")?;
    let cache = root
        .join(".pmt/pandoc-source")
        .join(format!("{name}-{version}.tar.gz"));
    let bytes = if cache.is_file() {
        std::fs::read(&cache)?
    } else if let Some(bytes) = cabal_archive(name, version, expected) {
        bytes
    } else {
        println!("[papper package] Download pinned Pandoc {version} source");
        let response = ureq::AgentBuilder::new()
            .timeout(Duration::from_secs(60))
            .build()
            .get(field(profile, "source")?)
            .call()?;
        let mut bytes = Vec::new();
        // The pinned tarball is about 9 MB; do not accept an unbounded error body.
        response
            .into_reader()
            .take(64 * 1024 * 1024)
            .read_to_end(&mut bytes)?;
        bytes
    };
    ensure!(
        sha256(&bytes) == expected,
        "Pandoc {version} source SHA-256 mismatch; refusing unpinned sources"
    );
    papper_core::paths::write_if_changed(&cache, &bytes)?;
    Ok(bytes)
}

/// Install the original package once, then update only the maintained registries.
pub fn prepare(root: &Path) -> Result<()> {
    let (overrides, metadata) = profile(root)?;
    let name = field(&metadata, "name")?;
    let version = field(&metadata, "version")?;
    let source_name = format!("{name}-{version}");
    let parent = root.join(".pmt/pandoc-source");
    let destination = parent.join(&source_name);
    let expected = field(&metadata, "source_sha256")?;
    let marker = ".papper-upstream.sha256";
    if destination.exists() {
        ensure!(
            std::fs::read_to_string(destination.join(marker))
                .ok()
                .as_deref()
                == Some(expected),
            "Prepared Pandoc sources have an unknown origin: {}",
            destination.display()
        );
    } else {
        let bytes = upstream_archive(root, &metadata)?;
        std::fs::create_dir_all(&parent)?;
        let temporary = tempfile::Builder::new()
            .prefix("pandoc-source-")
            .tempdir_in(&parent)?;
        tar::Archive::new(GzDecoder::new(bytes.as_slice())).unpack(temporary.path())?;
        let source = temporary.path().join(&source_name);
        ensure!(
            source.join("pandoc.cabal").is_file(),
            "Pandoc tarball omitted its package"
        );
        std::fs::write(source.join(marker), expected)?;
        // Publish a complete source tree; a parallel preparer may win this rename.
        match std::fs::rename(&source, &destination) {
            Ok(()) => (),
            Err(_)
                if std::fs::read_to_string(destination.join(marker))
                    .ok()
                    .as_deref()
                    == Some(expected) => {}
            Err(error) => return Err(error).context("Could not publish prepared Pandoc sources"),
        }
    }
    for path in metadata["overrides"]
        .as_array()
        .context("Pandoc profile omitted registry overrides")?
    {
        let path = path.as_str().context("Invalid Pandoc override path")?;
        papper_core::paths::write_if_changed(
            &destination.join(path),
            &std::fs::read(overrides.join(path))?,
        )?;
    }
    println!("[papper package] Prepared Pandoc {version} with Papper's format registries");
    Ok(())
}

/// Collect the declared formats as a set for complete prebuilt-worker verification.
fn formats(profile: &Value, key: &str) -> Result<BTreeSet<String>> {
    profile[key]
        .as_array()
        .with_context(|| format!("Pandoc profile omitted {key}"))?
        .iter()
        .map(|item| {
            item.as_str()
                .map(str::to_owned)
                .context("Invalid Pandoc format name")
        })
        .collect()
}

/// Reject a stale/full prebuilt Worker instead of silently publishing the wrong profile.
pub fn verify_formats(root: &Path, executable: &Path) -> Result<()> {
    let (_, metadata) = profile(root)?;
    for (key, argument) in [
        ("readers", "--list-input-formats"),
        ("writers", "--list-output-formats"),
    ] {
        let mut expected = formats(&metadata, key)?;
        if key == "writers" {
            expected.extend(formats(&metadata, "synthetic_writers")?);
        }
        let output = Command::new(executable).arg(argument).output()?;
        ensure!(
            output.status.success(),
            "Worker {argument} failed: {}",
            String::from_utf8_lossy(&output.stderr)
        );
        let actual: BTreeSet<_> = String::from_utf8(output.stdout)?
            .lines()
            .map(str::to_owned)
            .collect();
        ensure!(
            actual == expected,
            "Worker format profile mismatch for {argument}; missing {:?}, extra {:?}; rebuild with papper-dev worker",
            expected.difference(&actual).collect::<Vec<_>>(),
            actual.difference(&expected).collect::<Vec<_>>()
        );
    }
    Ok(())
}

/// Publish an immutable Worker copy and atomically select it for source commands.
pub fn publish(root: &Path, executable: &Path) -> Result<PathBuf> {
    let bytes = std::fs::read(executable)?;
    let identity = sha256(&bytes);
    let name = executable
        .file_name()
        .context("Worker executable has no filename")?;
    let relative = Path::new("runtime").join(&identity).join(name);
    let directory = root.join(".pmt/pandoc-worker");
    let destination = directory.join(&relative);
    // Identical builds may already be serving requests; leave their file untouched.
    if !destination.is_file() {
        papper_core::paths::atomic_write(&destination, &bytes)?;
    }
    #[cfg(unix)]
    // Atomic file creation does not inherit the executable mode on Unix.
    std::fs::set_permissions(&destination, std::fs::metadata(executable)?.permissions())?;
    let record = serde_json::json!({
        "executable": relative.to_string_lossy().replace('\\', "/"),
        "sha256": identity,
    });
    papper_core::paths::write_if_changed(
        &directory.join("current.json"),
        &serde_json::to_vec_pretty(&record)?,
    )?;
    Ok(destination)
}

/// Record the exact upstream identity and override hashes distributed with the wheel.
pub fn provenance(root: &Path) -> Result<Value> {
    let (directory, mut metadata) = profile(root)?;
    let mut hashes = serde_json::Map::new();
    for relative in metadata["overrides"]
        .as_array()
        .context("Pandoc overrides missing")?
    {
        let relative = relative.as_str().context("Invalid Pandoc override path")?;
        hashes.insert(
            relative.into(),
            sha256(&std::fs::read(directory.join(relative))?).into(),
        );
    }
    metadata["override_sha256"] = Value::Object(hashes);
    Ok(metadata)
}
