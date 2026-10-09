//! Stable project identities and managed state without process-wide cwd changes.

use anyhow::{Context, Result};
use sha2::{Digest, Sha256};
use std::path::{Path, PathBuf};

/// Locate Papper's managed root, allowing isolated installations and test projects.
pub fn home_dir() -> PathBuf {
    if let Some(value) = std::env::var_os("PAPPER_HOME") {
        return PathBuf::from(value);
    }
    let home = std::env::var_os("USERPROFILE")
        .or_else(|| std::env::var_os("HOME"))
        .map(PathBuf::from)
        .unwrap_or_else(std::env::temp_dir);
    home.join(".papper")
}

/// Return the directory containing managed native Pandoc and conversion tools.
pub fn tools_bin_dir() -> PathBuf {
    home_dir().join("tools").join("bin")
}

/// Remove Windows extended-path prefixes before hashing or passing paths to Pandoc.
pub fn display_path(path: &Path) -> String {
    let raw = path.to_string_lossy();
    if let Some(unc) = raw.strip_prefix(r"\\?\UNC\") {
        format!(r"\\{unc}")
    } else {
        raw.strip_prefix(r"\\?\").unwrap_or(&raw).to_string()
    }
}

/// Convert a path to Pandoc's forward-slash representation for Markdown metadata.
pub fn pandoc_path(path: &Path) -> String {
    display_path(path).replace('\\', "/")
}

/// Resolve an existing project directory without changing the process working directory.
pub fn canonical_project(path: &Path) -> Result<PathBuf> {
    let canonical = std::fs::canonicalize(path)
        .with_context(|| format!("Could not resolve project: {}", path.display()))?;
    anyhow::ensure!(
        canonical.is_dir(),
        "Project path is not a directory: {}",
        path.display()
    );
    Ok(PathBuf::from(display_path(&canonical)))
}

/// Match the legacy SHA-256 project key, including Windows case normalization.
pub fn project_key(project: &Path) -> Result<String> {
    let canonical = canonical_project(project)?;
    let mut text = display_path(&canonical);
    if cfg!(windows) {
        text = text.replace('/', "\\").to_lowercase();
    }
    let hash: String = Sha256::digest(text.as_bytes())
        .iter()
        .map(|byte| format!("{byte:02x}"))
        .collect();
    Ok(hash[..20].to_string())
}

/// Return persistent state isolated to the canonical project identity.
pub fn project_state_dir(project: &Path) -> Result<PathBuf> {
    Ok(home_dir().join("projects").join(project_key(project)?))
}

/// Keep native state separate from a concurrently running legacy Python service.
pub fn project_work_dir(project: &Path) -> Result<PathBuf> {
    Ok(project_state_dir(project)?.join("work").join("rust-v1"))
}

/// Atomically publish bytes; failed writes leave the previous output intact.
pub fn atomic_write(path: &Path, bytes: &[u8]) -> Result<()> {
    use std::io::Write;
    atomic_write_with(path, |file| {
        file.write_all(bytes)?;
        Ok(())
    })
}

/// Stream a staged output beside its destination and publish only after writing succeeds.
pub fn atomic_write_with(
    path: &Path,
    write: impl FnOnce(&mut std::fs::File) -> Result<()>,
) -> Result<()> {
    use std::io::Write;
    let parent = path
        .parent()
        .filter(|p| !p.as_os_str().is_empty())
        .unwrap_or(Path::new("."));
    std::fs::create_dir_all(parent)?;
    // tempfile's Windows rename API receives paths verbatim. Retain the OS's
    // extended absolute parent so remote-CSL caches and deep output paths can
    // exceed MAX_PATH without truncating or losing their previous valid file.
    #[cfg(windows)]
    let parent = parent.canonicalize()?;
    let destination = parent.join(path.file_name().context("Output path must name a file")?);
    let mut temporary = tempfile::NamedTempFile::new_in(&parent)?;
    write(temporary.as_file_mut())?;
    temporary.flush()?;
    for attempt in 0..=7 {
        match temporary.persist(&destination) {
            Ok(_) => return Ok(()),
            Err(error) => {
                // Concurrent startup and antivirus can briefly open a Windows
                // config without delete sharing. Retry the same complete temp
                // file; never truncate or delete the previously valid output.
                let transient =
                    cfg!(windows) && matches!(error.error.raw_os_error(), Some(5 | 32 | 33));
                if !transient || attempt == 7 {
                    return Err(error.error).with_context(|| {
                        format!(
                            "Could not replace output {}; close applications locking it and retry",
                            path.display()
                        )
                    });
                }
                temporary = error.file;
                std::thread::sleep(std::time::Duration::from_millis((5u64 << attempt).min(80)));
            }
        }
    }
    unreachable!("Every persist attempt either returns or retains its temporary file")
}

/// Publish immutable imported media; a concurrent or user-edited file must never be replaced.
pub fn publish_media(path: &Path, bytes: &[u8]) -> Result<()> {
    use std::io::Write;
    let parent = path.parent().context("Media path must have a parent")?;
    std::fs::create_dir_all(parent)?;
    #[cfg(windows)]
    let parent = parent.canonicalize()?;
    let destination = parent.join(path.file_name().context("Media path must name a file")?);
    let mut temporary = tempfile::NamedTempFile::new_in(parent)?;
    temporary.write_all(bytes)?;
    temporary.flush()?;
    match temporary.persist_noclobber(&destination) {
        Ok(_) => Ok(()),
        Err(error) if destination.exists() => {
            // The Lua collision check and publication can race with another importer.
            // Reuse identical bytes, otherwise fail safely so a retry can choose a new name.
            anyhow::ensure!(
                std::fs::read(&destination)? == bytes,
                "Media changed during import; existing file preserved: {}. Retry the conversion",
                path.display()
            );
            drop(error);
            Ok(())
        }
        Err(error) => {
            Err(error.error).with_context(|| format!("Could not publish media: {}", path.display()))
        }
    }
}

/// Preserve timestamps when a generated configuration has identical content.
pub fn write_if_changed(path: &Path, bytes: &[u8]) -> Result<bool> {
    if std::fs::read(path).ok().as_deref() == Some(bytes) {
        return Ok(false);
    }
    atomic_write(path, bytes)?;
    Ok(true)
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::io::Write;

    /// Interrupted streaming writes and conflicting media imports must preserve prior user data.
    #[test]
    fn failed_stream_and_conflicting_media_preserve_existing_files() -> Result<()> {
        let directory = tempfile::tempdir()?;
        let output = directory.path().join("output.docx");
        atomic_write(&output, b"previous complete document")?;
        let result = atomic_write_with(&output, |file| {
            file.write_all(b"incomplete document")?;
            anyhow::bail!("Simulated conversion failure")
        });
        assert!(result.is_err());
        assert_eq!(std::fs::read(&output)?, b"previous complete document");
        assert!(publish_media(&output, b"different imported image").is_err());
        assert_eq!(std::fs::read(&output)?, b"previous complete document");
        publish_media(&output, b"previous complete document")?;
        atomic_write_with(&output, |file| {
            file.write_all(b"new complete document")?;
            Ok(())
        })?;
        assert_eq!(std::fs::read(output)?, b"new complete document");
        Ok(())
    }
}
