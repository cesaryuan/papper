//! Build standalone HTML and supervise the same pipeline in a persistent HTTP service.

use anyhow::{Context, Result, bail};
use notify::{RecommendedWatcher, RecursiveMode, Watcher};
use papper_core::metadata::{
    EffectiveMetadata, MetadataOptions, load_effective_metadata_text, markdown_without_yaml_header,
    reply_manuscript_text, write_pandoc_metadata,
};
use papper_core::paths::{
    atomic_write, canonical_project, display_path, pandoc_path, project_work_dir, write_if_changed,
};
use papper_core::resources::ResourcePaths;
use papper_document::html::{
    postprocess_html_text_with_style_settings, postprocess_reply_html_text_with_style_settings,
    prepare_html_metadata,
};
use papper_engine::{PandocCli, PersistentWorker, WorkerConfig, WorkerRequest, discover_engine};
use serde::{Deserialize, Serialize};
use serde_json::{Map, Value, json};
use std::collections::{BTreeMap, HashMap, VecDeque};
use std::ffi::OsString;
use std::fs::{File, OpenOptions};
use std::io::Read;
use std::path::{Path, PathBuf};
use std::process::{Command, Stdio};
use std::sync::atomic::{AtomicBool, AtomicUsize, Ordering};
use std::sync::mpsc::{self, Receiver, TryRecvError};
use std::sync::{Arc, Mutex, RwLock};
use std::thread;
use std::time::{Duration, Instant};
use tiny_http::{Header, Method, Request, Response, Server, StatusCode};

use crate::assets::{AssetCache, digest};

/// Fully resolved per-invocation HTML inputs, independent of mutable global settings.
#[derive(Clone, Debug)]
pub struct HtmlBuildRequest {
    pub project: PathBuf,
    pub source: PathBuf,
    pub output: PathBuf,
    pub style_file: Option<PathBuf>,
    pub resource_path: Option<String>,
    pub manuscript_line_source: Option<PathBuf>,
    pub resources: ResourcePaths,
}

/// Metadata discovery policy retained across editor and CLI requests.
#[derive(Clone, Debug, Default, Serialize, Deserialize)]
pub struct MetadataSources {
    #[serde(default = "default_style_file")]
    pub style_file: PathBuf,
    #[serde(default)]
    pub style_paths: Vec<PathBuf>,
    #[serde(default)]
    pub project_name: String,
    #[serde(default)]
    pub resource_path_explicit: bool,
    #[serde(default)]
    pub manuscript_line_source: Option<PathBuf>,
}

/// Preserve automatic style discovery when older service configs omit its name.
fn default_style_file() -> PathBuf {
    PathBuf::from("style.yml")
}

/// Versioned project configuration accepted by the native HTTP runtime.
#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct ServerConfig {
    #[serde(default)]
    pub runtime_version: String,
    #[serde(default)]
    pub source_text_protocol: u32,
    pub project_dir: PathBuf,
    pub pandoc_args: Vec<String>,
    #[serde(default)]
    pub pandoc_metadata: Map<String, Value>,
    #[serde(default)]
    pub resource_paths: Vec<PathBuf>,
    #[serde(default)]
    pub metadata_sources: Option<MetadataSources>,
    #[serde(default)]
    pub resource_root: Option<PathBuf>,
}

/// Prepared metadata and filter environment shared by cold and warm builds.
#[derive(Clone)]
struct PreparedMetadata {
    effective: EffectiveMetadata,
    environment: BTreeMap<String, Option<String>>,
    metadata_file: PathBuf,
    reference_styles_xml: String,
}

/// Retain request results only while their content/dependencies remain identical.
struct ConversionState {
    runtime_id: String,
    config: ServerConfig,
    resources: ResourcePaths,
    worker: PersistentWorker,
    assets: AssetCache,
    work: tempfile::TempDir,
    metadata_cache: HashMap<String, PreparedMetadata>,
    results: VecDeque<(String, String)>,
    bytes: usize,
    requests: usize,
    hits: usize,
    citation_hits: usize,
}

/// Keep health checks responsive while a long conversion owns the native worker.
struct HttpState {
    conversion: Mutex<ConversionState>,
    version: RwLock<Value>,
    shutdown: AtomicBool,
}

/// Preserve search precedence while removing duplicate resource directories.
fn unique_paths(paths: impl IntoIterator<Item = PathBuf>) -> Vec<PathBuf> {
    let mut result = Vec::new();
    for path in paths {
        if !result.contains(&path) {
            result.push(path);
        }
    }
    result
}

/// Resolve style candidates, retaining missing paths for later creation invalidation.
fn style_candidates(config: &ServerConfig, source: &Path) -> Vec<PathBuf> {
    let Some(context) = &config.metadata_sources else {
        return Vec::new();
    };
    unique_paths([
        source
            .parent()
            .unwrap_or(&config.project_dir)
            .join(&context.style_file),
        config.project_dir.join(&context.style_file),
    ])
}

/// Strip editor BOM and normalize platform newlines once before parsing and hashing.
fn normalize_text(text: &str) -> String {
    text.trim_start_matches('\u{feff}')
        .replace("\r\n", "\n")
        .replace('\r', "\n")
}

/// Set or clear the Unicode-safe citation delimiter for a persistent worker request.
fn filter_environment(effective: &EffectiveMetadata) -> BTreeMap<String, Option<String>> {
    let delimiter = effective
        .pmt_settings
        .fields()
        .citation_number_range_delimiter
        .as_deref()
        .filter(|raw| *raw != "–");
    BTreeMap::from([
        (
            "PMT_CITATION_NUMBER_RANGE_DELIMITER".to_string(),
            delimiter.map(str::to_string),
        ),
        (
            "PMT_TABLE_AUTOFIT".into(),
            Some(
                effective
                    .pmt_settings
                    .fields()
                    .table_autofit
                    .as_str()
                    .into(),
            ),
        ),
    ])
}

