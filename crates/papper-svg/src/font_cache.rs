//! Cache font identities while validating the renderer's complete Windows font inventory.
//!
//! Windows fontdb 0.23 scans three known roots. Preserve their enumeration order
//! and check file identity plus ChangeTime before reusing a byte-derived font
//! digest. This avoids reparsing/hashing every installed font on each DOCX edit.
//! Symlinks, inaccessible inventory entries and unsupported metadata disable reuse.

use anyhow::{Context, Result, bail, ensure};
use sha2::{Digest, Sha256};
use std::io::Write;
use std::os::windows::{ffi::OsStrExt, fs::OpenOptionsExt, io::AsRawHandle};
use std::path::{Path, PathBuf};
use windows_sys::Win32::Storage::FileSystem::{
    FILE_BASIC_INFO, FILE_ID_INFO, FILE_READ_ATTRIBUTES, FILE_SHARE_DELETE, FILE_SHARE_READ,
    FILE_SHARE_WRITE, FileBasicInfo, FileIdInfo, GetFileInformationByHandleEx,
};

/// Treat optional cache failures as misses and retain the full font-byte identity contract.
pub(super) fn identity(compute: impl FnOnce() -> Result<String>) -> Result<String> {
    let directory = std::env::var_os("PAPPER_SVG_CACHE_DIR")
        .filter(|value| !value.is_empty())
        .map(PathBuf::from);
    let Some(directory) =
        directory.filter(|_| !std::env::var("PAPPER_SVG_CACHE").is_ok_and(|value| value == "0"))
    else {
        return compute();
    };
    let before = match snapshot() {
        Ok(value) => value,
        Err(error) => {
            trace(&format!("Font identity cache bypassed: {error:#}"));
            return compute();
        }
    };
    let path = directory.join("font-identity-v1.meta");
    if let Some(identity) = read(&path, &before) {
        trace("Font identity cache hit");
        return Ok(identity);
    }
    trace("Font identity cache miss; scanning font bytes");
    let identity = compute()?;
    // Fonts can be installed/replaced during the scan. Do not publish an identity
    // derived from mixed states; the next invocation must retry the full scan.
    if snapshot().is_ok_and(|after| after == before)
        && let Err(error) = publish(&path, &before, &identity)
    {
        trace(&format!("Cannot publish font identity cache: {error:#}"));
    }
    Ok(identity)
}

/// Emit cache diagnostics only when requested, preserving binary stdout and quiet hot builds.
fn trace(message: &str) {
    if std::env::var("PAPPER_SVG_TRACE").is_ok_and(|value| value == "1") {
        eprintln!("[papper-svg] {message}");
    }
}

/// Match fontdb 0.23's Windows roots, traversal order and supported extensions.
fn snapshot() -> Result<String> {
    let mut hash = Sha256::new();
    // Bind the inventory to the exact parser/default-family implementation.
    hash.update(b"windows-fontdb-0.23-inventory-v1\0");
    hash.update(super::cache::executable_identity()?.as_bytes());
    let system = std::env::var_os("SYSTEMROOT")
        .map(PathBuf::from)
        .unwrap_or_else(|| PathBuf::from(r"C:\Windows"));
    inventory(&system.join("Fonts"), &mut hash)?;
    if let Ok(home) = std::env::var("USERPROFILE") {
        let home = PathBuf::from(home);
        inventory(
            &home.join(r"AppData\Local\Microsoft\Windows\Fonts"),
            &mut hash,
        )?;
        inventory(
            &home.join(r"AppData\Roaming\Microsoft\Windows\Fonts"),
            &mut hash,
        )?;
    }
    Ok(hex(hash.finalize().as_slice()))
}

/// Include absent roots and ordered font entries; uncertain traversal cannot be a cache hit.
fn inventory(directory: &Path, hash: &mut Sha256) -> Result<()> {
    hash.update([0]);
    hash_path(directory, hash);
    let metadata = match std::fs::symlink_metadata(directory) {
        Ok(metadata) => metadata,
        Err(error) if error.kind() == std::io::ErrorKind::NotFound => {
            hash.update(b"missing");
            return Ok(());
        }
        Err(error) => return Err(error.into()),
    };
    ensure!(
        !metadata.file_type().is_symlink(),
        "Linked font directories require a full scan"
    );
    // A configured root can be a file. fontdb ignores it, but track its identity
    // so replacing it by a directory cannot reuse the preceding inventory.
    if !metadata.is_dir() {
        hash.update(b"not-directory");
        return Ok(());
    }
    hash.update(b"directory");
    for entry in std::fs::read_dir(directory)? {
        let entry = entry?;
        let kind = entry.file_type()?;
        ensure!(
            !kind.is_symlink(),
            "Linked font entries require a full scan"
        );
        if kind.is_dir() {
            inventory(&entry.path(), hash)?;
        } else if kind.is_file()
            && matches!(
                entry.path().extension().and_then(|value| value.to_str()),
                Some("ttf" | "ttc" | "otf" | "otc" | "TTF" | "TTC" | "OTF" | "OTC")
            )
        {
            hash.update([1]);
            hash_path(&entry.path(), hash);
            hash_file_state(&entry.path(), hash)?;
        }
    }
    hash.update([2]);
    Ok(())
}

