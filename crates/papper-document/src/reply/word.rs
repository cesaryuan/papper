//! Export an owned read-only Word document while retaining the user's application.

use anyhow::{Context, Result};
use papper_core::paths::display_path;
use std::path::Path;
use windows::Win32::System::Com::{
    CLSCTX_LOCAL_SERVER, CLSIDFromProgID, COINIT_APARTMENTTHREADED, CoCreateInstance,
    CoInitializeEx, CoUninitialize, DISPATCH_FLAGS, DISPATCH_METHOD, DISPATCH_PROPERTYGET,
    DISPPARAMS, IDispatch,
};
use windows::Win32::System::Ole::GetActiveObject;
use windows::Win32::System::Variant::VT_DISPATCH;
use windows::core::{GUID, HSTRING, IUnknown, Interface, PCWSTR, VARIANT, w};

/// Balance this thread's COM initialization after all interfaces have been released.
struct Apartment;
impl Drop for Apartment {
    /// Release only this thread's COM apartment, never the Office application.
    fn drop(&mut self) {
        unsafe {
            CoUninitialize();
        }
    }
}

/// Invoke an automation member with COM's reverse positional argument ordering.
fn invoke(
    object: &IDispatch,
    name: &str,
    flags: DISPATCH_FLAGS,
    mut arguments: Vec<VARIANT>,
) -> Result<VARIANT> {
    let name = HSTRING::from(name);
    let names = [PCWSTR(name.as_ptr())];
    let mut identifier = 0;
    let mut result = VARIANT::default();
    arguments.reverse();
    let parameters = DISPPARAMS {
        rgvarg: arguments.as_mut_ptr(),
        cArgs: arguments.len() as u32,
        ..DISPPARAMS::default()
    };
    // VARIANTs, member names and result storage remain alive throughout the COM call.
    unsafe {
        object.GetIDsOfNames(&GUID::zeroed(), names.as_ptr(), 1, 0, &mut identifier)?;
        object.Invoke(
            identifier,
            &GUID::zeroed(),
            0,
            flags,
            &parameters,
            Some(&mut result),
            None,
            None,
        )?;
    }
    Ok(result)
}

/// Extract an automation object from a returned VARIANT without leaking its reference.
fn dispatch(value: VARIANT) -> Result<IDispatch> {
    // Automation returns VT_DISPATCH, which windows-core's IUnknown conversion
    // deliberately rejects. Clone the borrowed dispatch before VARIANT releases it.
    unsafe {
        let raw = &value.as_raw().Anonymous.Anonymous;
        if raw.vt == VT_DISPATCH.0 {
            return IDispatch::from_raw_borrowed(&raw.Anonymous.pdispVal)
                .cloned()
                .context("Word returned an empty automation object");
        }
    }
    Ok(IUnknown::try_from(&value)?.cast()?)
}

/// Close only the document opened by this operation on success or failure.
struct OwnedDocument(IDispatch);
impl Drop for OwnedDocument {
    /// Discard changes to the read-only intermediate, preserving all user documents.
    fn drop(&mut self) {
        if let Err(error) = invoke(&self.0, "Close", DISPATCH_METHOD, vec![false.into()]) {
            eprintln!("[WARN] Could not close reply line-source document: {error:#}");
        }
    }
}

/// Prefer the running Word instance and export one read-only input to PDF.
pub fn export(source: &Path, output: &Path) -> Result<()> {
    // Word can return an already-open document for the same path. Open a unique
    // copy so closing our intermediate can never close the user's source document.
    let owned_source = output.with_extension(format!(
        "source.{}",
        source
            .extension()
            .and_then(|value| value.to_str())
            .unwrap_or("docx")
    ));
    std::fs::copy(source, &owned_source)?;
    // Interface lifetimes stay on this initialized STA thread; no global Office state is changed.
    unsafe {
        CoInitializeEx(None, COINIT_APARTMENTTHREADED).ok()?;
    }
    let _apartment = Apartment;
    let class = unsafe { CLSIDFromProgID(w!("Word.Application")) }.context("Microsoft Word is required on Windows for non-PDF reply line sources; install Word or provide --manuscript-line-source PDF")?;
    let mut active = None;
    let application: IDispatch = if unsafe { GetActiveObject(&class, None, &mut active) }.is_ok() {
        active
            .context("Running Word instance did not expose its application")?
            .cast()?
    } else {
        unsafe { CoCreateInstance(&class, None, CLSCTX_LOCAL_SERVER) }.context(
            "Could not attach to or start Microsoft Word; provide a PDF line source instead",
        )?
    };
    let documents = dispatch(invoke(
        &application,
        "Documents",
        DISPATCH_PROPERTYGET,
        Vec::new(),
    )?)?;
    let source = display_path(&owned_source.canonicalize()?);
    let document = OwnedDocument(dispatch(invoke(
        &documents,
        "Open",
        DISPATCH_METHOD,
        vec![
            source.as_str().into(),
            false.into(),
            true.into(),
            false.into(),
        ],
    )?)?);
    invoke(
        &document.0,
        "ExportAsFixedFormat",
        DISPATCH_METHOD,
        vec![display_path(output).as_str().into(), 17i32.into()],
    )?;
    anyhow::ensure!(
        output.is_file(),
        "Word conversion did not create PDF: {}",
        output.display()
    );
    Ok(())
}