/// Parse metadata from the immutable source snapshot and generate reference styles.
fn prepare(
    config: &ServerConfig,
    resources: &ResourcePaths,
    source: &Path,
    text: &str,
    work: &Path,
) -> Result<PreparedMetadata> {
    let effective = if let Some(context) = &config.metadata_sources {
        let options = MetadataOptions {
            style_paths: style_candidates(config, source)
                .into_iter()
                .filter(|path| path.is_file())
                .collect(),
            bundled_style_dir: resources.resource("defaults"),
            allow_missing_header: true,
            reply: reply_manuscript_text(text, source)?.is_some(),
            resource_roots: unique_paths(
                std::iter::once(source.parent().unwrap_or(&config.project_dir).to_path_buf())
                    .chain(std::iter::once(config.project_dir.clone()))
                    .chain(config.resource_paths.clone())
                    .chain(std::iter::once(resources.root.clone())),
            ),
            ..MetadataOptions::default()
        };
        let mut effective = load_effective_metadata_text(text, source, &options)?;
        let name = if context.project_name.is_empty() {
            source
                .file_stem()
                .and_then(|s| s.to_str())
                .unwrap_or("manuscript")
        } else {
            &context.project_name
        };
        prepare_html_metadata(
            &mut effective,
            &resources.resource("pandoc/manuscript-template/reference-doc/word/styles.xml"),
            name,
        )?;
        effective
    } else {
        EffectiveMetadata {
            pandoc_metadata: config.pandoc_metadata.clone(),
            pmt_settings: Default::default(),
            has_yaml_header: false,
        }
    };
    // A cached metadata entry must own immutable bytes. Reusing a source-only
    // filename let an A -> B -> A header edit read B's overwritten YAML while
    // postprocessing A's metadata, producing a document with mixed headers.
    let filename = format!(
        "metadata-{}-{}.yml",
        &digest(pandoc_path(source).as_bytes())[..8],
        &digest(&serde_json::to_vec(&effective.pandoc_metadata)?)[..24]
    );
    let metadata_file = write_pandoc_metadata(&effective.pandoc_metadata, work.join(filename))?;
    let environment = filter_environment(&effective);
    Ok(PreparedMetadata {
        effective,
        environment,
        metadata_file,
        reference_styles_xml: std::fs::read_to_string(
            resources.resource("pandoc/manuscript-template/reference-doc/word/styles.xml"),
        )?,
    })
}

impl ConversionState {
    /// Start one owned Haskell worker with its private workspace and project permissions.
    fn new(mut config: ServerConfig) -> Result<Self> {
        let runtime_id = crate::process::ServerExecutable::current()?.identity;
        config.project_dir = canonical_project(&config.project_dir)?;
        let resources = if let Some(root) = &config.resource_root {
            ResourcePaths {
                root: root.clone(),
                pandoc: root.join("pandoc"),
                template: if root.join("template").is_dir() {
                    root.join("template")
                } else {
                    root.join("_template")
                },
            }
        } else {
            ResourcePaths::discover()?
        };
        let persistent = project_work_dir(&config.project_dir)?;
        std::fs::create_dir_all(&persistent)?;
        let work = tempfile::Builder::new()
            .prefix("papper-server-")
            .tempdir()?;
        let worker = PersistentWorker::start(
            discover_engine(&resources.root)?,
            WorkerConfig {
                project_dir: config.project_dir.clone(),
                pandoc_args: config.pandoc_args.clone(),
                work_dirs: vec![work.path().to_path_buf()],
            },
            &persistent.join("worker.json"),
            &persistent.join("worker.log"),
        )?;
        let assets = AssetCache::new(
            config.project_dir.clone(),
            config.resource_paths.clone(),
            config.pandoc_args.clone(),
            persistent.join("remote"),
        );
        Ok(Self {
            runtime_id,
            config,
            resources,
            worker,
            assets,
            work,
            metadata_cache: HashMap::new(),
            results: VecDeque::new(),
            bytes: 0,
            requests: 0,
            hits: 0,
            citation_hits: 0,
        })
    }

    /// Resolve source-local context independently of the service's working-directory project.
    fn source(&self, raw: &str, buffer: bool) -> Result<PathBuf> {
        let joined = self.config.project_dir.join(raw);
        let source = if joined.exists() {
            PathBuf::from(display_path(&joined.canonicalize()?))
        } else if buffer {
            let parent = joined
                .parent()
                .context("Source path has no parent")?
                .canonicalize()?;
            PathBuf::from(display_path(&parent))
                .join(joined.file_name().context("Source path has no filename")?)
        } else {
            bail!("Markdown file not found: {}", joined.display());
        };
        // Editors bootstrap with temporary Markdown outside the project, and
        // authored manuscripts may also live elsewhere. Canonicalization still
        // supplies consistent style/resource context for disk and buffer builds.
        Ok(source)
    }

