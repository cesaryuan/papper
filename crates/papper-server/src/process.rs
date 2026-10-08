//! Spawn background services without retaining a captured caller's Windows pipes.

use anyhow::{Context, Result};
use papper_core::paths::home_dir;
use sha2::{Digest, Sha256};
use std::fs;
use std::io::{Read, Seek, Write};
use std::path::{Path, PathBuf};
use std::process::{Child, Command};

pub(crate) const SERVER_UPDATE_SOURCE_ENV: &str = "PAPPER_SERVER_UPDATE_SOURCE";

/// Start a service while preventing a daemon from keeping CLI capture handles alive.
pub fn spawn_background(command: &mut Command) -> Result<Child> {
    #[cfg(windows)]
    let _handles = InheritedStdHandles::clear()?;
    Ok(command.spawn()?)
}

/// Keep the hashed image open so publication copies exactly the inspected binary.
pub(crate) struct ServerExecutable {
    pub identity: String,
    pub source_path: PathBuf,
    source: fs::File,
}

impl ServerExecutable {
    /// Hash the actual program, including rebuilds carrying the same package version.
    pub(crate) fn current() -> Result<Self> {
        Self::from_path(&std::env::current_exe()?)
    }

    /// Hash one installed entry point while retaining the exact image for publication.
    pub(crate) fn from_path(path: &Path) -> Result<Self> {
        let source_path = if path.is_absolute() {
            path.to_path_buf()
        } else {
            std::env::current_dir()?.join(path)
        };
        let mut source = fs::File::open(&source_path)?;
        let mut hash = Sha256::new();
        let mut buffer = [0_u8; 64 * 1024];
        loop {
            let count = source.read(&mut buffer)?;
            if count == 0 {
                break;
            }
            hash.update(&buffer[..count]);
        }
        let identity = hash
            .finalize()
            .iter()
            .map(|byte| format!("{byte:02x}"))
            .collect();
        Ok(Self {
            identity,
            source_path,
            source,
        })
    }

    /// Atomically publish an independent executable without replacing running copies.
    pub(crate) fn install(&mut self) -> Result<PathBuf> {
        let directory = home_dir().join("server-runtimes").join(&self.identity);
        fs::create_dir_all(&directory)?;
        let directory = directory.canonicalize()?;
        let target = directory.join(if cfg!(windows) {
            "papper.exe"
        } else {
            "papper"
        });
        if target.is_file() {
            return Ok(target);
        }
        // Windows locks running uv entry points. A real copy (never a hard link)
        // releases that entry point when the CLI exits, allowing tool upgrades.
        let mut temporary = tempfile::NamedTempFile::new_in(&directory)?;
        self.source.rewind()?;
        std::io::copy(&mut self.source, temporary.as_file_mut())?;
        temporary.flush()?;
        #[cfg(unix)]
        fs::set_permissions(temporary.path(), self.source.metadata()?.permissions())?;
        // Concurrent first launches may publish the same hash. Never overwrite
        // the winning executable: it may already be running on Windows.
        match temporary.persist_noclobber(&target) {
            Ok(_) => Ok(target),
            Err(error) if error.error.kind() == std::io::ErrorKind::AlreadyExists => Ok(target),
            Err(error) => {
                Err(error.error).context("Could not publish background server executable")
            }
        }
    }
}

/// Restore caller-owned handle flags after the child has inherited only its explicit stdio.
#[cfg(windows)]
struct InheritedStdHandles(Vec<(windows_sys::Win32::Foundation::HANDLE, u32)>);

#[cfg(windows)]
impl InheritedStdHandles {
    /// Temporarily clear inheritance on original stdio, not the child's log handles.
    fn clear() -> Result<Self> {
        use windows_sys::Win32::Foundation::{
            GetHandleInformation, HANDLE_FLAG_INHERIT, INVALID_HANDLE_VALUE, SetHandleInformation,
        };
        use windows_sys::Win32::System::Console::{
            GetStdHandle, STD_ERROR_HANDLE, STD_INPUT_HANDLE, STD_OUTPUT_HANDLE,
        };
        let mut guard = Self(Vec::new());
        for kind in [STD_INPUT_HANDLE, STD_OUTPUT_HANDLE, STD_ERROR_HANDLE] {
            // A captured CLI receives inheritable parent pipe handles. Rust's
            // redirected Command stdio does not stop other inheritable handles
            // from entering a daemon, so its caller waits forever for pipe EOF.
            let handle = unsafe { GetStdHandle(kind) };
            if handle.is_null() || handle == INVALID_HANDLE_VALUE {
                continue;
            }
            let mut flags = 0;
            if unsafe { GetHandleInformation(handle, &mut flags) } == 0 {
                continue;
            }
            if flags & HANDLE_FLAG_INHERIT != 0 {
                if unsafe { SetHandleInformation(handle, HANDLE_FLAG_INHERIT, 0) } == 0 {
                    return Err(std::io::Error::last_os_error().into());
                }
                guard.0.push((handle, flags));
            }
        }
        Ok(guard)
    }
}

#[cfg(windows)]
impl Drop for InheritedStdHandles {
    /// Leave the caller's stdout/stderr usable after the independent child starts.
    fn drop(&mut self) {
        use windows_sys::Win32::Foundation::{HANDLE_FLAG_INHERIT, SetHandleInformation};
        for &(handle, flags) in &self.0 {
            // Handles belong to this still-running process and must never be closed here.
            let _ = unsafe { SetHandleInformation(handle, HANDLE_FLAG_INHERIT, flags) };
        }
    }
}
