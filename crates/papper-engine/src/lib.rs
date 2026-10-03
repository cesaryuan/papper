//! Run the retained Pandoc CLI and persistent Haskell worker without Python.

use anyhow::{Context, Result, bail};
use serde::{Deserialize, Serialize};
use std::collections::BTreeMap;
use std::env;
use std::ffi::{OsStr, OsString};
use std::fs::{self, File, OpenOptions};
use std::io::{BufRead, BufReader, Read, Seek, SeekFrom, Write};
use std::path::{Path, PathBuf};
use std::process::{Child, ChildStdin, Command, Output, Stdio};
use std::sync::mpsc::{self, Receiver};
use std::thread::{self, JoinHandle};
use std::time::{Duration, SystemTime};

/// Selected conversion executable and its retained embedded-filter capability.
#[derive(Clone, Debug)]
pub struct EngineLocation {
    pub executable: PathBuf,
    pub embedded_crossref: bool,
    pub source: String,
}

/// Locate the bundled/shared engine before considering ordinary Pandoc installs.
pub fn discover_engine(resource_root: &Path) -> Result<EngineLocation> {
    let worker_name = executable_name("pmt-pandoc-worker");
    let bundled = [
        resource_root.join("bin").join(&worker_name),
        resource_root
            .join("src/pandoc_manuscript/bin")
            .join(&worker_name),
    ];
    for candidate in bundled {
        if candidate.is_file() {
            return Ok(engine_location(candidate, true, "bundled"));
        }
    }
    let source_runtime = resource_root.join(".pmt/pandoc-worker");
    let selected = source_runtime.join("current.json");
    if selected.is_file() {
        // Source builds publish immutable copies, keeping running services from
        // locking Cabal's mutable linker output. The identity is computed at build time.
        let record: serde_json::Value = serde_json::from_slice(&fs::read(selected)?)?;
        let relative = record["executable"]
            .as_str()
            .context("Native Worker path missing")?;
        let candidate = source_runtime.join(relative);
        anyhow::ensure!(
            candidate.is_file(),
            "Prepared native Worker missing: {}",
            candidate.display()
        );
        return Ok(engine_location(candidate, true, "native build"));
    }
    let source_build = resource_root.join("scripts/pandoc-server/dist-newstyle/build");
    let mut builds = Vec::new();
    collect_native_builds(&source_build, &worker_name, 9, &mut builds);
    if let Some((_, candidate)) = builds.into_iter().max_by_key(|(modified, _)| *modified) {
        return Ok(engine_location(candidate, true, "native build"));
    }
    let managed = tools_bin_dir()?.join(&worker_name);
    if managed.is_file() {
        return Ok(engine_location(managed, true, "~/.papper/tools"));
    }
    // Isolated native service state can still reuse a previously installed engine.
    // An alternate PAPPER_HOME must not hide the retained legacy tool installation.
    if let Some(home) = env::var_os("USERPROFILE").or_else(|| env::var_os("HOME")) {
        let legacy = PathBuf::from(home)
            .join(".papper/tools/bin")
            .join(&worker_name);
        if legacy.is_file() {
            return Ok(engine_location(legacy, true, "legacy managed tools"));
        }
    }
    if let Some(candidate) = find_on_path("pandoc") {
        return Ok(engine_location(candidate, false, "PATH"));
    }
    let managed = tools_bin_dir()?.join(executable_name("pandoc"));
    if managed.is_file() {
        return Ok(engine_location(managed, false, "~/.papper/tools"));
    }
    bail!(
        "Papper Pandoc engine not found; install a platform wheel, build scripts/pandoc-server, \
         or install pmt-pandoc-worker into ~/.papper/tools/bin"
    )
}

/// Preserve the selected executable path without resolving runtime resource roots.
fn engine_location(executable: PathBuf, embedded_crossref: bool, source: &str) -> EngineLocation {
    EngineLocation {
        executable,
        embedded_crossref,
        source: source.into(),
    }
}

/// Enumerate bounded Cabal build directories while avoiding recursive symlinks.
fn collect_native_builds(
    root: &Path,
    name: &OsStr,
    depth: usize,
    found: &mut Vec<(SystemTime, PathBuf)>,
) {
    if depth == 0 {
        return;
    }
    let Ok(entries) = fs::read_dir(root) else {
        return;
    };
    for entry in entries.flatten() {
        let Ok(kind) = entry.file_type() else {
            continue;
        };
        if kind.is_dir() {
            collect_native_builds(&entry.path(), name, depth - 1, found);
        } else if kind.is_file()
            && entry.file_name() == name
            && let Ok(modified) = entry.metadata().and_then(|metadata| metadata.modified())
        {
            found.push((modified, entry.path()));
        }
    }
}