    /// Convert an immutable editor or disk snapshot with complete dependency invalidation.
    fn convert(&mut self, payload: &Value) -> Result<Value> {
        let started = Instant::now();
        anyhow::ensure!(payload.is_object(), "Request must be a JSON object");
        let path = match payload.get("path") {
            None => "manuscript.md",
            Some(Value::String(path)) => path.as_str(),
            _ => bail!("Source path must be a string"),
        };
        let buffer = match payload.get("text") {
            None | Some(Value::Null) => None,
            Some(Value::String(text)) => Some(text.as_str()),
            _ => bail!("Source text must be a string"),
        };
        let mode = match payload.get("mode") {
            None => "exact",
            Some(Value::String(mode)) => mode.as_str(),
            _ => bail!("Conversion mode must be a string"),
        };
        anyhow::ensure!(
            matches!(mode, "exact" | "preview"),
            "Unknown conversion mode: {mode}"
        );
        let source = self.source(path, buffer.is_some())?;
        let explicit = self
            .config
            .metadata_sources
            .as_ref()
            .is_some_and(|context| context.resource_path_explicit);
        let roots = if explicit {
            self.config.resource_paths.clone()
        } else {
            unique_paths(
                std::iter::once(source.parent().unwrap().to_path_buf())
                    .chain(self.config.resource_paths.clone()),
            )
        };
        self.assets.resource_roots(&roots);
        let disk_text;
        let text = if let Some(buffer) = buffer {
            normalize_text(buffer)
        } else {
            disk_text = std::fs::read_to_string(&source)?;
            normalize_text(&disk_text)
        };
        let body = if self.config.metadata_sources.is_some() {
            markdown_without_yaml_header(&text)
        } else {
            &text
        };
        let header_len = text.len() - body.len();
        let mut dependencies = style_candidates(&self.config, &source);
        dependencies.extend([
            self.resources.resource("defaults/style.yml"),
            self.resources.resource("defaults/style-cn.yml"),
            self.resources
                .resource("pandoc/manuscript-template/reference-doc/word/styles.xml"),
        ]);
        let mut signature = text.as_bytes()[..header_len].to_vec();
        signature.extend(pandoc_path(&source).as_bytes());
        for dependency in &dependencies {
            signature.extend(pandoc_path(dependency).as_bytes());
            signature.extend(std::fs::read(dependency).unwrap_or_default());
        }
        let metadata_key = digest(&signature);
        let prepared = if let Some(previous) = self.metadata_cache.get(&metadata_key) {
            previous.clone()
        } else {
            let prepared = prepare(
                &self.config,
                &self.resources,
                &source,
                &text,
                self.work.path(),
            )?;
            // Bound this independently of successful HTML: malformed/repeated headers cannot leak memory.
            if self.metadata_cache.len() >= 16 {
                self.metadata_cache.clear();
            }
            self.metadata_cache.insert(metadata_key, prepared.clone());
            prepared
        };
        let asset_started = Instant::now();
        // Reply output depends on another manuscript and its rendered line source.
        // Resolve those current inputs on every request rather than reuse manuscript-only caches.
        if reply_manuscript_text(&text, &source)?.is_some() {
            let html = render_reply_html(
                &self.config,
                &self.resources,
                &source,
                &text,
                &prepared,
                self.work.path(),
                mode == "preview",
            )?;
            return Ok(
                json!({"path":path,"output":html,"cache_hit":false,"citeproc_cache_hit":false,"mode":mode,
                "timings":{"total":started.elapsed().as_secs_f64()*1000.0}}),
            );
        }
        let fingerprint = self.assets.fingerprint(
            &prepared.effective.pandoc_metadata,
            &source,
            &text,
            &dependencies,
        )?;
        let asset_ms = asset_started.elapsed().as_secs_f64() * 1000.0;
        let key = digest(
            format!(
                "{mode}:{}:{}:{fingerprint}",
                pandoc_path(&source),
                digest(text.as_bytes())
            )
            .as_bytes(),
        );
        self.requests += 1;
        if self.assets.cacheable
            && let Some(index) = self
                .results
                .iter()
                .position(|(previous, _)| previous == &key)
        {
            let entry = self.results.remove(index).unwrap();
            let html = entry.1.clone();
            self.results.push_back(entry);
            self.hits += 1;
            return Ok(
                json!({"path":path,"output":html,"cache_hit":true,"citeproc_cache_hit":false,"mode":mode,"timings":{"asset_cache":asset_ms,"total":started.elapsed().as_secs_f64()*1000.0}}),
            );
        }
        let snapshot = self.work.path().join("input.md");
        let output = self.work.path().join("output.html");
        atomic_write(&snapshot, body.as_bytes())?;
        let native_started = Instant::now();
        let bundle = self.assets.lua_bundle(self.work.path())?;
        let response = self.worker.request(&WorkerRequest {
            input: snapshot,
            output: output.clone(),
            mode: Some(mode.to_string()),
            metadata_file: Some(prepared.metadata_file),
            asset_fingerprint: Some(if self.assets.cacheable {
                fingerprint
            } else {
                format!("{fingerprint}-{}", self.requests)
            }),
            filter_environment: Some(prepared.environment),
            resource_paths: Some(roots),
            remote_resources: Some(
                self.assets
                    .remote_paths
                    .iter()
                    .map(|(url, path)| (url.clone(), PathBuf::from(path)))
                    .collect(),
            ),
            lua_bundle: bundle.as_ref().map(|(path, _)| path.clone()),
            lua_bundle_paths: bundle.map(|(_, paths)| paths),
            ..WorkerRequest::default()
        })?;
        let worker_ms = native_started.elapsed().as_secs_f64() * 1000.0;
        let post_started = Instant::now();
        let raw = std::fs::read_to_string(&output)?;
        let html = postprocess_html_text_with_style_settings(
            &raw,
            &Value::Object(prepared.effective.pandoc_metadata),
            mode == "preview",
            &prepared.reference_styles_xml,
            &prepared.effective.pmt_settings,
        )?;
        let post_ms = post_started.elapsed().as_secs_f64() * 1000.0;
        self.citation_hits += usize::from(response.citeproc_cache_hit);
        if self.assets.cacheable && html.len() <= 32 * 1024 * 1024 {
            self.bytes += html.len();
            self.results.push_back((key, html.clone()));
            while self.results.len() > 8 || self.bytes > 32 * 1024 * 1024 {
                if let Some((_, discarded)) = self.results.pop_front() {
                    self.bytes -= discarded.len();
                }
            }
        }
        let mut timings = json!({"asset_cache":asset_ms,"worker":worker_ms,"postprocess":post_ms,"total":started.elapsed().as_secs_f64()*1000.0});
        for (name, duration) in response.filters_ms {
            timings[format!("filter_{}", name.trim_start_matches("papper-embedded-"))] =
                json!(duration);
        }
        Ok(
            json!({"path":path,"output":html,"cache_hit":false,"citeproc_cache_hit":response.citeproc_cache_hit,"mode":mode,"timings":timings}),
        )
    }

    /// Expose separate whole-HTML and native citation reuse counters.
    fn metrics(&self) -> Value {
        json!({"html_entries":self.results.len(),"html_bytes":self.bytes,"asset_files":self.assets.asset_count,"requests":self.requests,"html_hits":self.hits,"citeproc_hits":self.citation_hits})
    }

    /// Publish immutable service identity and the last observed native worker status.
    fn version(&mut self) -> Value {
        // Runtime identity describes executable code, never reloadable config;
        // otherwise an old process can masquerade as an upgraded server.
        json!({"server":"PMT-Pandoc-Server/Rust","runtime":"rust","pid":std::process::id(),"worker_pid":self.worker.pid(),"runtime_version":format!("{}-rust-v1", env!("CARGO_PKG_VERSION")),"runtime_id":self.runtime_id,"protocol":"pmt-html-v1","worker_alive":self.worker.is_alive(),"source_text":true,"project_dir":display_path(&self.config.project_dir),"config_digest":digest(&serde_json::to_vec(&self.config).unwrap_or_default())})
    }
}

