//! Share DOCX defaults and final output filters between manuscript and reply builds.

use anyhow::Result;
use papper_core::metadata::EffectiveMetadata;
use papper_core::resources::ResourcePaths;
use std::ffi::OsString;
use std::path::{Path, PathBuf};

/// Append metadata and image filters after the shared defaults in writer order.
pub(crate) fn append_output_filters(command: &mut Vec<OsString>, resources: &ResourcePaths) {
    for path in output_filters(resources) {
        command.extend(["--lua-filter".into(), path.into_os_string()]);
    }
}

/// Share final DOCX filter paths between conversion and runtime diagnostics.
pub(crate) fn output_filters(resources: &ResourcePaths) -> Vec<PathBuf> {
    ["docx_metadata", "svg_embed_images", "svg_to_png"]
        .into_iter()
        .map(|kind| resources.resource(format!("pandoc/filters/docx/{kind}.lua")))
        .collect()
}

/// Prepare the same effective reference for conversion and export-only commands.
pub(crate) fn prepare_reference(
    resources: &ResourcePaths,
    effective: &EffectiveMetadata,
    override_path: Option<&Path>,
    work: &Path,
) -> Result<PathBuf> {
    let reference =
        papper_document::docx::prepare_reference(resources, effective, override_path, work)?
            .unwrap_or_else(|| resources.resource("pandoc/manuscript-template/reference-doc.docx"));
    // Export-only mode does not run Pandoc, so validate even an unchanged
    // reference here rather than publishing a corrupt/non-DOCX ZIP archive.
    let mut archive = zip::ZipArchive::new(std::fs::File::open(&reference)?)?;
    archive.by_name("word/document.xml")?;
    archive.by_name("word/styles.xml")?;
    Ok(reference)
}

/// Append the effective reference shared with export-only commands.
pub(crate) fn append_reference(
    command: &mut Vec<OsString>,
    resources: &ResourcePaths,
    effective: &EffectiveMetadata,
    override_path: Option<&Path>,
    work: &Path,
) -> Result<()> {
    let reference = prepare_reference(resources, effective, override_path, work)?;
    command.extend(["--reference-doc".into(), reference.into_os_string()]);
    Ok(())
}

/// Resolve output aliases before checking whether an export would overwrite build data.
pub(crate) fn resolved_destination(path: &Path) -> Result<PathBuf> {
    let absolute = std::path::absolute(path)?;
    let mut resolved = PathBuf::new();
    // New outputs can alias other paths through symlinked parents or `..`.
    // Resolve existing components while retaining directories yet to be created.
    for component in absolute.components() {
        match component {
            std::path::Component::CurDir => {}
            std::path::Component::ParentDir => {
                resolved.pop();
            }
            component => {
                resolved.push(component.as_os_str());
                if matches!(component, std::path::Component::Normal(_)) && resolved.exists() {
                    resolved = resolved.canonicalize()?;
                }
            }
        }
    }
    // Windows paths compare case-insensitively even when the final file does
    // not exist yet and therefore cannot be canonicalized.
    Ok(if cfg!(windows) {
        PathBuf::from(resolved.to_string_lossy().to_lowercase())
    } else {
        resolved
    })
}
