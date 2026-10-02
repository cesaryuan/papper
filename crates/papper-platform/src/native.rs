//! Reuse the existing Rust equation converters through their versioned native ABI.

use anyhow::{Context, Result, bail};
use libloading::Library;
use papper_core::resources::ResourcePaths;
use serde_json::{Value, json};
use sha1::{Digest, Sha1};
use std::collections::BTreeMap;
use std::ffi::{CStr, CString, c_char};
use std::io::Read;
use std::path::{Path, PathBuf};

/// Load a native component and its staged adjacent dependencies without trusting cwd.
///
/// # Safety
/// The caller must select a trusted compiled component; library initialization
/// can execute native code before its symbols are resolved.
pub unsafe fn load_library(path: &Path) -> Result<Library> {
    let path = path.canonicalize()?;
    #[cfg(windows)]
    {
        use libloading::os::windows::{
            LOAD_LIBRARY_SEARCH_DLL_LOAD_DIR, LOAD_LIBRARY_SEARCH_SYSTEM32,
            Library as WindowsLibrary,
        };
        // The component directory contains its redistributed dependencies. Limit
        // resolution to that directory and Windows system DLLs, not manuscript cwd.
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

type Convert = unsafe extern "C" fn(*const c_char) -> *mut c_char;
type Free = unsafe extern "C" fn(*mut c_char);

/// Own a library for the lifetime of its converter/free function pointers.
pub struct NativeConverter {
    _library: Library,
    convert: Convert,
    free: Free,
}

impl NativeConverter {
    /// Load a compiled Rust component with explicit symbols and allocator ownership.
    pub fn load(resources: &ResourcePaths, project: &str) -> Result<Self> {
        let stem = project.replace('-', "_");
        let filename = if cfg!(windows) {
            format!("{stem}.dll")
        } else if cfg!(target_os = "macos") {
            format!("lib{stem}.dylib")
        } else {
            format!("lib{stem}.so")
        };
        let candidates = [
            resources.root.join("mathtype/bin").join(&filename),
            resources
                .root
                .join("src/pandoc_manuscript/mathtype/bin")
                .join(&filename),
            resources
                .root
                .join("scripts")
                .join(project)
                .join("target/release")
                .join(&filename),
        ];
        let path = candidates.into_iter().find(|path| path.is_file()).ok_or_else(|| anyhow::anyhow!("Native {project} library is missing; install a platform wheel or compile its Rust source"))?;
        // Only project-owned compiled native libraries are loaded. Both symbols
        // are resolved before publishing the object, and the library outlives them.
        let library = unsafe { load_library(&path) }
            .with_context(|| format!("Cannot load native converter: {}", path.display()))?;
        let convert =
            unsafe { *library.get::<Convert>(format!("{stem}_convert_v1\0").as_bytes())? };
        let free = unsafe { *library.get::<Free>(format!("{stem}_free_v1\0").as_bytes())? };
        Ok(Self {
            _library: library,
            convert,
            free,
        })
    }

    /// Copy one response and release it exactly once with the matching library allocator.
    pub fn call(&self, request: &Value) -> Result<Value> {
        let request = CString::new(serde_json::to_vec(request)?)?;
        // JSON escapes interior NUL characters; the ABI receives a valid UTF-8 C string.
        let pointer = unsafe { (self.convert)(request.as_ptr()) };
        anyhow::ensure!(
            !pointer.is_null(),
            "Native converter returned a null response"
        );
        let bytes = unsafe { CStr::from_ptr(pointer) }.to_bytes().to_vec();
        unsafe { (self.free)(pointer) };
        let response: Value = serde_json::from_slice(&bytes)?;
        if let Some(error) = response.get("error") {
            bail!("{}", error.as_str().unwrap_or("Native conversion failed"));
        }
        response
            .get("result")
            .cloned()
            .context("Native converter omitted its result")
    }

    /// Recover TeX from MathType OLE, retaining the native structural fallback.
    pub fn ole_to_latex(&self, ole: &[u8]) -> Result<String> {
        let result = self.call(&json!({"operation":"decode_ole","ole":hex(ole),"mode":"auto"}))?;
        result["latex"]
            .as_str()
            .map(str::to_string)
            .context("Native decoder omitted LaTeX")
    }
}

/// Encode artifact bytes without exposing allocator-backed native memory to callers.
pub fn hex(bytes: &[u8]) -> String {
    const DIGITS: &[u8] = b"0123456789abcdef";
    let mut result = String::with_capacity(bytes.len() * 2);
    for &byte in bytes {
        result.push(DIGITS[(byte >> 4) as usize] as char);
        result.push(DIGITS[(byte & 15) as usize] as char);
    }
    result
}

/// Decode validated hex artifacts returned by a native converter.
pub fn unhex(raw: &str) -> Result<Vec<u8>> {
    anyhow::ensure!(
        raw.len().is_multiple_of(2),
        "Native binary artifact has an odd hex length"
    );
    raw.as_bytes()
        .as_chunks::<2>()
        .0
        .iter()
        .map(|bytes| {
            let text = std::str::from_utf8(bytes)?;
            Ok(u8::from_str_radix(text, 16)?)
        })
        .collect()
}

/// Resolve OPC relationship paths within an archive without directory traversal.
fn relationship_path(base: &str, target: &str) -> Option<String> {
    let joined = if target.starts_with('/') {
        target.trim_start_matches('/').to_string()
    } else {
        format!("{base}/{target}")
    };
    let mut segments = Vec::new();
    for segment in joined.split('/') {
        match segment {
            "" | "." => (),
            ".." => {
                segments.pop()?;
            }
            segment => segments.push(segment),
        }
    }
    Some(segments.join("/"))
}

/// Read OLE objects referenced as equations, preserving unrelated embedded attachments.
pub fn mathtype_objects(docx: &Path) -> Result<Vec<Vec<u8>>> {
    let mut archive = zip::ZipArchive::new(std::fs::File::open(docx)?)?;
    let mut parts: Vec<_> = archive
        .file_names()
        .filter(|name| {
            name.starts_with("word/") && name.ends_with(".xml") && !name.contains("/_rels/")
        })
        .map(str::to_string)
        .collect();
    parts.sort();
    let mut result = Vec::new();
    for part in parts {
        let mut xml = String::new();
        archive.by_name(&part)?.read_to_string(&mut xml)?;
        let document = roxmltree::Document::parse(&xml)?;
        let ids: Vec<_> = document
            .descendants()
            .filter(|node| {
                node.has_tag_name(("urn:schemas-microsoft-com:office:office", "OLEObject"))
            })
            .filter(|node| {
                node.attribute("ProgID").is_some_and(|program| {
                    program.starts_with("Equation.") || program.contains("MathType")
                })
            })
            .filter_map(|node| {
                node.attribute((
                    "http://schemas.openxmlformats.org/officeDocument/2006/relationships",
                    "id",
                ))
            })
            .map(str::to_string)
            .collect();
        if ids.is_empty() {
            continue;
        }
        let (folder, filename) = part.rsplit_once('/').context("Invalid DOCX XML part")?;
        let mut rels_xml = String::new();
        let Ok(mut rels) = archive.by_name(&format!("{folder}/_rels/{filename}.rels")) else {
            continue;
        };
        rels.read_to_string(&mut rels_xml)?;
        drop(rels);
        let rels = roxmltree::Document::parse(&rels_xml)?;
        for id in ids {
            let target = rels
                .descendants()
                .find(|node| {
                    node.attribute("Id") == Some(id.as_str())
                        && node
                            .attribute("Type")
                            .is_some_and(|kind| kind.ends_with("/oleObject"))
                        && node.attribute("TargetMode") != Some("External")
                })
                .and_then(|node| node.attribute("Target"));
            if let Some(path) = target.and_then(|target| relationship_path(folder, target))
                && let Ok(mut object) = archive.by_name(&path)
            {
                let mut bytes = Vec::new();
                object.read_to_end(&mut bytes)?;
                result.push(bytes);
            }
        }
    }
    Ok(result)
}

/// Decode unique equation objects once and retain unsupported equations as preview images.
pub fn decode_documents(
    documents: &[PathBuf],
    resources: &ResourcePaths,
) -> Result<BTreeMap<String, Value>> {
    let mut result = BTreeMap::new();
    let mut converter = None;
    for document in documents {
        for ole in mathtype_objects(document)? {
            let key = hex(&Sha1::digest(&ole));
            if result.contains_key(&key) {
                continue;
            }
            if converter.is_none() {
                converter = Some(NativeConverter::load(resources, "mathtype-rust")?);
            }
            let latex = converter.as_ref().unwrap().ole_to_latex(&ole);
            let value = match latex {
                Ok(latex) => json!(latex),
                Err(error) => {
                    eprintln!(
                        "[WARN] Could not decode equation {key} in {}: {error}",
                        document.display()
                    );
                    json!(false)
                }
            };
            result.insert(key, value);
        }
    }
    Ok(result)
}