/// Build a cold HTML configuration from resolved CLI inputs and current metadata.
fn build_config(
    request: &HtmlBuildRequest,
    work: &Path,
    native_server: bool,
) -> Result<ServerConfig> {
    let roots = if let Some(explicit) = &request.resource_path {
        std::env::split_paths(explicit)
            .map(|path| {
                if path.is_absolute() {
                    path
                } else {
                    request.project.join(path)
                }
            })
            .collect()
    } else {
        unique_paths([
            request.source.parent().unwrap().to_path_buf(),
            request.project.clone(),
        ])
    };
    let project_name = request
        .source
        .file_stem()
        .and_then(|name| name.to_str())
        .unwrap_or("manuscript")
        .to_string();
    let context = MetadataSources {
        style_file: request
            .style_file
            .clone()
            .unwrap_or_else(default_style_file),
        project_name,
        resource_path_explicit: request.resource_path.is_some(),
        manuscript_line_source: request.manuscript_line_source.as_ref().map(|path| {
            if path.is_absolute() {
                path.clone()
            } else {
                request.project.join(path)
            }
        }),
        ..MetadataSources::default()
    };
    let mut config = ServerConfig {
        runtime_version: format!("{}-rust-v1", env!("CARGO_PKG_VERSION")),
        source_text_protocol: 1,
        project_dir: request.project.clone(),
        pandoc_args: Vec::new(),
        pandoc_metadata: Map::new(),
        resource_paths: roots,
        metadata_sources: Some(context),
        resource_root: Some(request.resources.root.clone()),
    };
    let resource_path = request.resource_path.clone().unwrap_or_else(|| {
        config
            .resource_paths
            .iter()
            .map(|path| pandoc_path(path))
            .collect::<Vec<_>>()
            .join(if cfg!(windows) { ";" } else { ":" })
    });
    config.pandoc_args = vec![
        "--defaults".into(),
        pandoc_path(&request.resources.resource("pandoc/pandoc-html.yml")),
        "--resource-path".into(),
        resource_path,
    ];
    if !native_server {
        let text = normalize_text(&std::fs::read_to_string(&request.source)?);
        let prepared = prepare(&config, &request.resources, &request.source, &text, work)?;
        config.pandoc_metadata = prepared.effective.pandoc_metadata;
        config.pandoc_args.extend([
            "--metadata-file".into(),
            pandoc_path(&prepared.metadata_file),
        ]);
    }
    // Native service requests derive metadata from their immutable source text.
    // Rebuilding CSS/YAML in every fresh client process only duplicates that
    // work and makes body/header edits restart an otherwise reusable worker.
    Ok(config)
}

/// Execute HTML through native Pandoc or an explicitly requested persistent service.
pub fn build_html(
    request: &HtmlBuildRequest,
    start_server: bool,
    host: &str,
    port: u16,
    server_command: Option<&str>,
) -> Result<()> {
    let work = project_work_dir(&request.project)?;
    std::fs::create_dir_all(&work)?;
    let native_server = start_server && server_command.is_none();
    let config = build_config(request, &work, native_server)?;
    // Honor explicit service startup even for external Markdown; otherwise the
    // CLI reports success after a one-shot build while editors find no service.
    if start_server {
        let config_path = work.join("server-config.json");
        // Serialize config publication, upgrade/restart and the initial build.
        // Two editor clients must not stop each other's replacement service.
        let _lifecycle = lock_service_startup(&config_path)?;
        write_if_changed(&config_path, &serde_json::to_vec_pretty(&config)?)?;
        ensure_server(&config, &config_path, host, port, server_command)?;
        let agent = local_agent(Duration::from_secs(120));
        let response = agent
            .post(&format!("{}/convert/raw", base_url(host, port)))
            .send_json(json!({"path":display_path(&request.source)}))
            .map_err(http_failure)?;
        let html = response.into_string()?;
        atomic_write(&request.output, html.as_bytes())?;
        return Ok(());
    }
    let temporary = tempfile::Builder::new().prefix("papper-html-").tempdir()?;
    let text = normalize_text(&std::fs::read_to_string(&request.source)?);
    if reply_manuscript_text(&text, &request.source)?.is_some() {
        let prepared = prepare(&config, &request.resources, &request.source, &text, &work)?;
        let html = render_reply_html(
            &config,
            &request.resources,
            &request.source,
            &text,
            &prepared,
            temporary.path(),
            false,
        )?;
        return atomic_write(&request.output, html.as_bytes());
    }
    let input = temporary.path().join("input.md");
    atomic_write(&input, markdown_without_yaml_header(&text).as_bytes())?;
    let output = temporary.path().join("output.html");
    let mut args: Vec<OsString> = config.pandoc_args.iter().map(OsString::from).collect();
    args.extend([
        OsString::from("--output"),
        output.as_os_str().to_owned(),
        input.as_os_str().to_owned(),
    ]);
    let mut effective = EffectiveMetadata {
        pandoc_metadata: config.pandoc_metadata.clone(),
        pmt_settings: Default::default(),
        has_yaml_header: false,
    };
    // Resolve application settings again only for the single-shot filter environment.
    if config.metadata_sources.is_some() {
        effective = prepare(&config, &request.resources, &request.source, &text, &work)?.effective;
    }
    PandocCli::new(discover_engine(&request.resources.root)?).run(
        &args,
        &request.project,
        &filter_environment(&effective),
    )?;
    let raw = std::fs::read_to_string(output)?;
    let xml = std::fs::read_to_string(
        request
            .resources
            .resource("pandoc/manuscript-template/reference-doc/word/styles.xml"),
    )?;
    let html = postprocess_html_text_with_style_settings(
        &raw,
        &Value::Object(config.pandoc_metadata),
        false,
        &xml,
        &effective.pmt_settings,
    )?;
    atomic_write(&request.output, html.as_bytes())
}

/// Render resolved reply prose with HTML defaults that preserve manuscript numbers.
fn render_reply_html(
    config: &ServerConfig,
    resources: &ResourcePaths,
    source: &Path,
    text: &str,
    prepared: &PreparedMetadata,
    work: &Path,
    preview: bool,
) -> Result<String> {
    let manuscript = reply_manuscript_text(text, source)?.context("Reply header is missing")?;
    anyhow::ensure!(
        manuscript.is_file(),
        "Reply manuscript not found: {}",
        manuscript.display()
    );
    eprintln!("[REPLY] Using manuscript: {}", manuscript.display());
    let engine = PandocCli::new(discover_engine(&resources.root)?);
    let resolver = papper_document::reply::ReplyResolver {
        manuscript: &manuscript,
        metadata: &prepared.metadata_file,
        work,
        effective: &prepared.effective,
        from_format: "markdown",
        engine: &engine,
        environment: &prepared.environment,
    };
    let numbered = papper_document::reply::resolve_reply_markdown(
        markdown_without_yaml_header(text),
        &resolver,
        papper_document::reply::ReplyFormat::Html,
    )?;
    let line_source = config
        .metadata_sources
        .as_ref()
        .and_then(|context| context.manuscript_line_source.as_deref())
        .unwrap_or(&manuscript);
    let resolved = papper_document::reply::resolve_line_regexes(&numbered, line_source, work)?;
    let input = work.join("reply.md");
    let output = work.join("reply.html");
    atomic_write(&input, resolved.as_bytes())?;
    let roots = if config
        .metadata_sources
        .as_ref()
        .is_some_and(|context| context.resource_path_explicit)
    {
        config.resource_paths.clone()
    } else {
        unique_paths(
            std::iter::once(source.parent().unwrap().to_path_buf())
                .chain(config.resource_paths.clone()),
        )
    };
    let resource_path = roots
        .iter()
        .map(|path| pandoc_path(path))
        .collect::<Vec<_>>()
        .join(if cfg!(windows) { ";" } else { ":" });
    let args: Vec<OsString> = vec![
        "--defaults".into(),
        papper_document::reply::reply_defaults(resources, work, "html")?.into_os_string(),
        "--metadata-file".into(),
        prepared.metadata_file.as_os_str().to_owned(),
        "--resource-path".into(),
        resource_path.into(),
        "-o".into(),
        output.as_os_str().to_owned(),
        input.into_os_string(),
    ];
    engine.run(&args, &config.project_dir, &prepared.environment)?;
    postprocess_reply_html_text_with_style_settings(
        &std::fs::read_to_string(output)?,
        &Value::Object(prepared.effective.pandoc_metadata.clone()),
        preview,
        &prepared.reference_styles_xml,
        &prepared.effective.pmt_settings,
    )
}

