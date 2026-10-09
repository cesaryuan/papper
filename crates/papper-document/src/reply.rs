//! Resolve reviewer replies for Word, HTML and retained plain-text output.
//!
//! References and copied items keep the manuscript's numbering; line placeholders
//! use the manuscript or an explicit rendered line source. Output defaults omit
//! numbering passes so already resolved replies are not numbered a second time.

mod line_source;
mod resolve;

use anyhow::{Context, Result};
use papper_core::paths::{atomic_write, pandoc_path};
use papper_core::resources::ResourcePaths;
use std::path::{Path, PathBuf};

pub use line_source::{extract_pdf_command, resolve_line_regexes};
pub use resolve::{ReplyResolver, render_reply_txt_markdown, resolve_reply_markdown};

/// Select the copied-equation layout appropriate to the final document.
#[derive(Clone, Copy, PartialEq, Eq)]
pub enum ReplyFormat {
    Docx,
    Html,
    Text,
}

/// Reuse authored defaults while retaining the manuscript's resolved numbering.
pub fn reply_defaults(resources: &ResourcePaths, work: &Path, target: &str) -> Result<PathBuf> {
    let source = resources.resource(format!("pandoc/pandoc-{target}.yml"));
    let mut defaults: serde_yaml::Value = serde_yaml::from_str(&std::fs::read_to_string(source)?)?;
    let filters = defaults
        .get_mut("filters")
        .and_then(serde_yaml::Value::as_sequence_mut)
        .context("Output defaults are missing their filter list")?;
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
    // Temporary defaults must retain paths relative to the authored pandoc directory.
    let text = serde_yaml::to_string(&defaults)?.replace("${.}", &pandoc_path(&resources.pandoc));
    let target = work.join(format!("pandoc-reply-{target}.yml"));
    atomic_write(&target, text.as_bytes())?;
    Ok(target)
}
