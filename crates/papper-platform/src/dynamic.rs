//! Load external C libraries with dependencies restricted to their staged directory.

use anyhow::Result;
use libloading::Library;
use std::path::Path;

/// Load a trusted external component without resolving dependencies from the manuscript cwd.
///
/// # Safety
/// The caller must select a trusted compiled library; its initialization can
/// execute native code before its symbols are resolved.
pub unsafe fn load_library(path: &Path) -> Result<Library> {
    let path = path.canonicalize()?;
    #[cfg(windows)]
    {
        use libloading::os::windows::{
            LOAD_LIBRARY_SEARCH_DLL_LOAD_DIR, LOAD_LIBRARY_SEARCH_SYSTEM32,
            Library as WindowsLibrary,
        };
        // MuPDF's adjacent redistributed dependencies must not resolve from cwd.
        Ok(unsafe {
            WindowsLibrary::load_with_flags(
                &path,
                LOAD_LIBRARY_SEARCH_DLL_LOAD_DIR | LOAD_LIBRARY_SEARCH_SYSTEM32,
            )?
        }
        .into())
    }
    #[cfg(not(windows))]
    {
        Ok(unsafe { Library::new(path)? })
    }
}