/// Format IPv4, hostname or bracketed IPv6 HTTP endpoints.
fn base_url(host: &str, port: u16) -> String {
    let host = if host.contains(':') && !host.starts_with('[') {
        format!("[{host}]")
    } else {
        host.to_string()
    };
    format!("http://{host}:{port}")
}

/// Bypass user proxies for the private local service while bounding all network waits.
fn local_agent(timeout: Duration) -> ureq::Agent {
    ureq::AgentBuilder::new()
        .try_proxy_from_env(false)
        // ureq's total timeout does not bound connection establishment. On Windows
        // a refused first-start connection otherwise waits about two seconds.
        .timeout_connect(timeout)
        .timeout(timeout)
        .build()
}

/// Preserve server error details instead of publishing a failed request as an output.
fn http_failure(error: ureq::Error) -> anyhow::Error {
    match error {
        ureq::Error::Status(status, response) => anyhow::anyhow!(
            "Pandoc server conversion failed ({status}): {}",
            response.into_string().unwrap_or_default()
        ),
        error => error.into(),
    }
}

/// Record the serving process identity, including concurrently started or manual services.
fn save_server_state(
    path: &Path,
    host: &str,
    port: u16,
    version: &Value,
    fingerprint: &str,
) -> Result<()> {
    let pid: u32 = version["pid"]
        .as_u64()
        .and_then(|pid| pid.try_into().ok())
        .context("Service did not report a valid process identity")?;
    write_if_changed(
        &path.with_file_name("server-state.json"),
        &serde_json::to_vec(
            &json!({"host":host,"port":port,"pid":pid,"config_digest":fingerprint}),
        )?,
    )?;
    Ok(())
}

/// Hold the project startup lock until config publication and service handoff finish.
fn lock_service_startup(config_path: &Path) -> Result<File> {
    let lifecycle = OpenOptions::new()
        .create(true)
        .truncate(false)
        .read(true)
        .write(true)
        .open(config_path.with_file_name("server-start.lock"))?;
    lifecycle
        .lock()
        .context("Could not lock project service startup")?;
    Ok(lifecycle)
}

/// Reuse a matching service or start a console-free child with independent output handles.
fn ensure_server(
    config: &ServerConfig,
    config_path: &Path,
    host: &str,
    port: u16,
    override_command: Option<&str>,
) -> Result<()> {
    let mut executable = if override_command.is_none() {
        Some(crate::process::ServerExecutable::current()?)
    } else {
        None
    };
    let mut installed_executable = None;
    let agent = local_agent(Duration::from_millis(500));
    let version_url = format!("{}/version", base_url(host, port));
    let fingerprint = digest(&serde_json::to_vec(config)?);
    if let Ok(response) = agent.get(&version_url).call() {
        let version: Value = response.into_json()?;
        anyhow::ensure!(
            version.get("protocol").and_then(Value::as_str) == Some("pmt-html-v1"),
            "Port {port} belongs to another service"
        );
        anyhow::ensure!(
            version.get("project_dir").and_then(Value::as_str)
                == Some(display_path(&config.project_dir).as_str()),
            "Port {port} belongs to another Papper project"
        );
        let compatible = executable.as_ref().is_none_or(|executable| {
            version["runtime"] == "rust"
                && version["runtime_version"] == config.runtime_version
                && version["runtime_id"].as_str() == Some(executable.identity.as_str())
        });
        if !compatible {
            anyhow::ensure!(
                version["runtime"] == "rust",
                "A legacy Papper service is using port {port}; choose another --server-port for the native service"
            );
            // Prepare the replacement before stopping a working old service;
            // an unwritable managed directory must leave it available.
            installed_executable = Some(executable.as_mut().unwrap().install()?);
            eprintln!(
                "[Pandoc server] Replacing service runtime {} with {}",
                version["runtime_version"].as_str().unwrap_or("unknown"),
                config.runtime_version
            );
            shutdown_server(&config.project_dir, host, port, &version)?;
        } else if version.get("config_digest").and_then(Value::as_str) == Some(fingerprint.as_str())
        {
            save_server_state(config_path, host, port, &version, &fingerprint)?;
            return Ok(());
        } else {
            // A matching project can reload metadata policy without discarding native caches.
            if version.get("runtime").and_then(Value::as_str) == Some("rust") {
                local_agent(Duration::from_secs(10))
                    .post(&format!("{}/config", base_url(host, port)))
                    .send_json(config)
                    .map_err(http_failure)?;
                save_server_state(config_path, host, port, &version, &fingerprint)?;
                return Ok(());
            }
            bail!(
                "A legacy Papper service is using port {port}; choose another --server-port for the native service"
            );
        }
    }
    let mut command = if let Some(raw) = override_command {
        let words = papper_engine::split_command(raw)?;
        let mut command = Command::new(&words[0]);
        command.args(&words[1..]);
        command
    } else {
        let path = match installed_executable {
            Some(path) => path,
            None => executable.as_mut().unwrap().install()?,
        };
        let mut command = Command::new(path);
        command.arg("__server").arg("--config").arg(config_path);
        command
    };
    command
        .args(["--host", host, "--port", &port.to_string()])
        .current_dir(&config.project_dir)
        .stdin(Stdio::null());
    if let Some(executable) = executable.as_ref() {
        command.env(
            crate::process::SERVER_UPDATE_SOURCE_ENV,
            &executable.source_path,
        );
    }
    let log_path = config_path.with_file_name("server.log");
    let log = OpenOptions::new()
        .create(true)
        .append(true)
        .open(&log_path)?;
    command
        .stdout(Stdio::from(log.try_clone()?))
        .stderr(Stdio::from(log));
    #[cfg(windows)]
    {
        use std::os::windows::process::CommandExt;
        command.creation_flags(0x0800_0000 | 0x0000_0200);
    }
    let mut child = crate::process::spawn_background(&mut command)
        .context("Could not launch the native HTML service")?;
    let deadline = Instant::now() + Duration::from_secs(10);
    while Instant::now() < deadline {
        if let Ok(response) = agent.get(&version_url).call() {
            let version: Value = response.into_json()?;
            if version.get("config_digest").and_then(Value::as_str) == Some(fingerprint.as_str())
                && version["runtime"] == "rust"
                && version["protocol"] == "pmt-html-v1"
                && version["project_dir"].as_str()
                    == Some(display_path(&config.project_dir).as_str())
                && executable.as_ref().is_none_or(|executable| {
                    version["runtime_id"].as_str() == Some(executable.identity.as_str())
                        && version["runtime_version"] == config.runtime_version
                })
            {
                // Two initial clients may race to bind one port. The process
                // answering HTTP owns the service, not necessarily our child.
                if version["pid"].as_u64() != Some(u64::from(child.id())) {
                    let _ = child.kill();
                    let _ = child.wait();
                }
                save_server_state(config_path, host, port, &version, &fingerprint)?;
                return Ok(());
            }
        }
        // Another first-start client may win the bind while this child exits.
        // Its worker can still be initializing; keep checking the shared
        // identity until the original readiness deadline instead of failing.
        let _ = child.try_wait()?;
        thread::sleep(Duration::from_millis(50));
    }
    let _ = child.kill();
    let _ = child.wait();
    bail!(
        "Pandoc server did not become ready at {}; see {}",
        base_url(host, port),
        log_path.display()
    )
}

