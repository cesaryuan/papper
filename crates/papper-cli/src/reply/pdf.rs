//! Extract PDF text and geometry through the linked MuPDF 1.27.2 engine.
//!
//! The isolated `__pdf_extract` command uses mupdf-sys's C exception wrappers,
//! keeping longjmp inside C and releasing every owned object on Rust errors.
//! Preserve the original extraction flags and JSON shape for reply line matching.

use anyhow::{Context, Result, anyhow};
use mupdf_sys as sys;
use papper_core::paths::display_path;
use serde_json::{Value, json};
use std::ffi::{CStr, CString};
use std::path::Path;
use std::ptr::NonNull;

/// Own the single engine context used by this isolated extraction process.
struct PdfContext(NonNull<sys::fz_context>);

impl PdfContext {
    /// Initialize handlers through the crate's exception-aware C wrapper.
    fn new() -> Result<Self> {
        // Each hidden command extracts once; the base wrapper's locks are global.
        let raw = unsafe { sys::mupdf_new_base_context() };
        Ok(Self(
            NonNull::new(raw).context("Could not initialize MuPDF")?,
        ))
    }

    /// Turn a caught native exception into an owned Rust error and free it.
    fn call<T>(&self, operation: impl FnOnce(*mut *mut sys::mupdf_error_t) -> T) -> Result<T> {
        let mut error = std::ptr::null_mut();
        let result = operation(&mut error);
        if let Some(error) = NonNull::new(error) {
            // The wrapper owns both the message and error until drop_error.
            let message = unsafe { CStr::from_ptr(error.as_ref().message) }
                .to_string_lossy()
                .into_owned();
            unsafe { sys::mupdf_drop_error(error.as_ptr()) };
            return Err(anyhow!("MuPDF: {message}"));
        }
        Ok(result)
    }
}

impl Drop for PdfContext {
    /// Release the context after all document and page borrows have ended.
    fn drop(&mut self) {
        // mupdf-sys 0.8.0's base destructor deletes its global locks first,
        // crashing when MuPDF subsequently locks during context teardown.
        // Keep those process-owned locks alive until this isolated helper exits.
        unsafe { sys::fz_drop_context(self.0.as_ptr()) };
    }
}

/// Keep the document alive while reading its metadata and structured pages.
struct PdfDocument<'a> {
    context: &'a PdfContext,
    raw: NonNull<sys::fz_document>,
}

impl<'a> PdfDocument<'a> {
    /// Open a PDF without depending on Python or a runtime-loaded DLL.
    fn open(context: &'a PdfContext, path: &Path) -> Result<Self> {
        let filename = CString::new(display_path(path))?;
        let raw = context.call(|error| unsafe {
            sys::mupdf_open_document(context.0.as_ptr(), filename.as_ptr(), error)
        })?;
        Ok(Self {
            context,
            raw: NonNull::new(raw)
                .with_context(|| format!("Could not open PDF {}", path.display()))?,
        })
    }

    /// Copy one optional metadata value before freeing its C-owned string.
    fn metadata(&self, key: &str) -> Result<Option<String>> {
        let key = CString::new(format!("info:{key}"))?;
        let value = self.context.call(|error| unsafe {
            sys::mupdf_lookup_metadata(
                self.context.0.as_ptr(),
                self.raw.as_ptr(),
                key.as_ptr(),
                error,
            )
        })?;
        let Some(value) = NonNull::new(value) else {
            return Ok(None);
        };
        let text = unsafe { CStr::from_ptr(value.as_ptr()) }
            .to_string_lossy()
            .into_owned();
        unsafe { sys::mupdf_drop_str(value.as_ptr()) };
        Ok(Some(text))
    }

    /// Count pages through a wrapper that catches corrupt-document exceptions.
    fn page_count(&self) -> Result<i32> {
        self.context.call(|error| unsafe {
            sys::mupdf_document_page_count(self.context.0.as_ptr(), self.raw.as_ptr(), error)
        })
    }

    /// Extract a page with the legacy ligature, whitespace, clip and CID policy.
    fn text_page(&self, number: i32) -> Result<TextPage<'a>> {
        let context = self.context;
        let page = context.call(|error| unsafe {
            sys::mupdf_load_page(context.0.as_ptr(), self.raw.as_ptr(), number, error)
        })?;
        let page = NonNull::new(page).context("MuPDF returned an empty PDF page")?;
        let options = sys::fz_stext_options {
            flags: sys::FZ_STEXT_PRESERVE_LIGATURES
                | sys::FZ_STEXT_PRESERVE_WHITESPACE
                | sys::FZ_STEXT_CLIP
                | sys::FZ_STEXT_USE_CID_FOR_UNKNOWN_UNICODE,
            scale: 1.0,
            clip: sys::fz_rect {
                x0: 0.0,
                y0: 0.0,
                x1: 0.0,
                y1: 0.0,
            },
        };
        let text = context.call(|error| unsafe {
            sys::mupdf_new_stext_page_from_page(context.0.as_ptr(), page.as_ptr(), &options, error)
        });
        // Page loading succeeds before text extraction; release it even on error.
        unsafe { sys::fz_drop_page(context.0.as_ptr(), page.as_ptr()) };
        Ok(TextPage {
            context,
            raw: NonNull::new(text?).context("MuPDF returned no structured PDF text")?,
        })
    }
}

