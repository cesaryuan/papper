//! Discover resource roots independently of a user's manuscript cwd.

use anyhow::{Result, bail};
use std::path::{Path, PathBuf};

/// Identify the compiled image implementation without hashing a helper on every build.
pub fn svg_renderer_id() -> &'static str {
    env!("PAPPER_SVG_RENDERER_ID")
}

/// Runtime resources used by native commands and the retained engine.
#[derive(Debug, Clone)]
pub struct ResourcePaths {
    pub root: PathBuf,
    pub pandoc: PathBuf,
    pub template: PathBuf,
}

impl ResourcePaths {
    /// Discover explicit, installed or development resources in that order.
    pub fn discover() -> Result<Self> {
        let mut candidates = Vec::new();
        if let Some(root) = std::env::var_os("PAPPER_RESOURCE_ROOT") {
            let root = PathBuf::from(root);
            // Explicit authored resources must not depend on a writable managed
            // home or on extracting the optional embedded release archive.
            if root.join("pandoc/pandoc-html.yml").is_file() {
                return Self::from_root(root);
            }
            candidates.push(root);
        }
        if let Some(root) = super::embedded::runtime_root()? {
            candidates.push(root);
        }
        if let Ok(exe) = std::env::current_exe()
            && let Some(parent) = exe.parent()
        {
            candidates.extend([
                parent.to_path_buf(),
                parent.join("resources"),
                parent.join("papper"),
                parent.join("../share/papper"),
                parent.join("share/papper"),
            ]);
        }
        // Source builds resolve resources from the crate, never from a manuscript cwd.
        candidates.push(Path::new(env!("CARGO_MANIFEST_DIR")).join("../.."));
        for root in candidates {
            if root.join("pandoc/pandoc-html.yml").is_file() {
                return Self::from_root(root);
            }
        }
        bail!(
            "Could not locate Papper resources; set PAPPER_RESOURCE_ROOT to a complete runtime resource directory"
        )
    }

    /// Normalize one complete authored runtime without changing the caller's cwd.
    fn from_root(root: PathBuf) -> Result<Self> {
        let root = PathBuf::from(super::paths::display_path(&std::fs::canonicalize(root)?));
        let template = if root.join("template/manuscript.md").is_file() {
            root.join("template")
        } else {
            root.join("_template")
        };
        Ok(Self {
            pandoc: root.join("pandoc"),
            template,
            root,
        })
    }

    /// Resolve a bundled resource while preserving absolute overrides.
    pub fn resource(&self, relative: impl AsRef<Path>) -> PathBuf {
        let relative = relative.as_ref();
        if relative.is_absolute() {
            relative.to_path_buf()
        } else {
            self.root.join(relative)
        }
    }

    /// Find the small image helper without copying or relaunching the main executable.
    pub fn svg_renderer(&self) -> Option<PathBuf> {
        self.native_image_helper("papper-svg", std::env::var_os("PAPPER_SVG_RENDERER"))
    }

    /// Locate the bundled Pandoc PNG adapter without selecting a system librsvg tool.
    pub fn svg_converter(&self) -> Option<PathBuf> {
        self.native_image_helper("rsvg-convert", None)
    }

    /// Share installed and source-build discovery between the image renderer and adapter.
    fn native_image_helper(
        &self,
        name: &str,
        explicit: Option<std::ffi::OsString>,
    ) -> Option<PathBuf> {
        let name = if cfg!(windows) {
            format!("{name}.exe")
        } else {
            name.into()
        };
        let mut candidates = Vec::new();
        if let Some(path) = explicit {
            candidates.push(PathBuf::from(path));
        }
        candidates.push(self.root.join("bin").join(&name));
        if let Ok(executable) = std::env::current_exe()
            && let Some(parent) = executable.parent()
        {
            candidates.push(parent.join(&name));
        }
        let target = std::env::var_os("CARGO_TARGET_DIR")
            .map(PathBuf::from)
            .map(|path| {
                if path.is_absolute() {
                    path
                } else {
                    self.root.join(path)
                }
            })
            .unwrap_or_else(|| self.root.join("target"));
        candidates.extend([
            target.join("debug").join(&name),
            target.join("release").join(&name),
        ]);
        candidates.into_iter().find(|path| path.is_file())
    }
}