/// Serve the versioned project API with bounded bodies, queued conversions and cache memory.
pub fn run_server(config_path: &Path, host: &str, port: u16) -> Result<()> {
    run_server_with_options(config_path, host, port, false)
}

/// Serve the project API and optionally refresh bundled paths after a runtime handoff.
pub fn run_server_with_options(
    config_path: &Path,
    host: &str,
    port: u16,
    refresh_runtime: bool,
) -> Result<()> {
    let mut config: ServerConfig = serde_json::from_reader(File::open(config_path)?)?;
    if refresh_runtime {
        refresh_runtime_config(&mut config, config_path)?;
    }
    let listener = Server::http((host, port))
        .map_err(|error| anyhow::anyhow!("Cannot bind HTML server: {error}"))?;
    let mut conversion = ConversionState::new(config)?;
    let config_fingerprint = digest(&serde_json::to_vec(&conversion.config)?);
    save_server_state(
        config_path,
        host,
        port,
        &conversion.version(),
        &config_fingerprint,
    )?;
    let update_source =
        std::env::var_os(crate::process::SERVER_UPDATE_SOURCE_ENV).map(PathBuf::from);
    let (watcher, updates) = update_watcher(update_source.as_deref());
    let runtime_identity = conversion.runtime_id.clone();
    let state = Arc::new(HttpState {
        version: RwLock::new(conversion.version()),
        conversion: Mutex::new(conversion),
        shutdown: AtomicBool::new(false),
    });
    let active = Arc::new(AtomicUsize::new(0));
    eprintln!(
        "[Pandoc server] Native HTTP service listening on {}",
        base_url(host, port)
    );
    let mut restart = None;
    let mut pending_check = None;
    let mut last_check = Instant::now();
    while !state.shutdown.load(Ordering::Acquire) {
        if let Some(request) = listener.recv_timeout(Duration::from_millis(100))? {
            if active.fetch_add(1, Ordering::AcqRel) >= 32 {
                active.fetch_sub(1, Ordering::AcqRel);
                let _ = request.respond(json_response(
                    &json!({"error":"Conversion queue is full"}),
                    503,
                ));
                continue;
            }
            let state = Arc::clone(&state);
            let active = Arc::clone(&active);
            thread::spawn(move || {
                handle_request(request, &state);
                active.fetch_sub(1, Ordering::AcqRel);
            });
        }

        while update_source.is_some() {
            match updates.try_recv() {
                Ok(Ok(_)) => pending_check = Some(Instant::now() + Duration::from_millis(300)),
                Ok(Err(error)) => {
                    eprintln!("[Pandoc server] Update watcher error: {error}");
                    pending_check = Some(Instant::now() + Duration::from_secs(1));
                }
                Err(TryRecvError::Empty | TryRecvError::Disconnected) => break,
            }
        }
        if update_source.is_some()
            && (pending_check.is_some_and(|deadline| Instant::now() >= deadline)
                || last_check.elapsed() >= Duration::from_secs(30))
        {
            pending_check = None;
            last_check = Instant::now();
            if let Some(source) = update_source.as_deref() {
                match stable_executable(source) {
                    Ok(mut candidate) if candidate.identity != runtime_identity => {
                        match candidate.install() {
                            Ok(path) => {
                                eprintln!(
                                    "[Pandoc server] Papper installation changed; restarting with runtime {}",
                                    candidate.identity
                                );
                                restart = Some((path, candidate.identity));
                                break;
                            }
                            Err(error) => eprintln!(
                                "[Pandoc server] Could not stage updated runtime: {error:#}"
                            ),
                        }
                    }
                    Ok(_) => {}
                    Err(error) => {
                        eprintln!(
                            "[Pandoc server] Could not inspect Papper installation: {error:#}"
                        );
                        pending_check = Some(Instant::now() + Duration::from_secs(1));
                    }
                }
            }
        }
    }
    let deadline = Instant::now() + Duration::from_secs(10);
    while active.load(Ordering::Acquire) > 0 && Instant::now() < deadline {
        thread::sleep(Duration::from_millis(10));
    }
    if let Ok(mut conversion) = state.conversion.lock() {
        conversion.worker.close();
    }
    eprintln!("[Pandoc server] Native service stopped");
    drop(watcher);
    drop(listener);
    if let (Some((executable, identity)), Some(source)) = (restart, update_source) {
        restart_with_runtime(&executable, &identity, config_path, host, port, &source)?;
    }
    Ok(())
}

/// Watch the installed executable's directory so atomic replacement is observable.
fn update_watcher(
    source: Option<&Path>,
) -> (
    Option<RecommendedWatcher>,
    Receiver<notify::Result<notify::Event>>,
) {
    let (sender, receiver) = mpsc::channel();
    let Some(source) = source else {
        return (None, receiver);
    };
    let Some(parent) = source.parent() else {
        eprintln!("[Pandoc server] Cannot watch an installation path without a parent directory");
        return (None, receiver);
    };
    match notify::recommended_watcher(move |event| {
        let _ = sender.send(event);
    }) {
        Ok(mut watcher) => match watcher.watch(parent, RecursiveMode::NonRecursive) {
            Ok(()) => (Some(watcher), receiver),
            Err(error) => {
                eprintln!("[Pandoc server] Could not watch Papper installation: {error}");
                (None, receiver)
            }
        },
        Err(error) => {
            eprintln!("[Pandoc server] Could not create Papper update watcher: {error}");
            (None, receiver)
        }
    }
}

