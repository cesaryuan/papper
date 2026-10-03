//! Install an embedded native runtime once, independently of uv's executable copies.

use anyhow::{Context, Result};
use std::io::{Read, Write};
use std::path::PathBuf;

include!(concat!(env!("OUT_DIR"), "/embedded_resources.rs"));

/// Return a previously installed runtime or publish one complete archive atomically.
pub fn runtime_root() -> Result<Option<PathBuf>> {
    if ARCHIVE.is_empty() {
        return Ok(None);
    }
    let parent = crate::paths::home_dir().join("runtime");
    let root = parent.join(ID);
    let ready = |path: &std::path::Path| {
        path.join(".complete").is_file()
            && path.join("pandoc/pandoc-html.yml").is_file()
            && path.join("template/manuscript.md").is_file()
    };
    if ready(&root) {
        return Ok(Some(root));
    }
    std::fs::create_dir_all(&parent)?;
    let temporary = tempfile::Builder::new()
        .prefix("install-")
        .tempdir_in(&parent)?;
    let mut archive = zip::ZipArchive::new(std::io::Cursor::new(ARCHIVE))?;
    for index in 0..archive.len() {
        let mut entry = archive.by_index(index)?;
        let relative = entry
            .enclosed_name()
            .context("Embedded runtime contains an unsafe path")?;
        let output = temporary.path().join(relative);
        if entry.is_dir() {
            std::fs::create_dir_all(output)?;
            continue;
        }
        if let Some(parent) = output.parent() {
            std::fs::create_dir_all(parent)?;
        }
        let mut file = std::fs::File::create(&output)?;
        let mut buffer = [0_u8; 64 * 1024];
        loop {
            let count = entry.read(&mut buffer)?;
            if count == 0 {
                break;
            }
            file.write_all(&buffer[..count])?;
        }
        file.flush()?;
        #[cfg(unix)]
        {
            use std::os::unix::fs::PermissionsExt;
            if let Some(mode) = entry.unix_mode() {
                std::fs::set_permissions(output, std::fs::Permissions::from_mode(mode & 0o777))?;
            }
        }
    }
    std::fs::write(temporary.path().join(".complete"), ID)?;
    anyhow::ensure!(
        ready(temporary.path()),
        "Embedded archive omitted required authored resources"
    );
    // Concurrent first launches may race. Accept only another fully installed
    // copy of this exact content identity, never a partially extracted directory.
    match std::fs::rename(temporary.path(), &root) {
        Ok(()) => {
            let _ = temporary.keep();
        }
        Err(_) if ready(&root) => (),
        Err(error) => {
            return Err(error)
                .context("Could not install embedded runtime; preserving existing files");
        }
    }
    Ok(Some(root))
}
