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

/// Override the shared reference only for an explicit document or configured
/// margins, letting authored defaults select the reference in all other cases.
pub(crate) fn append_reference(
    command: &mut Vec<OsString>,
    resources: &ResourcePaths,
    effective: &EffectiveMetadata,
    override_path: Option<&Path>,
    work: &Path,
) -> Result<()> {
    if let Some(reference) =
        papper_document::docx::prepare_reference(resources, effective, override_path, work)?
    {
        command.extend(["--reference-doc".into(), reference.into_os_string()]);
    }
    Ok(())
}