/// Require two identical reads so an in-place updater cannot publish a partial image.
fn stable_executable(path: &Path) -> Result<crate::process::ServerExecutable> {
    let first = crate::process::ServerExecutable::from_path(path)?;
    thread::sleep(Duration::from_millis(120));
    let second = crate::process::ServerExecutable::from_path(path)?;
    anyhow::ensure!(
        first.identity == second.identity,
        "Papper executable is still being updated"
    );
    Ok(second)
}

/// Refresh paths whose files came from the old executable's embedded resource archive.
fn refresh_runtime_config(config: &mut ServerConfig, config_path: &Path) -> Result<()> {
    let previous_root = config.resource_root.clone();
    let resources = ResourcePaths::discover()?;
    if let Some(previous_root) = previous_root {
        if let Some(index) = config
            .pandoc_args
            .iter()
            .position(|argument| argument == "--defaults")
            && let Some(defaults) = config.pandoc_args.get_mut(index + 1)
            && let Ok(relative) = Path::new(defaults).strip_prefix(&previous_root)
        {
            // Keep the CLI's path spelling on Windows; backslashes would change
            // the config digest and make an otherwise ready upgrade time out.
            *defaults = pandoc_path(&resources.root.join(relative));
        }
    }
    config.resource_root = Some(resources.root.clone());
    config.runtime_version = format!("{}-rust-v1", env!("CARGO_PKG_VERSION"));
    write_if_changed(config_path, &serde_json::to_vec_pretty(config)?)?;
    Ok(())
}

/// Start the replacement only after the old listener and its worker are released.
fn restart_with_runtime(
    executable: &Path,
    identity: &str,
    config_path: &Path,
    host: &str,
    port: u16,
    source: &Path,
) -> Result<()> {
    // The watcher competes with CLI upgrades too. Lock only after releasing
    // the old listener, so a CLI holding this lock can finish its shutdown.
    let _lifecycle = lock_service_startup(config_path)?;
    let config: ServerConfig = serde_json::from_reader(File::open(config_path)?)?;
    let version_url = format!("{}/version", base_url(host, port));
    if let Ok(response) = local_agent(Duration::from_millis(500))
        .get(&version_url)
        .call()
    {
        let version: Value = response.into_json()?;
        anyhow::ensure!(
            version["protocol"] == "pmt-html-v1"
                && version["runtime"] == "rust"
                && version["project_dir"].as_str()
                    == Some(display_path(&config.project_dir).as_str())
                && version["runtime_id"].as_str() == Some(identity),
            "Port {port} changed ownership before the runtime restart"
        );
        // A CLI completed the same upgrade while this watcher waited for the lock.
        eprintln!("[Pandoc server] Updated project service is already running; reusing it");
        return Ok(());
    }
    let log_path = config_path.with_file_name("server.log");
    let log = OpenOptions::new()
        .create(true)
        .append(true)
        .open(&log_path)?;
    let mut command = Command::new(executable);
    command
        .arg("__server")
        .arg("--config")
        .arg(config_path)
        .arg("--host")
        .arg(host)
        .arg("--port")
        .arg(port.to_string())
        .arg("--refresh-runtime")
        .current_dir(&config.project_dir)
        .env(crate::process::SERVER_UPDATE_SOURCE_ENV, source)
        .stdin(Stdio::null())
        .stdout(Stdio::from(log.try_clone()?))
        .stderr(Stdio::from(log));
    #[cfg(windows)]
    {
        use std::os::windows::process::CommandExt;
        command.creation_flags(0x0800_0000 | 0x0000_0200);
    }
    let mut child = crate::process::spawn_background(&mut command)
        .context("Could not start the upgraded Papper server")?;
    let deadline = Instant::now() + Duration::from_secs(10);
    while Instant::now() < deadline {
        if let Ok(response) = local_agent(Duration::from_millis(500))
            .get(&version_url)
            .call()
        {
            let version: Value = response.into_json()?;
            if version["runtime_id"].as_str() == Some(identity)
                && version["project_dir"].as_str()
                    == Some(display_path(&config.project_dir).as_str())
            {
                return Ok(());
            }
        }
        if let Some(status) = child.try_wait()? {
            anyhow::bail!(
                "Upgraded Papper server exited before becoming ready ({status}); see {}",
                log_path.display()
            );
        }
        thread::sleep(Duration::from_millis(50));
    }
    let _ = child.kill();
    let _ = child.wait();
    anyhow::bail!(
        "Upgraded Papper server did not become ready at {}; see {}",
        version_url,
        log_path.display()
    )
}

/// Construct one exact-length UTF-8 JSON response.
fn json_response(value: &Value, status: u16) -> Response<std::io::Cursor<Vec<u8>>> {
    Response::from_data(serde_json::to_vec(value).unwrap_or_default())
        .with_status_code(StatusCode(status))
        .with_header(Header::from_bytes("Content-Type", "application/json; charset=utf-8").unwrap())
}

