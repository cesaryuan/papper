//! Extract PDF text and geometry with MuPDF 1.27.2 in a dedicated native process.
//!
//! MuPDF errors use C longjmp; the hidden __pdf_extract process isolates malformed
//! documents instead of allowing a C exception to cross Rust stack frames.

use anyhow::{Context, Result};
use libloading::Library;
use papper_core::paths::display_path;
use papper_core::resources::ResourcePaths;
use serde_json::{Value, json};
use std::ffi::{CString, c_char, c_int, c_void};
use std::path::{Path, PathBuf};

type Pointer = *mut c_void;
type NewContext = unsafe extern "C" fn(Pointer, Pointer, usize, *const c_char) -> Pointer;
type DropContext = unsafe extern "C" fn(Pointer);
type Register = unsafe extern "C" fn(Pointer);
type OpenDocument = unsafe extern "C" fn(Pointer, *const c_char) -> Pointer;
type DropObject = unsafe extern "C" fn(Pointer, Pointer);
type CountPages = unsafe extern "C" fn(Pointer, Pointer) -> c_int;
type Metadata = unsafe extern "C" fn(Pointer, Pointer, *const c_char, *mut c_char, c_int) -> c_int;
type NewText = unsafe extern "C" fn(Pointer, Pointer, c_int, Pointer) -> Pointer;
type NewBuffer = unsafe extern "C" fn(Pointer, usize) -> Pointer;
type NewOutput = unsafe extern "C" fn(Pointer, Pointer) -> Pointer;
type PrintJson = unsafe extern "C" fn(Pointer, Pointer, Pointer, f32);
type PrintText = unsafe extern "C" fn(Pointer, Pointer, Pointer);
type BufferStorage = unsafe extern "C" fn(Pointer, Pointer, *mut *mut u8) -> usize;

/// Match MuPDF 1.27.2's public structured-text options ABI.
#[repr(C)]
struct TextOptions {
    flags: c_int,
    scale: f32,
    clip: [f32; 4],
}

/// Locate bundled native libraries, with an explicit source-checkout development path.
fn library_path() -> Result<PathBuf> {
    if let Some(path) = std::env::var_os("PAPPER_MUPDF_LIBRARY") {
        return Ok(path.into());
    }
    let resources = ResourcePaths::discover()?;
    let names = if cfg!(windows) {
        vec!["mupdfcpp64.dll", "mupdf.dll"]
    } else if cfg!(target_os = "macos") {
        vec!["libmupdf.dylib"]
    } else {
        vec!["libmupdf.so"]
    };
    for directory in [
        resources.root.join("bin"),
        resources.root.join("native"),
        resources.root.clone(),
    ] {
        for name in &names {
            let path = directory.join(name);
            if path.is_file() {
                return Ok(path);
            }
        }
    }
    // A source checkout may reuse its development DLL; installed wheels never search Python.
    if resources.root.join("Cargo.toml").is_file()
        && resources
            .root
            .join("crates/papper-cli/Cargo.toml")
            .is_file()
    {
        let development = resources
            .root
            .join(".venv/Lib/site-packages/pymupdf/mupdfcpp64.dll");
        if development.is_file() {
            return Ok(development);
        }
    }
    anyhow::bail!(
        "MuPDF native library is missing; install the complete Papper native resources or set PAPPER_MUPDF_LIBRARY"
    )
}