impl Drop for PdfDocument<'_> {
    /// Release the document while its borrowed context remains alive.
    fn drop(&mut self) {
        unsafe { sys::fz_drop_document(self.context.0.as_ptr(), self.raw.as_ptr()) };
    }
}

/// Own extracted text independently of the temporary source page.
struct TextPage<'a> {
    context: &'a PdfContext,
    raw: NonNull<sys::fz_stext_page>,
}

impl TextPage<'_> {
    /// Serialize the existing text or geometry contract using C exception wrappers.
    fn serialize(&self, json_output: bool) -> Result<Vec<u8>> {
        let output = TextOutput::new(self.context)?;
        let stream = output.stream.expect("TextOutput initializes its stream");
        self.context.call(|error| unsafe {
            if json_output {
                sys::mupdf_print_stext_page_as_json(
                    self.context.0.as_ptr(),
                    stream.as_ptr(),
                    self.raw.as_ptr(),
                    1.0,
                    error,
                );
            } else {
                sys::mupdf_print_stext_page_as_text(
                    self.context.0.as_ptr(),
                    stream.as_ptr(),
                    self.raw.as_ptr(),
                    error,
                );
            }
        })?;
        Ok(output.bytes())
    }
}

impl Drop for TextPage<'_> {
    /// Release the structured text before its context.
    fn drop(&mut self) {
        unsafe { sys::fz_drop_stext_page(self.context.0.as_ptr(), self.raw.as_ptr()) };
    }
}

/// Own both serialization resources, including partially initialized outputs.
struct TextOutput<'a> {
    context: &'a PdfContext,
    buffer: NonNull<sys::fz_buffer>,
    stream: Option<NonNull<sys::fz_output>>,
}

impl<'a> TextOutput<'a> {
    /// Allocate an empty buffer and its output through exception-safe wrappers.
    fn new(context: &'a PdfContext) -> Result<Self> {
        let raw = context.call(|error| unsafe {
            sys::mupdf_buffer_from_str(context.0.as_ptr(), c"".as_ptr(), error)
        })?;
        let mut output = Self {
            context,
            buffer: NonNull::new(raw).context("Could not allocate PDF text buffer")?,
            stream: None,
        };
        let raw = context.call(|error| unsafe {
            sys::mupdf_new_output_with_buffer(context.0.as_ptr(), output.buffer.as_ptr(), error)
        })?;
        output.stream = Some(NonNull::new(raw).context("Could not allocate PDF text output")?);
        Ok(output)
    }

    /// Copy already-written memory-buffer bytes without native allocation.
    fn bytes(&self) -> Vec<u8> {
        let mut data = std::ptr::null_mut();
        let length = unsafe {
            sys::fz_buffer_storage(self.context.0.as_ptr(), self.buffer.as_ptr(), &mut data)
        };
        if length == 0 {
            return Vec::new();
        }
        unsafe { std::slice::from_raw_parts(data, length) }.to_vec()
    }
}

impl Drop for TextOutput<'_> {
    /// Close the memory-only stream and free it before its backing buffer.
    fn drop(&mut self) {
        unsafe {
            if let Some(stream) = self.stream {
                // Unbuffered memory outputs have no close callback or pending flush.
                sys::fz_close_output(self.context.0.as_ptr(), stream.as_ptr());
                sys::fz_drop_output(self.context.0.as_ptr(), stream.as_ptr());
            }
            sys::fz_drop_buffer(self.context.0.as_ptr(), self.buffer.as_ptr());
        }
    }
}

/// Read real PDF text, bounding boxes and producer metadata in the isolated helper.
pub fn extract(path: &Path) -> Result<Value> {
    let context = PdfContext::new()?;
    let document = PdfDocument::open(&context, path)?;
    let mut metadata = serde_json::Map::new();
    for key in [
        "Producer", "Creator", "Title", "Author", "Subject", "Keywords",
    ] {
        if let Some(value) = document.metadata(key)? {
            metadata.insert(key.into(), value.into());
        }
    }
    let mut pages = Vec::new();
    for number in 0..document.page_count()? {
        let page = document.text_page(number)?;
        let layout = page.serialize(true)?;
        let text = page.serialize(false)?;
        pages.push(json!({
            "layout": serde_json::from_slice::<Value>(&layout).context("Invalid MuPDF structured-text JSON")?,
            "text": String::from_utf8_lossy(&text),
        }));
    }
    Ok(json!({"metadata": metadata, "pages": pages}))
}
