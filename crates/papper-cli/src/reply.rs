//! Build reviewer replies with manuscript numbering and native DOCX/TXT outputs.

mod line_source;
mod resolve;

use anyhow::{Context, Result};
use papper_core::metadata::{MetadataOptions, load_effective_metadata_text, write_pandoc_metadata};
use papper_core::paths::{
    atomic_write, canonical_project, display_path, pandoc_path, project_state_dir,
};
use papper_core::resources::ResourcePaths;
use papper_document::docx::{
    DocxPostprocessOptions, derive_docx_pandoc_metadata, postprocess_docx,
};
use papper_engine::{PandocCli, discover_engine};
use std::collections::BTreeMap;
use std::ffi::OsString;
use std::path::{Path, PathBuf};

pub use line_source::extract_pdf_command;

/// Resolve companions beside the reply first, retaining absolute explicit paths.
fn companion(reply: &Path, requested: &Path, project: &Path) -> PathBuf {
    if requested.is_absolute() {
        return requested.to_path_buf();
    }
    for parent in [reply.parent().unwrap_or(project), project] {
        let candidate = parent.join(requested);
        if candidate.exists() {
            return candidate;
        }
    }
    project.join(requested)
}

/// Resolve all reply placeholders and publish a complete DOCX or TXT atomically.
pub fn build(args: &crate::ReplyArgs) -> Result<()> {
    let project = canonical_project(&std::env::current_dir()?)?;
    let reply = if args.markdown.is_absolute() {
        args.markdown.clone()
    } else {
        project.join(&args.markdown)
    };
    anyhow::ensure!(
        reply.exists(),
        "Reply markdown file not found: {}",
        reply.display()
    );
    anyhow::ensure!(
        reply.is_file(),
        "Reply markdown path is not a file: {}",
        reply.display()
    );
    let reply = PathBuf::from(display_path(&reply.canonicalize()?));
    let output = if args.output == "output/docx/<reply-name>.docx" {
        project.join("output/docx").join(format!(
            "{}.docx",
            reply.file_stem().unwrap_or_default().to_string_lossy()
        ))
    } else {
        let requested = PathBuf::from(&args.output);
        if requested.is_absolute() {
            requested
        } else {
            project.join(requested)
        }
    };
    let extension = output
        .extension()
        .and_then(|value| value.to_str())
        .unwrap_or("")
        .to_lowercase();
    anyhow::ensure!(
        matches!(extension.as_str(), "txt" | "docx"),
        "Unsupported reply output suffix `{}`; use .docx or .txt",
        output
            .extension()
            .and_then(|value| value.to_str())
            .unwrap_or("")
    );
    anyhow::ensure!(
        !output.is_dir(),
        "Output path is a directory and cannot be overwritten: {}",
        output.display()
    );
    if output.exists() {
        std::fs::OpenOptions::new()
            .read(true)
            .write(true)
            .open(&output)
            .with_context(|| {
                format!(
                    "Output file appears to be open or locked. Close it and retry: {}",
                    output.display()
                )
            })?;
    }
    let manuscript = companion(&reply, &args.reply_manuscript, &project);
    let line_source = companion(&reply, &args.manuscript_line_source, &project);
    let resources = ResourcePaths::discover()?;
    let temporary = tempfile::Builder::new().prefix("papper-reply-").tempdir()?;
    let mut styles = vec![
        reply.parent().unwrap().join("style.yml"),
        project.join("style.yml"),
    ];
    styles.dedup();
    let options = MetadataOptions {
        style_paths: styles.into_iter().filter(|path| path.is_file()).collect(),
        bundled_style_dir: resources.template.clone(),
        allow_missing_header: true,
        reply: true,
        resource_roots: vec![
            reply.parent().unwrap().to_path_buf(),
            project.clone(),
            resources.root.clone(),
        ],
        ..MetadataOptions::default()
    };
    let text = std::fs::read_to_string(&reply)?.replace("\r\n", "\n");
    let effective = load_effective_metadata_text(&text, &reply, &options)?;
    let use_mathtype = if extension == "docx" && effective.pmt_settings.fields().mathtype {
        match papper_document::docx::check_mathtype_available(&resources, &effective) {
            Ok(()) => true,
            Err(error) => {
                eprintln!(
                    "[WARN] MathType was requested by reply metadata, but conversion will be skipped: {error:#}"
                );
                eprintln!("[WARN] Building reply DOCX with Pandoc/Word equations instead");
                false
            }
        }
    } else {
        false
    };
    let metadata = derive_docx_pandoc_metadata(&effective, use_mathtype)?;
    let metadata_path = write_pandoc_metadata(&metadata, temporary.path().join("metadata.yml"))?;
    let engine = PandocCli::new(discover_engine(&resources.root)?);
    let mut environment = BTreeMap::from([(
        "PMT_CITATION_NUMBER_RANGE_DELIMITER".into(),
        effective
            .pmt_settings
            .fields()
            .citation_number_range_delimiter
            .as_deref()
            .filter(|raw| *raw != "–")
            .map(str::to_owned),
    )]);
    environment.insert(
        "PMT_ENABLE_MATHTYPE_MARKERS".into(),
        use_mathtype.then(|| "true".into()),
    );
    environment.insert(
        "PMT_TABLE_AUTOFIT".into(),
        Some(
            effective
                .pmt_settings
                .fields()
                .table_autofit
                .as_str()
                .into(),
        ),
    );
    let resolved = resolve::resolve_reply_markdown(
        &text,
        &resolve::ReplyResolver {
            manuscript: &manuscript,
            metadata: &metadata_path,
            work: temporary.path(),
            effective: &effective,
            from_format: &args.from_format,
            engine: &engine,
            environment: &environment,
        },
        extension == "docx",
    )?;
    let resolved = line_source::resolve_line_regexes(&resolved, &line_source, temporary.path())?;
    if extension == "txt" {
        atomic_write(
            &output,
            resolve::render_reply_txt_markdown(&resolved).as_bytes(),
        )?;
    } else {
        let source = temporary.path().join("resolved.md");
        atomic_write(&source, resolved.as_bytes())?;
        let generated = temporary.path().join("raw.docx");
        let defaults = crate::docx_pipeline::reply_defaults(&resources, temporary.path())?;
        let reference = args.reference_doc.as_deref().map(|path| {
            if path.is_absolute() {
                path.to_path_buf()
            } else {
                project.join(path)
            }
        });
        let resource_path = [reply.parent().unwrap(), project.as_path()]
            .iter()
            .map(|path| pandoc_path(path))
            .collect::<Vec<_>>()
            .join(if cfg!(windows) { ";" } else { ":" });
        let mut command: Vec<OsString> = vec![
            "--defaults".into(),
            defaults.into_os_string(),
            source.into_os_string(),
            "-f".into(),
            args.from_format.clone().into(),
            "-o".into(),
            generated.as_os_str().to_owned(),
            "--resource-path".into(),
            resource_path.into(),
            "--metadata-file".into(),
            metadata_path.into_os_string(),
        ];
        crate::docx_pipeline::append_reference(
            &mut command,
            &resources,
            &effective,
            reference.as_deref(),
            temporary.path(),
        )?;
        crate::docx_pipeline::append_output_filters(&mut command, &resources);
        environment.extend(crate::images::filter_environment(
            &resources,
            &effective.pmt_settings,
            &[project.clone(), reply.parent().unwrap().to_path_buf()],
            &temporary.path().join("svg-embedded"),
            &temporary.path().join("svg-png"),
            &project_state_dir(&project)?.join("cache/svg-rsvg"),
        )?);
        engine.run(&command, &project, &environment)?;
        let processed = temporary.path().join("processed.docx");
        postprocess_docx(
            &generated,
            &processed,
            &effective,
            &DocxPostprocessOptions {
                skip_author_info: true,
                reply_style_formatting: true,
                native_crossrefs: false,
            },
        )?;
        let final_docx = if use_mathtype {
            let converted = temporary.path().join("converted.docx");
            papper_document::docx::convert_marked_docx(
                &processed, &converted, &resources, &effective, &project,
            )?;
            converted
        } else {
            processed
        };
        atomic_write(&output, &std::fs::read(final_docx)?)?;
    }
    println!(
        "[OK] Reply {} created: {}",
        extension.to_uppercase(),
        output.display()
    );
    Ok(())
}
