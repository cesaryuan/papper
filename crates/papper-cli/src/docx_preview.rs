//! Project-bound DOCX builder owned exclusively by the PDF preview service.
//!
//! `papper __docx_preview_server` passes current editor snapshots through the
//! complete DOCX pipeline and the server's embedded PDF renderer. Output paths
//! and build policy belong to this service; requests cannot run arbitrary DOCX
//! exports. Pandoc and citation caches survive between edits. Stdin EOF closes
//! the server and removes its private conversion files.

use anyhow::{Context, Result, bail};
use clap::Parser;
use papper_core::paths::{canonical_project, display_path};
use papper_core::resources::ResourcePaths;
use papper_engine::{PersistentWorker, WorkerConfig, WorkerRequest, discover_engine};
use papper_server::assets::AssetCache;
use serde::Deserialize;
use serde_json::{Map, Value, json};
use std::collections::BTreeMap;
use std::ffi::OsString;
use std::path::{Path, PathBuf};
use std::time::Instant;

use crate::{Cli, CliCommand};

/// One editor snapshot; no request-selected output path or build options are accepted.
#[derive(Deserialize)]
struct PreviewRequest {
    source: PathBuf,
    source_text: String,
}

/// Retain native conversion state while keeping each request's files and metadata independent.
pub(crate) struct PreviewBuilder {
    pub(crate) directory: tempfile::TempDir,
    project: PathBuf,
    resources: ResourcePaths,
    worker: Option<PersistentWorker>,
    assets: Option<(Vec<String>, AssetCache)>,
    previous_environment: BTreeMap<String, Option<String>>,
}

impl PreviewBuilder {
    /// Keep preview intermediates under an editor-owned root for cleanup after forced termination.
    fn new(parent: &Path) -> Result<Self> {
        std::fs::create_dir_all(parent)?;
        let directory = tempfile::Builder::new()
            .prefix("papper-docx-preview-")
            .tempdir_in(parent)?;
        Ok(Self {
            directory,
            project: canonical_project(&std::env::current_dir()?)?,
            resources: ResourcePaths::discover()?,
            worker: None,
            assets: None,
            previous_environment: BTreeMap::new(),
        })
    }

    /// Execute current options in the retained native process, validating citation dependencies each time.
    pub(crate) fn convert(
        &mut self,
        command: &[OsString],
        environment: &BTreeMap<String, Option<String>>,
        roots: &[PathBuf],
        metadata: &Map<String, Value>,
        source: &Path,
        text: &str,
    ) -> Result<()> {
        let arguments = command
            .iter()
            .map(|arg| {
                arg.to_str()
                    .context("DOCX worker argument is not valid Unicode")
                    .map(str::to_owned)
            })
            .collect::<Result<Vec<_>>>()?;
        let mut dependency_args = Vec::new();
        let mut index = 0;
        // Build-local output/reference/metadata names change on every request. Metadata
        // is fingerprinted by value; references are regenerated and read afresh by Pandoc.
        while index + 1 < arguments.len() {
            if matches!(
                arguments[index].as_str(),
                "--output" | "--metadata-file" | "--reference-doc"
            ) {
                index += 2;
            } else {
                dependency_args.push(arguments[index].clone());
                index += 1;
            }
        }
        if self
            .assets
            .as_ref()
            .is_none_or(|(previous, _)| previous != &dependency_args)
        {
            self.assets = Some((
                dependency_args.clone(),
                AssetCache::new(
                    self.project.clone(),
                    roots.to_vec(),
                    dependency_args,
                    self.directory.path().join("citations"),
                ),
            ));
        }
        let assets = &mut self.assets.as_mut().unwrap().1;
        assets.resource_roots(roots);
        let fingerprint = assets.fingerprint(metadata, source, text, &[])?;
        if self.worker.is_none() {
            self.worker = Some(PersistentWorker::start(
                discover_engine(&self.resources.root)?,
                WorkerConfig {
                    pandoc_args: vec![
                        "--from".into(),
                        "markdown".into(),
                        "--to".into(),
                        "docx".into(),
                    ],
                    project_dir: self.project.clone(),
                    work_dirs: vec![self.directory.path().to_path_buf()],
                },
                &self.directory.path().join("worker.json"),
                &self.directory.path().join("worker.log"),
            )?);
        }
        let output_index = arguments
            .iter()
            .position(|arg| arg == "--output")
            .context("DOCX output option missing")?;
        let mut request_environment = environment.clone();
        // A helper or option may disappear between requests; restore inherited values
        // instead of leaking the preceding document's process environment into this one.
        for key in self.previous_environment.keys() {
            request_environment
                .entry(key.clone())
                .or_insert_with(|| std::env::var(key).ok());
        }
        self.previous_environment = request_environment.clone();
        self.worker.as_mut().unwrap().request(&WorkerRequest {
            arguments: Some(arguments.clone()),
            input: PathBuf::from(arguments.last().context("DOCX input missing")?),
            output: PathBuf::from(&arguments[output_index + 1]),
            asset_fingerprint: assets.cacheable.then_some(fingerprint),
            filter_environment: Some(request_environment),
            remote_resources: Some(
                assets
                    .remote_paths
                    .iter()
                    .map(|(url, path)| (url.clone(), PathBuf::from(path)))
                    .collect(),
            ),
            ..WorkerRequest::default()
        })?;
        Ok(())
    }