/// Return the platform-specific command filename.
fn executable_name(name: &str) -> OsString {
    if cfg!(windows) {
        format!("{name}.exe").into()
    } else {
        name.into()
    }
}

/// Resolve Papper's managed tools from the user's home without importing Python.
pub fn tools_bin_dir() -> Result<PathBuf> {
    Ok(papper_core::paths::tools_bin_dir())
}

/// Find a native executable on PATH without invoking a shell or wrapper parser.
pub fn find_on_path(command: &str) -> Option<PathBuf> {
    let paths = env::var_os("PATH")?;
    for directory in env::split_paths(&paths) {
        let candidate = directory.join(executable_name(command));
        if candidate.is_file() {
            return Some(candidate);
        }
    }
    None
}

/// Run the shared Pandoc CLI with the requested filter environment and cwd.
pub struct PandocCli {
    location: EngineLocation,
}

impl PandocCli {
    /// Reuse a resolved engine for successive conversions and diagnostics.
    pub fn new(location: EngineLocation) -> Self {
        Self { location }
    }

    /// Expose whether standard crossref is executed by the shared engine itself.
    pub fn location(&self) -> &EngineLocation {
        &self.location
    }

    /// Capture conversion output and report native failures with their stderr.
    pub fn run(
        &self,
        arguments: &[OsString],
        working_dir: &Path,
        environment: &BTreeMap<String, Option<String>>,
    ) -> Result<Output> {
        let mut command = Command::new(&self.location.executable);
        command.args(arguments).current_dir(working_dir);
        configure_environment(&mut command, environment)?;
        hide_console(&mut command);
        let output = command.output().with_context(|| {
            format!(
                "Cannot launch Pandoc: {}",
                self.location.executable.display()
            )
        })?;
        if !output.status.success() {
            bail!(
                "Pandoc failed ({}): {}",
                output.status,
                String::from_utf8_lossy(&output.stderr).trim()
            );
        }
        Ok(output)
    }
}

/// Immutable configuration consumed by the retained Haskell worker on startup.
#[derive(Clone, Debug, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct WorkerConfig {
    pub pandoc_args: Vec<String>,
    pub project_dir: PathBuf,
    #[serde(default)]
    pub work_dirs: Vec<PathBuf>,
}

/// One private JSON-line conversion; omitted fields retain Pandoc defaults.
#[derive(Clone, Debug, Default, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct WorkerRequest {
    pub input: PathBuf,
    pub output: PathBuf,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub mode: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub input_format: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub asset_fingerprint: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub metadata_file: Option<PathBuf>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub resource_paths: Option<Vec<PathBuf>>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub filter_environment: Option<BTreeMap<String, Option<String>>>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub lua_bundle: Option<PathBuf>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub lua_bundle_paths: Option<Vec<PathBuf>>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub remote_resources: Option<BTreeMap<String, PathBuf>>,
}

/// Worker conversion status and its native filter/citation cache measurements.
#[derive(Clone, Debug, Default, Serialize, Deserialize)]
pub struct WorkerResponse {
    pub ok: bool,
    #[serde(default)]
    pub error: Option<String>,
    #[serde(default)]
    pub elapsed_ms: u64,
    #[serde(default)]
    pub citeproc_cache_hit: bool,
    #[serde(default)]
    pub filters_ms: BTreeMap<String, f64>,
}

/// Own a single serialized Haskell process and restart it after native exits.
pub struct PersistentWorker {
    location: EngineLocation,
    config: WorkerConfig,
    config_path: PathBuf,
    log_path: PathBuf,
    process: Option<Child>,
    input: Option<ChildStdin>,
    responses: Option<Receiver<std::result::Result<String, String>>>,
    reader: Option<JoinHandle<()>>,
    timeout: Duration,
}

impl PersistentWorker {
    /// Write the private configuration and start the retained native process.
    pub fn start(
        location: EngineLocation,
        config: WorkerConfig,
        config_path: &Path,
        log_path: &Path,
    ) -> Result<Self> {
        let config_path = absolute_path(config_path)?;
        let log_path = absolute_path(log_path)?;
        if let Some(parent) = config_path.parent() {
            fs::create_dir_all(parent)?;
        }
        if let Some(parent) = log_path.parent() {
            fs::create_dir_all(parent)?;
        }
        let mut config = WorkerConfig {
            project_dir: papper_core::paths::canonical_project(&config.project_dir)?,
            ..config
        };
        for root in &mut config.work_dirs {
            let resolved = absolute_path(root)?;
            fs::create_dir_all(&resolved)?;
            *root = PathBuf::from(papper_core::paths::display_path(&resolved.canonicalize()?));
        }
        let mut temporary =
            tempfile::NamedTempFile::new_in(config_path.parent().context("Invalid config path")?)?;
        serde_json::to_writer(&mut temporary, &config)?;
        temporary.flush()?;
        temporary
            .persist(&config_path)
            .map_err(|error| error.error)?;
        let mut worker = Self {
            location,
            config,
            config_path,
            log_path,
            process: None,
            input: None,
            responses: None,
            reader: None,
            timeout: Duration::from_secs(120),
        };
        worker.spawn()?;
        Ok(worker)
    }

