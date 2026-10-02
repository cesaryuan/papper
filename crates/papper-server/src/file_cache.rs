//! Reuse asset bytes only while file identity and OS change metadata stay unchanged.

use std::collections::BTreeMap;
use std::path::{Path, PathBuf};
use std::sync::Arc;

/// Include creation/change time and identity so restored mtime/length cannot hide edits.
#[derive(Clone, Debug, Eq, PartialEq)]
pub enum Stamp {
    Missing,
    File(Vec<u8>),
}

/// Read a conservative change signature; unsupported/error states disable reuse.
pub fn stamp(path: &Path) -> Option<Stamp> {
    let metadata = match std::fs::metadata(path) {
        Ok(metadata) if metadata.is_file() => metadata,
        Ok(_) => return None,
        Err(error) if error.kind() == std::io::ErrorKind::NotFound => return Some(Stamp::Missing),
        Err(_) => return None,
    };
    #[cfg(unix)]
    {
        use std::os::unix::fs::MetadataExt;
        let values = [
            metadata.dev(),
            metadata.ino(),
            metadata.len(),
            metadata.mtime() as u64,
            metadata.mtime_nsec() as u64,
            metadata.ctime() as u64,
            metadata.ctime_nsec() as u64,
        ];
        Some(Stamp::File(
            values.into_iter().flat_map(u64::to_le_bytes).collect(),
        ))
    }
    #[cfg(windows)]
    {
        use std::os::windows::{fs::OpenOptionsExt, io::AsRawHandle};
        use windows_sys::Win32::Storage::FileSystem::{
            FILE_BASIC_INFO, FILE_ID_INFO, FILE_READ_ATTRIBUTES, FILE_SHARE_DELETE,
            FILE_SHARE_READ, FILE_SHARE_WRITE, FileBasicInfo, FileIdInfo,
            GetFileInformationByHandleEx,
        };
        let file = std::fs::OpenOptions::new()
            .access_mode(FILE_READ_ATTRIBUTES)
            .share_mode(FILE_SHARE_READ | FILE_SHARE_WRITE | FILE_SHARE_DELETE)
            .open(path)
            .ok()?;
        let handle = file.as_raw_handle();
        let mut basic: FILE_BASIC_INFO = unsafe { std::mem::zeroed() };
        let mut identity: FILE_ID_INFO = unsafe { std::mem::zeroed() };
        // FILE_READ_ATTRIBUTES avoids scanning data during a hot cache check.
        // Windows ChangeTime changes even when an editor restores LastWriteTime.
        let basic_ok = unsafe {
            GetFileInformationByHandleEx(
                handle,
                FileBasicInfo,
                (&mut basic as *mut FILE_BASIC_INFO).cast(),
                std::mem::size_of::<FILE_BASIC_INFO>() as u32,
            )
        };
        let identity_ok = unsafe {
            GetFileInformationByHandleEx(
                handle,
                FileIdInfo,
                (&mut identity as *mut FILE_ID_INFO).cast(),
                std::mem::size_of::<FILE_ID_INFO>() as u32,
            )
        };
        if basic_ok == 0 || identity_ok == 0 {
            return None;
        }
        let mut bytes = [
            metadata.len(),
            basic.CreationTime as u64,
            basic.LastWriteTime as u64,
            basic.ChangeTime as u64,
            identity.VolumeSerialNumber,
        ]
        .into_iter()
        .flat_map(u64::to_le_bytes)
        .collect::<Vec<_>>();
        bytes.extend(identity.FileId.Identifier);
        Some(Stamp::File(bytes))
    }
    #[cfg(not(any(unix, windows)))]
    {
        let _ = metadata;
        None
    }
}

/// Share validated bytes and digests across graph parsing and the final asset hash.
#[derive(Default)]
pub struct FileCache {
    entries: BTreeMap<PathBuf, (Stamp, Arc<Vec<u8>>, String)>,
    request: BTreeMap<PathBuf, (Arc<Vec<u8>>, String)>,
    digests: BTreeMap<PathBuf, (Stamp, String)>,
    checked: BTreeMap<PathBuf, String>,
    bytes: usize,
}

impl FileCache {
    /// Start a new dependency snapshot; shared graph paths need one validation per request.
    pub fn begin_request(&mut self) {
        self.request.clear();
        self.checked.clear();
    }

    /// Validate large images using identity and a retained digest without keeping their bytes.
    pub fn digest(&mut self, path: &Path) -> std::io::Result<String> {
        use sha2::{Digest, Sha256};
        use std::io::Read;
        if let Some(digest) = self.checked.get(path) {
            return Ok(digest.clone());
        }
        let before = stamp(path);
        if let Some((previous, digest)) = self.digests.get(path)
            && before.as_ref() == Some(previous)
        {
            let digest = digest.clone();
            self.checked.insert(path.to_path_buf(), digest.clone());
            return Ok(digest);
        }
        let mut file = std::fs::File::open(path)?;
        let mut hash = Sha256::new();
        let mut buffer = [0u8; 65536];
        loop {
            let length = file.read(&mut buffer)?;
            if length == 0 {
                break;
            }
            hash.update(&buffer[..length]);
        }
        let digest = hash
            .finalize()
            .iter()
            .map(|byte| format!("{byte:02x}"))
            .collect::<String>();
        self.remember_digest(path, before, &digest);
        Ok(digest)
    }

    /// Keep small digest entries independent of the byte cache's memory budget.
    fn remember_digest(&mut self, path: &Path, before: Option<Stamp>, digest: &str) {
        if let Some(before) = before
            && stamp(path).as_ref() == Some(&before)
        {
            if self.digests.len() >= 2048 {
                self.digests.clear();
            }
            self.digests
                .insert(path.to_path_buf(), (before, digest.to_string()));
        }
        self.checked.insert(path.to_path_buf(), digest.to_string());
    }

    /// Read once per observed change, with bounded retention and no unsafe mtime shortcut.
    pub fn read(&mut self, path: &Path) -> std::io::Result<(Arc<Vec<u8>>, String)> {
        if let Some(value) = self.request.get(path) {
            return Ok(value.clone());
        }
        let before = stamp(path);
        if let Some((previous, bytes, digest)) = self.entries.get(path)
            && before.as_ref() == Some(previous)
        {
            let value = (Arc::clone(bytes), digest.clone());
            self.request.insert(path.to_path_buf(), value.clone());
            return Ok(value);
        }
        let bytes = Arc::new(std::fs::read(path)?);
        let digest = crate::assets::digest(&bytes);
        self.remember_digest(path, before.clone(), &digest);
        // Large figures need only a digest during graph validation. Retaining
        // their bytes exhausted the budget and repeatedly evicted the whole
        // graph for real manuscripts with >32 MiB of image dependencies.
        if let Some(before) = before
            && stamp(path).as_ref() == Some(&before)
            && bytes.len() <= 1024 * 1024
        {
            if self.entries.len() >= 512 || self.bytes + bytes.len() > 32 * 1024 * 1024 {
                self.entries.clear();
                self.bytes = 0;
            }
            if let Some((_, previous, _)) = self.entries.remove(path) {
                self.bytes -= previous.len();
            }
            self.bytes += bytes.len();
            self.entries.insert(
                path.to_path_buf(),
                (before, Arc::clone(&bytes), digest.clone()),
            );
        }
        self.request
            .insert(path.to_path_buf(), (Arc::clone(&bytes), digest.clone()));
        Ok((bytes, digest))
    }
}
