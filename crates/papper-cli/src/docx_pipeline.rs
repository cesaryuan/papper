//! Share DOCX defaults and final output filters between manuscript and reply builds.

use anyhow::{Context, Result};
use papper_core::metadata::EffectiveMetadata;
use papper_core::paths::{atomic_write, pandoc_path};
use papper_core::resources::ResourcePaths;
use std::ffi::OsString;
use std::path::{Path, PathBuf};

/// Reuse authored DOCX defaults, omitting numbering and its dependent passes
/// because reply references already contain their original manuscript numbers.
pub(crate) fn reply_defaults(resources: &ResourcePaths, work: &Path) -> Result<PathBuf> {
    let source = resources.resource("pandoc/pandoc-docx.yml");
    let mut defaults: serde_yaml::Value = serde_yaml::from_str(&std::fs::read_to_string(source)?)?;
    let filters = defaults
        .get_mut("filters")
        .and_then(serde_yaml::Value::as_sequence_mut)
        .context("DOCX defaults are missing their filter list")?;
    filters.retain(|filter| {
        !matches!(
            filter.as_str(),
            Some(
                "pandoc-crossref"
                    | "citeproc"
                    | "${.}/filters/shared/bilingual_captions.lua"
                    | "${.}/filters/docx/native_crossrefs.lua"
                    | "${.}/filters/docx/native_citations.lua"
            )
        )
    });
    // The generated file lives outside pandoc/, so resolve resource-relative
    // paths against the authored defaults before handing it to Pandoc.
    let text = serde_yaml::to_string(&defaults)?.replace("${.}", &pandoc_path(&resources.pandoc));
    let target = work.join("pandoc-reply-docx.yml");
    atomic_write(&target, text.as_bytes())?;
    Ok(target)
}

/// Append metadata and image filters after the shared defaults in writer order.
pub(crate) fn append_output_filters(command: &mut Vec<OsString>, resources: &ResourcePaths) {
    for kind in ["docx_metadata", "svg_embed_images", "svg_to_png"] {
        command.extend([
            "--lua-filter".into(),
            resources
                .resource(format!("pandoc/filters/docx/{kind}.lua"))
                .into_os_string(),
        ]);
    }
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
