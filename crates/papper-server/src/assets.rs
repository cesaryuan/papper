//! Fingerprint conversion dependencies and cache conditionally validated citation downloads.

use crate::file_cache::FileCache;
use anyhow::{Context, Result, bail};
use papper_core::paths::{atomic_write, pandoc_path};
use regex::Regex;
use serde_json::{Map, Value, json};
use sha2::{Digest, Sha256};
use std::collections::{BTreeMap, BTreeSet, VecDeque};
use std::path::{Path, PathBuf};
use std::sync::LazyLock;
use std::time::{Duration, SystemTime, UNIX_EPOCH};

static PARTIALS: LazyLock<Regex> = LazyLock::new(|| {
    Regex::new(r"\$([\w./-]+)\([^)]*\)\$").expect("Bundled template partial pattern is valid")
});
static REFERENCES: LazyLock<Regex> = LazyLock::new(|| {
    Regex::new(r#"!?\[[^\]]*\]\((?:<([^>]+)>|([^\s)]+))|(?:src|href)=["']([^"']+)["']|(?m)^ {0,3}\[[^\]]+\]:\s*(?:<([^>]+)>|([^\s]+))"#).expect("Bundled Markdown resource pattern is valid")
});
// CSS is parsed only for authored stylesheets. Escaped CSS falls back to an
// uncached conversion because token escapes can hide imports from these patterns.
static CSS_COMMENTS: LazyLock<Regex> =
    LazyLock::new(|| Regex::new(r"(?s)/\*.*?\*/").expect("Bundled CSS comment pattern is valid"));
static CSS_REFERENCES: LazyLock<Regex> = LazyLock::new(|| {
    Regex::new(
        r#"(?i)(?:(@import)\s+(?:url\s*\(\s*)?|url\s*\(\s*)(?:"([^"]*)"|'([^']*)'|([^\s);]+))"#,
    )
    .expect("Bundled CSS resource pattern is valid")
});

const PATH_FIELDS: &[&str] = &[
    "template",
    "csl",
    "citation-style",
    "bibliography",
    "metadata-file",
    "metadata-files",
    "filters",
    "lua-filter",
    "include-in-header",
    "include-before-body",
    "include-after-body",
    "citation-abbreviations",
    "crossrefYaml",
    "defaults",
    "css",
];

/// Hash bytes with the same stable algorithm as the retained worker.
pub fn digest(bytes: &[u8]) -> String {
    Sha256::digest(bytes)
        .iter()
        .map(|byte| format!("{byte:02x}"))
        .collect()
}

/// Distinguish managed HTTP citation inputs from local paths.
fn is_remote(raw: &str) -> bool {
    raw.starts_with("http://") || raw.starts_with("https://")
}

/// Interpret embedding flags conservatively without treating explicit false as enabled.
fn embedding_enabled(value: Option<&Value>) -> bool {
    match value {
        Some(Value::Bool(enabled)) => *enabled,
        Some(Value::String(value)) => !matches!(
            value.trim().to_ascii_lowercase().as_str(),
            "" | "false" | "no" | "off" | "0"
        ),
        Some(Value::Number(value)) => value.as_f64().is_some_and(|value| value != 0.0),
        Some(Value::Array(values)) => !values.is_empty(),
        Some(Value::Object(values)) => !values.is_empty(),
        Some(Value::Null) | None => false,
    }
}

/// Track local and downloaded assets consumed by a project conversion.
pub struct AssetCache {
    project: PathBuf,
    roots: Vec<PathBuf>,
    args: Vec<String>,
    remote: RemoteAssets,
    pub remote_paths: BTreeMap<String, String>,
    pub cacheable: bool,
    pub asset_count: usize,
    paths: BTreeSet<PathBuf>,
    files: FileCache,
    graph: Option<Graph>,
    remote_kinds: BTreeMap<String, String>,
}

/// Retain a parsed graph while validating all selected and absent candidates on reuse.
struct Graph {
    key: String,
    fingerprint: String,
    digests: BTreeMap<PathBuf, Option<String>>,
    remote_paths: BTreeMap<String, String>,
    remote_kinds: BTreeMap<String, String>,
}

impl AssetCache {
    /// Match dependency resolution to the current request's actual Pandoc roots.
    pub fn resource_roots(&mut self, roots: &[PathBuf]) {
        self.roots = roots.to_vec();
    }
    /// Initialize isolated citation storage and explicit Pandoc resource roots.
    pub fn new(
        project: PathBuf,
        roots: Vec<PathBuf>,
        args: Vec<String>,
        remote_dir: PathBuf,
    ) -> Self {
        Self {
            project,
            roots,
            args,
            remote: RemoteAssets::new(remote_dir),
            remote_paths: BTreeMap::new(),
            cacheable: true,
            asset_count: 0,
            paths: BTreeSet::new(),
            files: FileCache::default(),
            graph: None,
            remote_kinds: BTreeMap::new(),
        }
    }

    /// Resolve a resource while tracking absent higher-priority candidates.
    fn resolve(
        &mut self,
        raw: &str,
        base: &Path,
        paths: &mut BTreeSet<PathBuf>,
        kind: &str,
    ) -> Result<Option<PathBuf>> {
        let raw = raw.trim().replace("${.}", &pandoc_path(base));
        if raw.is_empty() || raw == "citeproc" {
            return Ok(None);
        }
        if is_remote(&raw) {
            if matches!(
                kind.trim_start_matches('-'),
                "csl" | "citation-style" | "bibliography" | "citation-abbreviations"
            ) {
                let path = self.remote.ensure(&raw, kind)?;
                self.remote_kinds.insert(raw.clone(), kind.to_string());
                self.remote_paths.insert(raw, pandoc_path(&path));
                paths.insert(path.clone());
                return Ok(Some(path));
            }
            self.cacheable = false;
            return Ok(None);
        }
        if raw.starts_with("data:") {
            return Ok(None);
        }
        let path = PathBuf::from(&raw);
        let mut candidates = if path.is_absolute() {
            vec![path]
        } else {
            std::iter::once(base.join(&path))
                .chain(self.roots.iter().map(|root| root.join(&path)))
                .collect()
        };
        if matches!(kind.trim_start_matches('-'), "filters" | "filter")
            && !raw.contains(['/', '\\'])
            && let Some(system_path) = locate_on_path(&raw)
        {
            candidates.insert(0, system_path);
        }
        let fallback = candidates.first().cloned();
        for candidate in candidates {
            paths.insert(candidate.clone());
            if candidate.is_file() {
                return Ok(Some(candidate));
            }
        }
        Ok(fallback)
    }

    /// Add nested metadata paths and recursively expand defaults, templates and CSL parents.
    fn add_value(
        &mut self,
        value: &Value,
        base: &Path,
        kind: &str,
        paths: &mut BTreeSet<PathBuf>,
        queue: &mut VecDeque<(PathBuf, String)>,
    ) -> Result<()> {
        if let Some(items) = value.as_array() {
            for item in items {
                self.add_value(item, base, kind, paths, queue)?;
            }
        } else if let Some(object) = value.as_object() {
            if let Some(path) = object.get("path") {
                self.add_value(path, base, kind, paths, queue)?;
            }
        } else if let Some(raw) = value.as_str()
            && let Some(path) = self.resolve(raw, base, paths, kind)?
            && matches!(
                kind.trim_start_matches('-'),
                "defaults"
                    | "metadata-file"
                    | "metadata-files"
                    | "template"
                    | "csl"
                    | "citation-style"
                    | "css"
            )
        {
            queue.push_back((path, kind.to_string()));
        }
        Ok(())
    }

    /// Fingerprint complete dependencies, including template partials and resource creation/deletion.
    pub fn fingerprint(
        &mut self,
        metadata: &Map<String, Value>,
        source: &Path,
        text: &str,
        extra: &[PathBuf],
    ) -> Result<String> {
        self.files.begin_request();
        self.cacheable = true;
        let references: Vec<String> = REFERENCES
            .captures_iter(text)
            .filter_map(|capture| {
                capture
                    .iter()
                    .skip(1)
                    .flatten()
                    .next()
                    .map(|item| item.as_str().to_string())
            })
            .collect();
        // Body prose can change without changing the graph. Keys still include
        // resolution context and every path-bearing reference; absent preferred
        // candidates are validated too, so later file creation changes output.
        let key = digest(&serde_json::to_vec(&(
            metadata,
            &self.roots,
            &self.args,
            source,
            extra,
            &references,
            std::env::var_os("PATH"),
            std::env::var_os("USERPROFILE"),
            std::env::var_os("HOME"),
        ))?);
        if let Some(graph) = self.graph.take() {
            let mut unchanged = graph.key == key;
            if unchanged {
                // Keep network validation TTL/ETag behavior active on graph hits;
                // changed downloaded CSL parents force complete graph expansion.
                for (url, kind) in &graph.remote_kinds {
                    self.remote.ensure(url, kind)?;
                }
                for (path, expected) in &graph.digests {
                    let current = self.files.digest(path).ok();
                    if &current != expected {
                        unchanged = false;
                    }
                    if current.is_none()
                        && crate::file_cache::stamp(path) != Some(crate::file_cache::Stamp::Missing)
                    {
                        unchanged = false;
                    }
                }
            }
            if unchanged {
                self.paths = graph.digests.keys().cloned().collect();
                self.asset_count = self.paths.len();
                self.remote_paths = graph.remote_paths.clone();
                self.remote_kinds = graph.remote_kinds.clone();
                let fingerprint = graph.fingerprint.clone();
                self.graph = Some(graph);
                return Ok(fingerprint);
            }
        }
        self.remote_paths.clear();
        self.remote_kinds.clear();
        let mut paths: BTreeSet<PathBuf> = extra.iter().cloned().collect();
        let mut queue = VecDeque::new();
        let project = self.project.clone();
        let args = self.args.clone();
        let mut embed_resources = ["embed-resources", "self-contained"]
            .iter()
            .any(|field| embedding_enabled(metadata.get(*field)));
        let mut index = 0;
        while index < args.len() {
            let (option, inline) = args[index]
                .split_once('=')
                .map_or((args[index].as_str(), None), |(a, b)| (a, Some(b)));
            let kind = option.trim_start_matches('-');
            if matches!(kind, "embed-resources" | "self-contained") {
                embed_resources |=
                    inline.is_none_or(|value| embedding_enabled(Some(&json!(value))));
            }
            if PATH_FIELDS.contains(&kind) || matches!(kind, "filter" | "lua-filter") {
                let raw = if let Some(raw) = inline {
                    Some(raw.to_string())
                } else {
                    index += 1;
                    args.get(index).cloned()
                };
                if let Some(raw) = raw {
                    self.add_value(
                        &Value::String(raw),
                        &project,
                        option,
                        &mut paths,
                        &mut queue,
                    )?;
                }
            }
            index += 1;
        }
        for key in PATH_FIELDS {
            if let Some(value) = metadata.get(*key) {
                self.add_value(value, &project, key, &mut paths, &mut queue)?;
            }
        }
        self.add_value(
            metadata
                .get("crossrefYaml")
                .unwrap_or(&json!("pandoc-crossref.yaml")),
            &project,
            "crossrefYaml",
            &mut paths,
            &mut queue,
        )?;
        if let Some(home) = std::env::var_os("USERPROFILE").or_else(|| std::env::var_os("HOME")) {
            paths.insert(PathBuf::from(&home).join(".pandoc-crossref/config.yaml"));
            paths.insert(PathBuf::from(home).join(".pandoc-crossref/config-html.yaml"));
        }
        let mut expanded = BTreeSet::new();
        let mut data_dirs = Vec::new();
        while let Some((path, kind)) = queue.pop_front() {
            if !expanded.insert((path.clone(), kind.clone())) {
                continue;
            }
            let Ok((bytes, _)) = self.files.read(&path) else {
                continue;
            };
            let Ok(contents) = std::str::from_utf8(&bytes) else {
                // Non-UTF-8 stylesheets can contain imports we cannot safely
                // fingerprint. Retain their bytes but do not reuse rendered HTML.
                if kind.trim_start_matches('-') == "css" {
                    self.cacheable = false;
                }
                continue;
            };
            let base = path.parent().unwrap_or(&project);
            match kind.trim_start_matches('-') {
                "css" => {
                    if contents.contains('\\') {
                        self.cacheable = false;
                        continue;
                    }
                    let stylesheet = CSS_COMMENTS.replace_all(contents, "");
                    for capture in CSS_REFERENCES.captures_iter(&stylesheet) {
                        let Some(raw) = capture.iter().skip(2).flatten().next() else {
                            continue;
                        };
                        let raw = raw.as_str().trim();
                        let lower = raw.to_ascii_lowercase();
                        if raw.is_empty() || raw.starts_with('#') || lower.starts_with("data:") {
                            continue;
                        }
                        // Embedded remote CSS/images/fonts have no conditional
                        // validation here. Re-render rather than pinning stale bytes.
                        if lower.contains("://") || raw.starts_with("//") {
                            self.cacheable = false;
                            continue;
                        }
                        let decoded = decode_url_path(raw.split(['#', '?']).next().unwrap_or(raw));
                        let dependency_kind = if capture.get(1).is_some() {
                            "css"
                        } else {
                            "css-resource"
                        };
                        self.add_value(
                            &json!(decoded),
                            base,
                            dependency_kind,
                            &mut paths,
                            &mut queue,
                        )?;
                    }
                }
                "template" => {
                    for capture in PARTIALS.captures_iter(contents) {
                        let raw = &capture[1];
                        let name = if Path::new(raw).extension().is_none() {
                            format!(
                                "{raw}.{}",
                                path.extension().unwrap_or_default().to_string_lossy()
                            )
                        } else {
                            raw.to_string()
                        };
                        self.add_value(&json!(name), base, "template", &mut paths, &mut queue)?;
                    }
                }
                "csl" | "citation-style" => {
                    if let Ok(document) = roxmltree::Document::parse(contents) {
                        for node in document.descendants().filter(|n| {
                            n.has_tag_name(("http://purl.org/net/xbiblio/csl", "link"))
                                && n.attribute("rel") == Some("independent-parent")
                        }) {
                            if let Some(parent) = node.attribute("href") {
                                let basename = parent.rsplit('/').next().unwrap_or("parent");
                                let basename = if basename.contains('.') {
                                    basename.to_string()
                                } else {
                                    format!("{basename}.csl")
                                };
                                let roots: Vec<_> = self
                                    .roots
                                    .iter()
                                    .cloned()
                                    .chain(data_dirs.iter().flat_map(|root: &PathBuf| {
                                        [root.join("csl"), root.join("csl/dependent")]
                                    }))
                                    .collect();
                                let local = roots
                                    .iter()
                                    .map(|root| root.join(&basename))
                                    .find(|candidate| candidate.is_file());
                                for candidate in roots.iter().map(|root| root.join(&basename)) {
                                    paths.insert(candidate);
                                }
                                let selected = if let Some(local) = local {
                                    local
                                } else {
                                    self.remote_kinds
                                        .insert(parent.to_string(), "csl".to_string());
                                    self.remote.ensure(parent, "csl")?
                                };
                                self.remote_paths
                                    .insert(parent.to_string(), pandoc_path(&selected));
                                paths.insert(selected.clone());
                                queue.push_back((selected, "csl".to_string()));
                            }
                        }
                    }
                }
                _ => {
                    let Ok(value) = serde_yaml::from_str::<Value>(contents) else {
                        continue;
                    };
                    for field in ["embed-resources", "self-contained"] {
                        embed_resources |= embedding_enabled(value.get(field))
                            || embedding_enabled(
                                value
                                    .get("metadata")
                                    .and_then(|metadata| metadata.get(field)),
                            );
                    }
                    if let Some(data) = value.get("data-dir").and_then(Value::as_str) {
                        let raw = data.replace("${.}", &pandoc_path(base));
                        data_dirs.push(if Path::new(&raw).is_absolute() {
                            PathBuf::from(raw)
                        } else {
                            base.join(raw)
                        });
                    }
                    let value_base = if kind.trim_start_matches('-') == "defaults" {
                        base
                    } else {
                        &project
                    };
                    for key in PATH_FIELDS {
                        if let Some(item) = value.get(*key) {
                            self.add_value(item, value_base, key, &mut paths, &mut queue)?;
                        }
                        if let Some(item) = value.get("metadata").and_then(|m| m.get(*key)) {
                            self.add_value(item, &project, key, &mut paths, &mut queue)?;
                        }
                    }
                }
            }
        }
        let roots = self.roots.clone();
        for reference in references {
            let raw = reference.as_str();
            if raw.starts_with(['#']) || raw.starts_with("data:") || raw.starts_with("mailto:") {
                continue;
            }
            if is_remote(raw) || raw.starts_with("//") {
                // Ordinary HTML retains the URL, so unchanged documents remain
                // cacheable. Embedded images/scripts include remote bytes whose
                // freshness is not covered by the managed citation downloader.
                if embed_resources {
                    self.cacheable = false;
                }
                continue;
            }
            let clean = raw.split(['#', '?']).next().unwrap_or(raw);
            let decoded = decode_url_path(clean);
            let base = roots
                .first()
                .map(PathBuf::as_path)
                .unwrap_or_else(|| source.parent().unwrap_or(&project));
            self.resolve(&decoded, base, &mut paths, "image")?;
        }
        self.asset_count = paths.len();
        self.paths = paths.clone();
        let mut hash = Sha256::new();
        hash.update(serde_json::to_vec(metadata)?);
        let mut digests = BTreeMap::new();
        for path in paths {
            hash.update(pandoc_path(&path));
            // Reuse bytes only with stable OS identity/change time, never just
            // mtime and size; restored timestamps still invalidate real edits.
            let value = self.files.digest(&path).ok();
            if value.is_none()
                && crate::file_cache::stamp(&path) != Some(crate::file_cache::Stamp::Missing)
            {
                self.cacheable = false;
            }
            hash.update(value.as_deref().unwrap_or("<missing>"));
            digests.insert(path, value);
        }
        let fingerprint = hash
            .finalize()
            .iter()
            .map(|byte| format!("{byte:02x}"))
            .collect::<String>();
        if self.cacheable {
            self.graph = Some(Graph {
                key,
                fingerprint: fingerprint.clone(),
                digests,
                remote_paths: self.remote_paths.clone(),
                remote_kinds: self.remote_kinds.clone(),
            });
        }
        Ok(fingerprint)
    }

    /// Reuse the existing audited Lua batching optimization without changing custom filters.
    pub fn lua_bundle(&self, work: &Path) -> Result<Option<(PathBuf, Vec<PathBuf>)>> {
        let expected = [
            (
                "normalize_chinese_numbering",
                "0d2278ae300e7d4452cff58b3cafb3f3aaf3fc7bd271014afe3db1e92511e242",
            ),
            (
                "merge_table_cells",
                "98265b652438b1f452c20e8f35eeba5bf526bed38aabd48192863dc433c08311",
            ),
            (
                "paragraph_custom_styles",
                "cd84cbd11d7663285b06e7698de320306cf8d2145c37f06b8dbefe1fa5be10c0",
            ),
            (
                "subfigure_layout_styles",
                "ef52f360ffcab970b384e6f52105d56d61e64cd14bb6b398f4936f3481b53ea8",
            ),
            (
                "revision_table_styles",
                "c7e95fe19e55d488ab3dd7e9e977edb9f276f61a7a7d74c47b4dc7c81fd9c53b",
            ),
        ];
        let mut scripts = Vec::new();
        for (name, expected_hash) in expected {
            let Some(path) = self.paths.iter().find(|path| {
                path.file_stem().and_then(|s| s.to_str()) == Some(name)
                    && path.extension().and_then(|s| s.to_str()) == Some("lua")
            }) else {
                return Ok(None);
            };
            let bytes = std::fs::read(path)?;
            let normalized = String::from_utf8(bytes)?.replace("\r\n", "\n");
            if digest(normalized.as_bytes()) != expected_hash {
                return Ok(None);
            }
            scripts.push(path.clone());
        }
        // Each script keeps its original _ENV and PANDOC_SCRIPT_FILE. Custom or
        // edited filters fall back to the independent Pandoc execution path.
        let literals = scripts
            .iter()
            .map(|path| serde_json::to_string(&pandoc_path(path)))
            .collect::<std::result::Result<Vec<_>, _>>()?
            .join(", ");
        let text = format!(
            "-- Apply audited Papper HTML filters in order within one Lua state\nlocal paths = {{{literals}}}\nlocal filters = {{}}\nfor _, path in ipairs(paths) do\n  local env = setmetatable({{PANDOC_SCRIPT_FILE = path}}, {{__index = _G}})\n  assert(loadfile(path, 't', env))()\n  filters[#filters + 1] = env\nend\n-- Walk fresh prose through each script's own callback table\nlocal function apply_filters(document)\n  for _, filter in ipairs(filters) do document = document:walk(filter) end\n  return document\nend\nreturn {{{{Pandoc = apply_filters}}}}\n"
        );
        let target = work.join("papper-html-filter-bundle.lua");
        papper_core::paths::write_if_changed(&target, text.as_bytes())?;
        Ok(Some((target, scripts)))
    }
}

/// Locate custom executable filters through the effective process PATH.
fn locate_on_path(name: &str) -> Option<PathBuf> {
    std::env::split_paths(&std::env::var_os("PATH")?)
        .flat_map(|root| {
            if cfg!(windows) {
                vec![root.join(name), root.join(format!("{name}.exe"))]
            } else {
                vec![root.join(name)]
            }
        })
        .find(|path| path.is_file())
}

/// Decode percent escapes in Markdown image paths without treating plus as a space.
fn decode_url_path(raw: &str) -> String {
    let bytes = raw.as_bytes();
    let mut out = Vec::with_capacity(bytes.len());
    let mut index = 0;
    while index < bytes.len() {
        if bytes[index] == b'%'
            && index + 2 < bytes.len()
            && let Some(value) = std::str::from_utf8(&bytes[index + 1..index + 3])
                .ok()
                .and_then(|digits| u8::from_str_radix(digits, 16).ok())
        {
            out.push(value);
            index += 3;
            continue;
        }
        out.push(bytes[index]);
        index += 1;
    }
    String::from_utf8_lossy(&out).into_owned()
}

/// Keep downloaded citation assets and validators between service requests and restarts.
struct RemoteAssets {
    directory: PathBuf,
    agent: ureq::Agent,
    local_agent: ureq::Agent,
}

impl RemoteAssets {
    /// Build a lazy HTTP client; TLS is initialized only for actual HTTPS downloads.
    fn new(directory: PathBuf) -> Self {
        Self {
            directory,
            agent: ureq::AgentBuilder::new()
                .try_proxy_from_env(true)
                .timeout(Duration::from_secs(15))
                .build(),
            local_agent: ureq::AgentBuilder::new()
                .try_proxy_from_env(false)
                .timeout(Duration::from_secs(15))
                .build(),
        }
    }

    /// Fetch expired citation assets conditionally and publish only validated content.
    fn ensure(&mut self, url: &str, kind: &str) -> Result<PathBuf> {
        let directory = self.directory.join(digest(url.as_bytes()));
        let record_path = directory.join("metadata.json");
        let mut record: Value = std::fs::read(&record_path)
            .ok()
            .and_then(|bytes| serde_json::from_slice(&bytes).ok())
            .unwrap_or_else(|| json!({}));
        let name = url
            .split('?')
            .next()
            .unwrap_or(url)
            .rsplit('/')
            .next()
            .filter(|s| !s.is_empty())
            .unwrap_or("citation-asset");
        let filename = if record.get("url").and_then(Value::as_str) == Some(url) {
            record
                .get("filename")
                .and_then(Value::as_str)
                .filter(|name| {
                    Path::new(name).file_name().and_then(|s| s.to_str()) == Some(*name)
                        && !name.is_empty()
                })
                .unwrap_or(name)
                .to_string()
        } else {
            name.to_string()
        };
        let kind = kind.trim_start_matches('-');
        let filename = if matches!(kind, "csl" | "citation-style")
            && !filename.ends_with(".csl")
            && !filename.ends_with(".xml")
        {
            format!("{filename}.csl")
        } else if kind == "citation-abbreviations" && !filename.ends_with(".json") {
            format!("{filename}.json")
        } else {
            filename
        };
        let mut path = directory.join(&filename);
        let now = SystemTime::now().duration_since(UNIX_EPOCH)?.as_secs_f64();
        let age = now
            - record
                .get("checked_at")
                .and_then(Value::as_f64)
                .unwrap_or(0.0);
        if path.is_file() && (0.0..300.0).contains(&age) {
            return Ok(path);
        }
        let authority = url
            .split("://")
            .nth(1)
            .unwrap_or("")
            .split('/')
            .next()
            .unwrap_or("");
        let local = authority == "localhost"
            || authority.starts_with("localhost:")
            || authority == "127.0.0.1"
            || authority.starts_with("127.0.0.1:")
            || authority.starts_with("[::1]");
        // Loopback assets must bypass proxy settings just like the local build service.
        let mut request = if local {
            &self.local_agent
        } else {
            &self.agent
        }
        .get(url)
        .set("User-Agent", "pandoc/3.12");
        if path.is_file() {
            if let Some(etag) = record.get("etag").and_then(Value::as_str) {
                request = request.set("If-None-Match", etag);
            }
            if let Some(date) = record.get("last_modified").and_then(Value::as_str) {
                request = request.set("If-Modified-Since", date);
            }
        }
        let download = (|| -> Result<()> {
            let response = match request.call() {
                Ok(response) => response,
                Err(ureq::Error::Status(304, _)) if path.is_file() => {
                    record["checked_at"] = json!(now);
                    return Ok(());
                }
                Err(error) => return Err(error.into()),
            };
            if response.status() == 304 && path.is_file() {
                record["checked_at"] = json!(now);
                return Ok(());
            }
            let etag = response.header("ETag").map(str::to_string);
            let last_modified = response.header("Last-Modified").map(str::to_string);
            let content_type = response.header("Content-Type").unwrap_or("").to_string();
            let resolved = response.get_url().to_string();
            let mut bytes = Vec::new();
            use std::io::Read;
            response
                .into_reader()
                .take(32 * 1024 * 1024 + 1)
                .read_to_end(&mut bytes)?;
            anyhow::ensure!(
                bytes.len() <= 32 * 1024 * 1024,
                "Remote citation asset exceeds 32 MiB: {url}"
            );
            if matches!(kind, "csl" | "citation-style") {
                let xml = std::str::from_utf8(&bytes)?;
                let document = roxmltree::Document::parse(xml)?;
                anyhow::ensure!(
                    document
                        .root_element()
                        .has_tag_name(("http://purl.org/net/xbiblio/csl", "style")),
                    "Remote CSL is not a CSL style: {url}"
                );
            }
            if kind == "citation-abbreviations" {
                serde_json::from_slice::<Value>(&bytes)?;
            }
            if kind == "bibliography" && path.extension().is_none() {
                let extension = if content_type.contains("json") {
                    "json"
                } else if content_type.contains("yaml") {
                    "yaml"
                } else if content_type.contains("ris") {
                    "ris"
                } else {
                    "bib"
                };
                path.set_extension(extension);
            }
            atomic_write(&path, &bytes)?;
            record = json!({"url":url,"filename":path.file_name().unwrap().to_string_lossy(),"checked_at":now,"etag":etag,"last_modified":last_modified,"resolved_url":resolved});
            Ok(())
        })();
        if let Err(error) = download {
            if !path.is_file() {
                bail!("Could not cache remote {kind}: {url}: {error}");
            }
            eprintln!("[WARN] Remote validation failed; using cached {kind}: {url}: {error}");
            record["checked_at"] = json!(now - 270.0);
        }
        atomic_write(&record_path, &serde_json::to_vec(&record)?)
            .context("Could not save remote citation validators")?;
        Ok(path)
    }
}
