//! Spawn background services without retaining a captured caller's Windows pipes.

use anyhow::Result;
use std::process::{Child, Command};

/// Start a service while preventing a daemon from keeping CLI capture handles alive.
pub fn spawn_background(command: &mut Command) -> Result<Child> {
    #[cfg(windows)]
    let _handles = InheritedStdHandles::clear()?;
    Ok(command.spawn()?)
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