    /// Set a bounded conversion deadline appropriate for the selected target.
    pub fn set_timeout(&mut self, timeout: Duration) {
        self.timeout = timeout;
    }

    /// Probe the existing native process without executing an extra conversion.
    pub fn is_alive(&mut self) -> bool {
        self.process
            .as_mut()
            .is_some_and(|child| matches!(child.try_wait(), Ok(None)))
    }

    /// Expose the owned native PID for diagnostics and safe process cleanup.
    pub fn pid(&self) -> Option<u32> {
        self.process.as_ref().map(Child::id)
    }

    /// Convert once; native crashes restart on the next request, never replay writes.
    pub fn request(&mut self, request: &WorkerRequest) -> Result<WorkerResponse> {
        if !self.is_alive() {
            eprintln!("[Pandoc server] Worker exited; restarting for the next conversion");
            self.close();
            self.spawn()?;
        }
        let mut line = serde_json::to_vec(request)?;
        line.push(b'\n');
        let write_result = self
            .input
            .as_mut()
            .context("Worker stdin is unavailable")?
            .write_all(&line)
            .and_then(|()| {
                self.input
                    .as_mut()
                    .expect("stdin exists while sending")
                    .flush()
            });
        if let Err(error) = write_result {
            let failure = self.failure(&format!("could not send request: {error}"));
            self.close();
            return Err(failure);
        }
        let received = self
            .responses
            .as_ref()
            .context("Worker stdout is unavailable")?
            .recv_timeout(self.timeout);
        let response = match received {
            Ok(Ok(line)) => line,
            Ok(Err(error)) => {
                let failure = self.failure(&error);
                self.close();
                return Err(failure);
            }
            Err(error) => {
                let failure = self.failure(&format!(
                    "no response before the conversion deadline: {error}"
                ));
                self.close();
                return Err(failure);
            }
        };
        let parsed: WorkerResponse = match serde_json::from_str(&response) {
            Ok(parsed) => parsed,
            Err(error) => {
                let failure = self.failure(&format!("invalid worker JSON response: {error}"));
                self.close();
                return Err(failure);
            }
        };
        if !parsed.ok {
            bail!(
                "{}",
                parsed
                    .error
                    .as_deref()
                    .unwrap_or("Papper Pandoc worker conversion failed")
            );
        }
        Ok(parsed)
    }

    /// Terminate and reap only this owned process, including protocol reader handles.
    pub fn close(&mut self) {
        self.input.take();
        // Drop the receiver first so a protocol-reader send cannot deadlock cleanup.
        self.responses.take();
        if let Some(mut child) = self.process.take() {
            let _ = child.kill();
            let _ = child.wait();
        }
        if let Some(reader) = self.reader.take() {
            let _ = reader.join();
        }
    }

    /// Start the private worker, inheriting the environment without Python shims.
    fn spawn(&mut self) -> Result<()> {
        let override_command = env::var("PMT_PANDOC_SERVER_WORKER_COMMAND").ok();
        let words = if let Some(override_command) = override_command {
            split_command(&override_command)?
        } else {
            if !self.location.embedded_crossref {
                bail!(
                    "The selected ordinary Pandoc cannot run the Papper worker; install pmt-pandoc-worker or set PMT_PANDOC_SERVER_WORKER_COMMAND"
                );
            }
            vec![self.location.executable.as_os_str().to_owned()]
        };
        let mut command = Command::new(&words[0]);
        // Older explicit worker overrides accept --config but not --pmt-worker.
        // The retained shared engine dispatches this legacy private entry identically.
        command
            .args(&words[1..])
            .arg("--config")
            .arg(&self.config_path)
            .current_dir(&self.config.project_dir)
            .stdin(Stdio::piped())
            .stdout(Stdio::piped());
        let log = OpenOptions::new()
            .append(true)
            .create(true)
            .open(&self.log_path)?;
        command.stderr(Stdio::from(log));
        configure_environment(&mut command, &BTreeMap::new())?;
        hide_console(&mut command);
        let mut child = command.spawn().with_context(|| {
            format!(
                "Cannot launch Papper worker: {}",
                words[0].to_string_lossy()
            )
        })?;
        let input = child.stdin.take().context("Could not open worker stdin")?;
        let output = child
            .stdout
            .take()
            .context("Could not open worker stdout")?;
        let (sender, receiver) = mpsc::sync_channel(1);
        let reader = thread::spawn(move || read_responses(BufReader::new(output), sender));
        self.process = Some(child);
        self.input = Some(input);
        self.responses = Some(receiver);
        self.reader = Some(reader);
        Ok(())
    }