    /// Build a private DOCX from the current buffer without exposing general export controls.
    fn build(&mut self, request: PreviewRequest) -> Result<(Vec<u8>, Value)> {
        let started = Instant::now();
        let source = self
            .project
            .join(&request.source)
            .canonicalize()
            .context("DOCX source not found")?;
        anyhow::ensure!(
            source.is_file(),
            "DOCX source must be a saved Markdown file"
        );
        let source = PathBuf::from(display_path(&source));
        let output = self.directory.path().join("preview.docx");
        let argv = vec![
            "papper".into(),
            "build".into(),
            "docx".into(),
            source.as_os_str().to_owned(),
            "--output-file".into(),
            output.as_os_str().to_owned(),
        ];
        let Some(CliCommand::Build(mut args)) = Cli::try_parse_from(argv)?.command else {
            bail!("Expected DOCX build options");
        };
        if args.style_file.is_none() {
            args.style_file = std::env::var_os("PMT_STYLE_FILE").map(PathBuf::from);
        }
        if args.reference_doc.is_none() {
            args.reference_doc = std::env::var_os("PMT_REFERENCE_DOC").map(PathBuf::from);
        }
        for selected in [&args.style_file, &args.reference_doc]
            .into_iter()
            .flatten()
        {
            anyhow::ensure!(
                self.project.join(selected).is_file(),
                "DOCX configuration file not found: {}",
                selected.display()
            );
        }
        let reference = args
            .reference_doc
            .as_ref()
            .map(|path| self.project.join(path))
            .unwrap_or_else(|| {
                self.resources
                    .resource("pandoc/manuscript-template/reference-doc.docx")
            });
        anyhow::ensure!(
            output != crate::docx_pipeline::resolved_destination(&reference)?,
            "DOCX output must not overwrite the reference document"
        );
        let project = self.project.clone();
        let resources = self.resources.clone();
        crate::commands::build_other_with_worker(
            &args,
            &project,
            &source,
            &output,
            &resources,
            Some(&request.source_text),
            Some(self),
        )?;
        Ok((
            std::fs::read(&output)?,
            json!({
                "timings_ms":{"total":started.elapsed().as_secs_f64() * 1000.0},
                "worker_pid":self.worker.as_ref().and_then(PersistentWorker::pid)
            }),
        ))
    }
}

impl Drop for PreviewBuilder {
    /// Close the native process before its directory is dropped, including error/EOF paths on Windows.
    fn drop(&mut self) {
        self.worker.take();
    }
}

/// Own the preview-only builder until Papper server observes editor shutdown.
pub(crate) fn run(token: &str, work_dir: &Path) -> Result<()> {
    let mut state = PreviewBuilder::new(work_dir)?;
    papper_server::docx_preview::run(token, move |payload| {
        state.build(serde_json::from_value(payload)?)
    })
}