/// Copy one serialized structured-text buffer before releasing its MuPDF ownership.
unsafe fn serialize_page(
    library: &Library,
    context: Pointer,
    page: Pointer,
    json_output: bool,
) -> Result<Vec<u8>> {
    // All pointers originate from this DLL and stay alive until the copied bytes are owned.
    unsafe {
        let new_buffer = library.get::<NewBuffer>(b"fz_new_buffer\0")?;
        let new_output = library.get::<NewOutput>(b"fz_new_output_with_buffer\0")?;
        let storage = library.get::<BufferStorage>(b"fz_buffer_storage\0")?;
        let close = library.get::<DropObject>(b"fz_close_output\0")?;
        let drop_output = library.get::<DropObject>(b"fz_drop_output\0")?;
        let drop_buffer = library.get::<DropObject>(b"fz_drop_buffer\0")?;
        let buffer = new_buffer(context, 1024);
        let output = new_output(context, buffer);
        if json_output {
            library.get::<PrintJson>(b"fz_print_stext_page_as_json\0")?(context, output, page, 1.0);
        } else {
            library.get::<PrintText>(b"fz_print_stext_page_as_text\0")?(context, output, page);
        }
        close(context, output);
        let mut bytes = std::ptr::null_mut();
        let length = storage(context, buffer, &mut bytes);
        let result = if length == 0 {
            Vec::new()
        } else {
            std::slice::from_raw_parts(bytes, length).to_vec()
        };
        drop_output(context, output);
        drop_buffer(context, buffer);
        Ok(result)
    }
}

/// Read real PDF text, bounding boxes and producer metadata without an interpreter.
pub fn extract(path: &Path) -> Result<Value> {
    let library_path = library_path()?;
    // The explicitly selected native library must export the pinned MuPDF C ABI.
    unsafe {
        let library = papper_platform::native::load_library(&library_path)
            .with_context(|| format!("Could not load MuPDF {}", library_path.display()))?;
        let context = library.get::<NewContext>(b"fz_new_context_imp\0")?(
            std::ptr::null_mut(),
            std::ptr::null_mut(),
            256 << 20,
            c"1.27.2".as_ptr(),
        );
        anyhow::ensure!(
            !context.is_null(),
            "MuPDF 1.27.2 context initialization failed"
        );
        library.get::<Register>(b"fz_register_document_handlers\0")?(context);
        let filename = CString::new(display_path(path))?;
        let document =
            library.get::<OpenDocument>(b"fz_open_document\0")?(context, filename.as_ptr());
        anyhow::ensure!(!document.is_null(), "Could not open PDF {}", path.display());
        let lookup = library.get::<Metadata>(b"fz_lookup_metadata\0")?;
        let mut metadata = serde_json::Map::new();
        for key in [
            "Producer", "Creator", "Title", "Author", "Subject", "Keywords",
        ] {
            let name = CString::new(format!("info:{key}"))?;
            let mut value = vec![0u8; 8192];
            let length = lookup(
                context,
                document,
                name.as_ptr(),
                value.as_mut_ptr().cast(),
                value.len() as c_int,
            );
            if length > 0 {
                let length = value
                    .iter()
                    .position(|byte| *byte == 0)
                    .unwrap_or(value.len());
                metadata.insert(
                    key.into(),
                    String::from_utf8_lossy(&value[..length])
                        .into_owned()
                        .into(),
                );
            }
        }
        let count = library.get::<CountPages>(b"fz_count_pages\0")?(context, document);
        let text_page = library.get::<NewText>(b"fz_new_stext_page_from_page_number\0")?;
        let drop_text = library.get::<DropObject>(b"fz_drop_stext_page\0")?;
        let mut pages = Vec::new();
        // Match existing PDF text extraction: preserve ligatures/whitespace,
        // clip to the media box and retain unknown glyph CIDs. A null options
        // pointer expands ligatures and can change users' line regex matches.
        let mut options = TextOptions {
            flags: 1 | 2 | 64 | 128,
            scale: 1.0,
            clip: [0.0; 4],
        };
        for number in 0..count {
            let page = text_page(
                context,
                document,
                number,
                (&mut options as *mut TextOptions).cast(),
            );
            let layout = serialize_page(&library, context, page, true)?;
            let text = serialize_page(&library, context, page, false)?;
            drop_text(context, page);
            pages.push(json!({"layout":serde_json::from_slice::<Value>(&layout).context("Invalid MuPDF structured-text JSON")?,"text":String::from_utf8_lossy(&text)}));
        }
        library.get::<DropObject>(b"fz_drop_document\0")?(context, document);
        library.get::<DropContext>(b"fz_drop_context\0")?(context);
        Ok(json!({"metadata":metadata,"pages":pages}))
    }
}
