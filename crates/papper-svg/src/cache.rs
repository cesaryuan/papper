//! Reuse dependency-safe Pandoc fallback PNGs before loading fonts or rendering.
//!
//! Each entry contains a SHA-256 checksum followed by PNG bytes and is published
//! atomically. Cache failures are diagnostic-only; normal SVG rendering continues.

use anyhow::{Context, Result};
use resvg::usvg::fontdb::{Family, Source};
use sha2::{Digest, Sha256};
use std::collections::BTreeMap;
use std::io::{Read, Write};
use std::path::{Path, PathBuf};

/// Keep optional cache I/O outside the renderer's success/failure contract.
pub(super) struct Cache {
    path: PathBuf,
}

impl Cache {
    /// Key self-contained SVGs by actual bytes, rendering options and implementation.
    pub(super) fn for_svg(input: &str, dpi: f64) -> Option<Self> {
        if std::env::var("PAPPER_SVG_CACHE").is_ok_and(|value| value == "0") {
            return None;
        }
        let directory =
            std::env::var_os("PAPPER_SVG_CACHE_DIR").filter(|value| !value.is_empty())?;
        let document = roxmltree::Document::parse(input).ok()?;
        let mut text = false;
        // CSS paint servers can reference files without using an href attribute.
        // Keep fragment and data URLs cacheable, but bypass unknown URL schemes.
        let mut cursor = 0;
        while let Some(offset) = input[cursor..].find("url(") {
            let start = cursor + offset + 4;
            let end = input[start..].find(')')? + start;
            let value = input[start..end].trim().trim_matches(['\'', '"']);
            if !value.is_empty() && !value.starts_with('#') && !value.starts_with("data:") {
                return None;
            }
            cursor = end + 1;
        }
        for node in document.descendants().filter(|node| node.is_element()) {
            text |= node.tag_name().name() == "text";
            for attribute in node
                .attributes()
                .filter(|attribute| attribute.name() == "href")
            {
                let href = attribute.value().trim();
                // External and nested SVG images can depend on bytes/fonts outside
                // this stream. Bypass rather than cache an incomplete dependency key.
                if !href.is_empty()
                    && !href.starts_with('#')
                    && ![
                        "data:image/png;",
                        "data:image/jpeg;",
                        "data:image/gif;",
                        "data:image/webp;",
                    ]
                    .iter()
                    .any(|prefix| href.starts_with(prefix))
                {
                    return None;
                }
            }
        }
        let mut digest = Sha256::new();
        digest.update(b"papper-svg-fallback-v1\0");
        digest.update(input.as_bytes());
        digest.update(dpi.to_bits().to_le_bytes());
        let identity = match std::env::var("PAPPER_SVG_RENDERER_ID") {
            Ok(value) if !value.is_empty() => value,
            _ => match executable_identity() {
                Ok(value) => value,
                Err(error) => {
                    eprintln!("[papper-svg] Cache identity unavailable: {error:#}");
                    return None;
                }
            },
        };
        digest.update(identity.as_bytes());
        if text {
            // Papper collects this once per build. Standalone callers without a
            // verified font identity still render text normally, without caching it.
            let fonts = std::env::var("PAPPER_SVG_FONT_ID")
                .ok()
                .filter(|value| !value.is_empty())?;
            digest.update([0]);
            digest.update(fonts.as_bytes());
        }
        let key = hex(digest.finalize().as_slice());
        Some(Self {
            path: PathBuf::from(directory)
                .join(&key[..2])
                .join(format!("{key}.cache")),
        })
    }

    /// Reject truncated or edited entries without decoding large PNG pixel buffers.
    pub(super) fn read(&self) -> Option<Vec<u8>> {
        let bytes = match std::fs::read(&self.path) {
            Ok(bytes) => bytes,
            Err(error) if error.kind() == std::io::ErrorKind::NotFound => return None,
            Err(error) => {
                eprintln!("[papper-svg] Cannot read PNG cache: {error}");
                return None;
            }
        };
        if bytes.len() < 40
            || &bytes[32..40] != b"\x89PNG\r\n\x1a\n"
            || Sha256::digest(&bytes[32..]).as_slice() != &bytes[..32]
        {
            eprintln!(
                "[papper-svg] Ignoring damaged PNG cache: {}",
                self.path.display()
            );
            return None;
        }
        Some(bytes[32..].to_vec())
    }

    /// Publish complete entries even when concurrent builds render the same image.
    pub(super) fn publish(&self, png: &[u8]) {
        if let Err(error) = self.write(png) {
            eprintln!("[papper-svg] Cannot publish PNG cache, using rendered image: {error:#}");
        }
    }

    /// Stage beside the destination so interrupted writers cannot expose partial PNGs.
    fn write(&self, png: &[u8]) -> Result<()> {
        let parent = self
            .path
            .parent()
            .context("PNG cache directory is missing")?;
        std::fs::create_dir_all(parent)?;
        let mut temporary = tempfile::NamedTempFile::new_in(parent)?;
        temporary.write_all(&Sha256::digest(png))?;
        temporary.write_all(png)?;
        temporary.flush()?;
        temporary.persist(&self.path)?;
        Ok(())
    }
}

/// Fingerprint direct helper invocations without trusting only a package version.
fn executable_identity() -> Result<String> {
    let mut file = std::fs::File::open(std::env::current_exe()?)?;
    let mut digest = Sha256::new();
    let mut buffer = [0; 64 * 1024];
    loop {
        let count = file.read(&mut buffer)?;
        if count == 0 {
            break;
        }
        digest.update(&buffer[..count]);
    }
    Ok(hex(digest.finalize().as_slice()))
}

/// Hash the renderer's actual font database once per build, including file contents.
pub(super) fn font_identity() -> Result<String> {
    let fonts = super::font_database();
    let mut digest = Sha256::new();
    let mut files = BTreeMap::<&Path, Vec<u8>>::new();
    for face in fonts.faces() {
        let path = match &face.source {
            Source::File(path) => Some(path.as_path()),
            Source::SharedFile(path, _) => Some(path.as_path()),
            Source::Binary(_) => None,
        };
        let bytes = if let Some(path) = path {
            if !files.contains_key(path) {
                let hash = fonts
                    .with_face_data(face.id, |bytes, _| Sha256::digest(bytes).to_vec())
                    .with_context(|| format!("Cannot fingerprint font: {}", path.display()))?;
                files.insert(path, hash);
            }
            files[path].clone()
        } else {
            fonts
                .with_face_data(face.id, |bytes, _| Sha256::digest(bytes).to_vec())
                .context("Cannot fingerprint font data")?
        };
        // Preserve font selection order as well as bytes: equal-family faces can
        // resolve differently after the system font database changes its ordering.
        digest.update(bytes);
        digest.update(face.index.to_le_bytes());
    }
    for family in [
        Family::Serif,
        Family::SansSerif,
        Family::Cursive,
        Family::Fantasy,
        Family::Monospace,
    ] {
        digest.update([0]);
        digest.update(fonts.family_name(&family).as_bytes());
    }
    Ok(hex(digest.finalize().as_slice()))
}

/// Format a digest without depending on a formatter implementation for arrays.
fn hex(bytes: &[u8]) -> String {
    bytes.iter().map(|byte| format!("{byte:02x}")).collect()
}