    /// Include native exit status and a bounded stderr tail for loader failures.
    fn failure(&mut self, reason: &str) -> anyhow::Error {
        let status = self
            .process
            .as_mut()
            .and_then(|child| child.try_wait().ok())
            .flatten()
            .map(|status| status.to_string())
            .unwrap_or_else(|| "process still running".into());
        let tail = log_tail(&self.log_path).unwrap_or_default();
        anyhow::anyhow!(
            "Papper Pandoc worker failed: {reason}; {status}; log: {}{}{}",
            self.log_path.display(),
            if tail.is_empty() { "" } else { "\n" },
            tail
        )
    }
}

impl Drop for PersistentWorker {
    /// Reap the native worker when its Rust owner leaves scope.
    fn drop(&mut self) {
        self.close();
    }
}

/// Read exactly one JSON line per request and surface EOF without busy waiting.
fn read_responses(
    mut reader: impl BufRead,
    sender: mpsc::SyncSender<std::result::Result<String, String>>,
) {
    loop {
        let mut response = String::new();
        let result = match reader.read_line(&mut response) {
            Ok(0) => Err("worker stdout closed without a response".into()),
            Ok(_) if response.len() > 1024 * 1024 => {
                Err("worker response exceeds the protocol limit".into())
            }
            Ok(_) => Ok(response),
            Err(error) => Err(format!("cannot read worker stdout: {error}")),
        };
        let failed = result.is_err();
        if sender.send(result).is_err() || failed {
            break;
        }
    }
}

/// Preserve inherited variables while applying explicit unset operations and tools PATH.
fn configure_environment(
    command: &mut Command,
    overrides: &BTreeMap<String, Option<String>>,
) -> Result<()> {
    let managed = tools_bin_dir()?;
    let mut paths = vec![managed.clone()];
    let inherited = overrides
        .get("PATH")
        .cloned()
        .unwrap_or_else(|| env::var("PATH").ok());
    if let Some(path) = inherited {
        paths.extend(env::split_paths(OsStr::new(&path)).filter(|path| path != &managed));
    }
    command.env("PATH", env::join_paths(paths)?);
    for (key, value) in overrides {
        if key == "PATH" {
            continue;
        }
        if let Some(value) = value {
            command.env(key, value);
        } else {
            command.env_remove(key);
        }
    }
    Ok(())
}

/// Avoid allocating a visible console for a detached Windows HTML server.
fn hide_console(command: &mut Command) {
    #[cfg(windows)]
    {
        use std::os::windows::process::CommandExt;
        command.creation_flags(0x0800_0000); // CREATE_NO_WINDOW keeps editor builds unobtrusive.
    }
    #[cfg(not(windows))]
    let _ = command;
}

/// Resolve paths before the worker switches its cwd to the manuscript project.
fn absolute_path(path: &Path) -> Result<PathBuf> {
    Ok(if path.is_absolute() {
        path.into()
    } else {
        env::current_dir()?.join(path)
    })
}

/// Read only the last 8 KiB of a potentially long-running native worker log.
fn log_tail(path: &Path) -> Result<String> {
    let mut log = File::open(path)?;
    let length = log.metadata()?.len();
    log.seek(SeekFrom::Start(length.saturating_sub(8192)))?;
    let mut tail = Vec::new();
    log.read_to_end(&mut tail)?;
    Ok(String::from_utf8_lossy(&tail).trim().to_owned())
}

/// Split executable overrides while preserving quoted spaces and Windows slashes.
pub fn split_command(command: &str) -> Result<Vec<OsString>> {
    let mut words = Vec::new();
    let mut word = String::new();
    let mut quote = None;
    let mut started = false;
    let mut chars = command.chars().peekable();
    while let Some(character) = chars.next() {
        if character == '\\' && !cfg!(windows) && quote != Some('\'') {
            word.push(
                chars
                    .next()
                    .context("Command ends with an incomplete escape")?,
            );
            started = true;
        } else if quote == Some(character) {
            quote = None;
        } else if quote.is_none() && matches!(character, '\'' | '"') {
            quote = Some(character);
            started = true;
        } else if quote.is_none() && character.is_whitespace() {
            if started {
                words.push(OsString::from(std::mem::take(&mut word)));
                started = false;
            }
        } else {
            word.push(character);
            started = true;
        }
    }
    if quote.is_some() {
        bail!("Command contains an unclosed quote");
    }
    if started {
        words.push(OsString::from(word));
    }
    if words.is_empty() || words[0].is_empty() {
        bail!("Pandoc server command is empty");
    }
    Ok(words)
}