/// Consume a request before responding and serialize conversion transactions on the worker.
fn handle_request(mut request: Request, shared: &HttpState) {
    let url = request.url().to_string();
    let method = request.method().clone();
    if method == Method::Get && url == "/version" {
        // CLI health checks must not time out merely because another editor/CLI
        // request is running. Refresh opportunistically; identity stays stable.
        if let Ok(mut state) = shared.conversion.try_lock()
            && let Ok(mut version) = shared.version.write()
        {
            *version = state.version();
        }
        let version = shared
            .version
            .read()
            .map(|version| version.clone())
            .unwrap_or_else(|_| json!({"error":"Service state unavailable"}));
        let _ = request.respond(json_response(&version, 200));
        return;
    }
    let mut state = match shared.conversion.lock() {
        Ok(state) => state,
        Err(_) => {
            let _ = request.respond(json_response(
                &json!({"error":"Service state unavailable"}),
                503,
            ));
            return;
        }
    };
    if shared.shutdown.load(Ordering::Acquire) {
        let _ = request.respond(json_response(&json!({"error":"Service is stopping"}), 503));
        return;
    }
    if method == Method::Get {
        let value = match url.as_str() {
            "/metrics" => state.metrics(),
            _ => {
                let _ = request.respond(json_response(&json!({"error":"Not found"}), 404));
                return;
            }
        };
        let _ = request.respond(json_response(&value, 200));
        return;
    }
    if method != Method::Post {
        let _ = request.respond(json_response(&json!({"error":"Method not allowed"}), 405));
        return;
    }
    let result = (|| -> Result<Value> {
        if request
            .body_length()
            .is_some_and(|length| length > 32 * 1024 * 1024)
        {
            bail!("Invalid request body length");
        }
        let mut bytes = Vec::new();
        request
            .as_reader()
            .take(32 * 1024 * 1024 + 1)
            .read_to_end(&mut bytes)?;
        anyhow::ensure!(
            bytes.len() <= 32 * 1024 * 1024,
            "Invalid request body length"
        );
        let payload: Value = serde_json::from_slice(&bytes)?;
        match url.as_str() {
            "/" | "/convert" | "/convert/raw" => {
                anyhow::ensure!(payload.is_object(), "Request must be a JSON object");
                state.convert(&payload)
            }
            "/batch" => {
                let inputs = payload.as_array().context("Batch must be a JSON array")?;
                let mut results = Vec::new();
                for input in inputs {
                    results.push(state.convert(input)?);
                }
                Ok(Value::Array(results))
            }
            "/config" => {
                let config: ServerConfig = serde_json::from_value(payload)?;
                anyhow::ensure!(
                    canonical_project(&config.project_dir)? == state.config.project_dir,
                    "Cannot change service project identity"
                );
                // Restart only when base conversion args change; metadata-only updates keep citation caches.
                if config.pandoc_args != state.config.pandoc_args
                    || config.resource_root != state.config.resource_root
                    || config.resource_paths != state.config.resource_paths
                {
                    *state = ConversionState::new(config)?;
                } else {
                    state.config = config;
                    state.metadata_cache.clear();
                    state.results.clear();
                    state.bytes = 0;
                }
                Ok(json!({"ok":true}))
            }
            "/shutdown" => {
                anyhow::ensure!(
                    payload["project_dir"].as_str()
                        == Some(display_path(&state.config.project_dir).as_str()),
                    "Shutdown project identity does not match"
                );
                // A startup client may have inspected a previous process on
                // this port. Reject its shutdown if a replacement now owns it.
                anyhow::ensure!(
                    payload
                        .get("pid")
                        .is_none_or(|pid| pid.as_u64() == Some(u64::from(std::process::id()))),
                    "Shutdown process identity does not match"
                );
                shared.shutdown.store(true, Ordering::Release);
                state.worker.close();
                Ok(json!({"ok":true}))
            }
            _ => bail!("Not found"),
        }
    })();
    if let Ok(mut version) = shared.version.write() {
        *version = state.version();
    }
    match result {
        Ok(value) if url == "/convert/raw" => {
            let html = value["output"].as_str().unwrap_or_default().to_string();
            let hit = value["cache_hit"].as_bool().unwrap_or(false);
            let citeproc = if hit {
                "skipped"
            } else if value["citeproc_cache_hit"] == true {
                "hit"
            } else {
                "miss"
            };
            let timing = value["timings"]
                .as_object()
                .unwrap()
                .iter()
                .map(|(name, value)| format!("{name};dur={:.3}", value.as_f64().unwrap_or(0.0)))
                .collect::<Vec<_>>()
                .join(", ");
            let mut response = Response::from_string(html).with_header(
                Header::from_bytes("Content-Type", "text/html; charset=utf-8").unwrap(),
            );
            for (name, content) in [
                ("X-PMT-Cache", if hit { "hit" } else { "miss" }),
                ("X-PMT-Citeproc-Cache", citeproc),
                ("X-PMT-Mode", value["mode"].as_str().unwrap_or("exact")),
                ("Server-Timing", &timing),
            ] {
                response = response.with_header(Header::from_bytes(name, content).unwrap());
            }
            let _ = request.respond(response);
        }
        Ok(value) => {
            let _ = request.respond(json_response(&value, 200));
        }
        Err(error) => {
            let known_route = matches!(
                url.as_str(),
                "/" | "/convert" | "/convert/raw" | "/batch" | "/config" | "/shutdown"
            );
            let _ = request.respond(json_response(
                &json!({"error":format!("{error:#}")}),
                if known_route { 400 } else { 404 },
            ));
        }
    }
}

/// Stop only the matching project service recorded by this native implementation.
pub fn stop_project_server(project: &Path) -> Result<()> {
    let state_file = project_work_dir(project)?.join("server-state.json");
    if !state_file.is_file() {
        return Ok(());
    }
    let saved: Value = serde_json::from_reader(File::open(&state_file)?)?;
    let host = saved["host"]
        .as_str()
        .context("Invalid saved server host")?;
    let port: u16 = saved["port"]
        .as_u64()
        .and_then(|port| port.try_into().ok())
        .context("Invalid saved server port")?;
    let address = base_url(host, port);
    let response = match local_agent(Duration::from_millis(500))
        .get(&format!("{address}/version"))
        .call()
    {
        Ok(response) => response,
        Err(ureq::Error::Transport(_)) => return Ok(()),
        Err(error) => return Err(http_failure(error)),
    };
    let version: Value = response.into_json()?;
    // PID plus project identity prevents deleting caches under an unrelated
    // service that has reused the old port after a crash or a reboot.
    if version["runtime"] != "rust"
        || version["pid"] != saved["pid"]
        || version["project_dir"].as_str() != Some(display_path(project).as_str())
    {
        return Ok(());
    }
    shutdown_server(project, host, port, &version)
}

/// Gracefully stop an inspected project process before reusing its port or work files.
fn shutdown_server(project: &Path, host: &str, port: u16, version: &Value) -> Result<()> {
    let address = base_url(host, port);
    local_agent(Duration::from_secs(120))
        .post(&format!("{address}/shutdown"))
        .send_json(json!({"project_dir":display_path(project),"pid":version["pid"]}))
        .map_err(http_failure)?;
    let deadline = Instant::now() + Duration::from_secs(5);
    while Instant::now() < deadline {
        match local_agent(Duration::from_millis(200))
            .get(&format!("{address}/version"))
            .call()
        {
            Err(ureq::Error::Transport(_)) => return Ok(()),
            Ok(response) => {
                let current: Value = response.into_json()?;
                anyhow::ensure!(
                    current["pid"] == version["pid"]
                        && current["project_dir"].as_str() == Some(display_path(project).as_str()),
                    "Port {port} changed ownership while stopping the project service"
                );
            }
            Err(error) => return Err(http_failure(error)),
        }
        thread::sleep(Duration::from_millis(50));
    }
    bail!("Project service did not stop; preserving its active working files")
}