/// Encode native Unicode paths with a length prefix to avoid ambiguous inventory concatenation.
fn hash_path(path: &Path, hash: &mut Sha256) {
    let units = path.as_os_str().encode_wide().collect::<Vec<_>>();
    hash.update((units.len() as u64).to_le_bytes());
    for unit in units {
        hash.update(unit.to_le_bytes());
    }
}

/// Detect same-size edits even when mtime is restored, without reading font contents.
fn hash_file_state(path: &Path, hash: &mut Sha256) -> Result<()> {
    let file = std::fs::OpenOptions::new()
        .access_mode(FILE_READ_ATTRIBUTES)
        .share_mode(FILE_SHARE_READ | FILE_SHARE_WRITE | FILE_SHARE_DELETE)
        .open(path)?;
    let mut basic: FILE_BASIC_INFO = unsafe { std::mem::zeroed() };
    let mut identity: FILE_ID_INFO = unsafe { std::mem::zeroed() };
    // The opened handle pins file identity while metadata is read, including replacement races.
    let basic_ok = unsafe {
        GetFileInformationByHandleEx(
            file.as_raw_handle(),
            FileBasicInfo,
            (&mut basic as *mut FILE_BASIC_INFO).cast(),
            std::mem::size_of::<FILE_BASIC_INFO>() as u32,
        )
    };
    let identity_ok = unsafe {
        GetFileInformationByHandleEx(
            file.as_raw_handle(),
            FileIdInfo,
            (&mut identity as *mut FILE_ID_INFO).cast(),
            std::mem::size_of::<FILE_ID_INFO>() as u32,
        )
    };
    ensure!(
        basic_ok != 0 && identity_ok != 0 && basic.ChangeTime > 0,
        "Reliable font change metadata unavailable: {}",
        path.display()
    );
    for value in [
        file.metadata()?.len(),
        basic.CreationTime as u64,
        basic.LastWriteTime as u64,
        basic.ChangeTime as u64,
        identity.VolumeSerialNumber,
    ] {
        hash.update(value.to_le_bytes());
    }
    hash.update(identity.FileId.Identifier);
    Ok(())
}

/// Reject stale, truncated or edited identity records without treating cache damage as a build failure.
fn read(path: &Path, inventory: &str) -> Option<String> {
    let raw = std::fs::read_to_string(path).ok()?;
    let fields = raw.lines().collect::<Vec<_>>();
    if fields.len() != 3
        || fields[0] != inventory
        || !fields
            .iter()
            .all(|field| field.len() == 64 && field.bytes().all(|byte| byte.is_ascii_hexdigit()))
        || checksum(fields[0], fields[1]) != fields[2]
    {
        return None;
    }
    Some(fields[1].to_owned())
}

/// Protect the small cache record against accidental edits independently of its inventory key.
fn checksum(inventory: &str, identity: &str) -> String {
    let mut hash = Sha256::new();
    hash.update(b"papper-font-identity-v1\0");
    hash.update(inventory.as_bytes());
    hash.update(identity.as_bytes());
    hex(hash.finalize().as_slice())
}

/// Publish beside the destination so concurrent builds never observe partial identities.
fn publish(path: &Path, inventory: &str, identity: &str) -> Result<()> {
    if identity.len() != 64 || !identity.bytes().all(|byte| byte.is_ascii_hexdigit()) {
        bail!("Invalid computed font identity");
    }
    let parent = path.parent().context("Font cache directory missing")?;
    std::fs::create_dir_all(parent)?;
    let mut file = tempfile::NamedTempFile::new_in(parent)?;
    write!(
        file,
        "{inventory}\n{identity}\n{}\n",
        checksum(inventory, identity)
    )?;
    file.flush()?;
    file.persist(path)?;
    Ok(())
}

/// Encode digests without adding a serialization dependency to the resource helper.
fn hex(bytes: &[u8]) -> String {
    bytes.iter().map(|byte| format!("{byte:02x}")).collect()
}
