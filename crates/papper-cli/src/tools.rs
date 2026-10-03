//! Resolve native tools, recover managed downloads and refresh release hints off the CLI path.

use anyhow::{Context, Result, bail, ensure};
use papper_core::paths::{atomic_write, display_path, home_dir, tools_bin_dir};
use papper_core::resources::ResourcePaths;
use pep440_rs::Version;
use regex::Regex;
use serde::{Deserialize, Serialize};
use serde_json::{Value, json};
use sha2::{Digest, Sha256};
use std::collections::BTreeMap;
use std::fs::{self, File};
use std::io::{Read, Write};
use std::path::{Component, Path, PathBuf};
use std::process::{Command, Output, Stdio};
use std::str::FromStr;
use std::time::{Duration, Instant, SystemTime, UNIX_EPOCH};

/// Executable selected for a conversion tool and its diagnostic arguments.
#[derive(Clone, Debug)]
pub struct ResolvedTool {
    pub name: String,
    pub executable: PathBuf,
    pub source: String,
    pub version_args: Vec<String>,
}

impl ResolvedTool {
    /// Describe an ordinary version command without spawning another process.
    fn ordinary(name: &str, executable: PathBuf, source: &str) -> Self {
        Self {
            name: name.into(),
            executable,
            source: source.into(),
            version_args: vec!["--version".into()],
        }
    }

    /// Execute the selected diagnostic with a deadline, including embedded crossref.
    pub fn version_output(&self) -> Result<String> {
        checked_output(
            &self.executable,
            &self.version_args,
            Duration::from_secs(15),
        )
    }
}

/// Per-command tool resolution, retaining validation across related diagnostics.
pub struct ToolManager {
    resources: ResourcePaths,
    cache: BTreeMap<String, ResolvedTool>,
}

impl ToolManager {
    /// Keep resource discovery independent of a manuscript's working directory.
    pub fn new(resources: ResourcePaths) -> Self {
        Self {
            resources,
            cache: BTreeMap::new(),
        }
    }

    /// Validate a packaged engine before permitting legacy download fallback.
    fn native_tools(&mut self) -> Result<Option<(ResolvedTool, ResolvedTool)>> {
        let location = papper_engine::discover_engine(&self.resources.root).ok();
        if let Some(location) = location.filter(|location| location.embedded_crossref) {
            if let (Some(pandoc), Some(crossref)) =
                (self.cache.get("pandoc"), self.cache.get("pandoc-crossref"))
                && pandoc.executable == location.executable
                && crossref.source == "embedded"
            {
                return Ok(Some((pandoc.clone(), crossref.clone())));
            }
            let pandoc =
                ResolvedTool::ordinary("pandoc", location.executable.clone(), &location.source);
            validate_tool(&pandoc).with_context(|| format!(
                "Papper's native engine has no usable Pandoc CLI: {}. Reinstall a current platform wheel or rebuild scripts/pandoc-server",
                location.executable.display()
            ))?;
            let crossref = ResolvedTool {
                name: "pandoc-crossref".into(),
                executable: location.executable,
                source: "embedded".into(),
                version_args: vec!["--pmt-crossref-version".into()],
            };
            let output = crossref
                .version_output()
                .context("Rebuild Papper's native engine for embedded crossref")?;
            ensure!(
                output.starts_with("pandoc-crossref v"),
                "Papper's native engine is missing embedded crossref: {}",
                crossref.executable.display()
            );
            self.remember(&pandoc);
            self.remember(&crossref);
            return Ok(Some((pandoc, crossref)));
        }
        // A wheel missing its bundled engine is incomplete; installing a second
        // Pandoc would mask that packaging error and change conversion behavior.
        ensure!(
            self.resources.root.join("scripts/pandoc-server").is_dir(),
            "Papper's platform wheel is missing its native Pandoc engine; reinstall a current wheel"
        );
        Ok(None)
    }

    /// Retain one validated selection for the remainder of the current command.
    fn remember(&mut self, tool: &ResolvedTool) {
        self.cache.insert(tool.name.clone(), tool.clone());
    }

    /// Prepare managed tools only when the source checkout has no shared engine.
    pub fn setup(&mut self, force: bool) -> Result<(ResolvedTool, ResolvedTool)> {
        self.cache.clear();
        if let Some(native) = self.native_tools()? {
            println!(
                "[TOOLS] Using Papper's native Pandoc CLI and embedded crossref; no tool downloads needed"
            );
            return Ok(native);
        }
        let crossref = self.install_managed("pandoc-crossref", None, force)?;
        let release = matching_pandoc_release(&crossref);
        let pandoc = self.install_managed("pandoc", release, force)?;
        self.remember(&pandoc);
        self.remember(&crossref);
        Ok((pandoc, crossref))
    }

    /// Resolve PATH tools for an unbuilt checkout, installing compatible releases as needed.
    pub fn ensure_pandoc_tools(&mut self) -> Result<(ResolvedTool, ResolvedTool)> {
        if let Some(native) = self.native_tools()? {
            return Ok(native);
        }
        if let (Some(pandoc), Some(crossref)) =
            (self.cache.get("pandoc"), self.cache.get("pandoc-crossref"))
        {
            return Ok((pandoc.clone(), crossref.clone()));
        }
        let system = papper_engine::find_on_path("pandoc");
        let candidate = system
            .clone()
            .unwrap_or_else(|| managed_executable("pandoc"));
        if candidate.is_file() {
            let pandoc = ResolvedTool::ordinary(
                "pandoc",
                candidate,
                if system.is_some() {
                    "PATH"
                } else {
                    "~/.papper/tools"
                },
            );
            if validate_tool(&pandoc).is_err() {
                println!(
                    "[TOOLS] Installed Pandoc is unusable or below 3.11; starting managed setup"
                );
                return self.setup(false);
            }
            self.remember(&pandoc);
        }
        let crossref = self.resolve_ordinary("pandoc-crossref", None)?;
        let release = if self.cache.contains_key("pandoc") {
            None
        } else {
            matching_pandoc_release(&crossref)
        };
        let pandoc = self.resolve_ordinary("pandoc", release)?;
        Ok((pandoc, crossref))
    }

    /// Prefer a validated PATH or managed executable before downloading a release.
    fn resolve_ordinary(&mut self, name: &str, release: Option<Value>) -> Result<ResolvedTool> {
        if let Some(tool) = self.cache.get(name) {
            return Ok(tool.clone());
        }
        let candidates = [
            papper_engine::find_on_path(name)
                .map(|path| ResolvedTool::ordinary(name, path, "PATH")),
            Some(ResolvedTool::ordinary(
                name,
                managed_executable(name),
                "~/.papper/tools",
            )),
        ];
        for candidate in candidates.into_iter().flatten() {
            if candidate.executable.is_file() && validate_tool(&candidate).is_ok() {
                self.remember(&candidate);
                return Ok(candidate);
            }
        }
        println!(
            "[TOOLS] No usable {name} found; installing into {}",
            tools_bin_dir().display()
        );
        let tool = install_release(name, release, false)?;
        self.remember(&tool);
        Ok(tool)
    }

    /// Ignore PATH during explicit setup, while retaining an already usable managed install.
    fn install_managed(
        &mut self,
        name: &str,
        release: Option<Value>,
        force: bool,
    ) -> Result<ResolvedTool> {
        let tool = ResolvedTool::ordinary(name, managed_executable(name), "~/.papper/tools");
        if !force && tool.executable.is_file() && validate_tool(&tool).is_ok() {
            println!(
                "[TOOLS] {name} already installed in ~/.papper/tools: {}",
                tool.executable.display()
            );
            return Ok(tool);
        }
        install_release(name, release, force)
    }
}

/// Validate native tools and print their resolved paths for `papper setup`.
pub fn setup(resources: &ResourcePaths, force: bool) -> Result<()> {
    let (pandoc, crossref) = ToolManager::new(resources.clone()).setup(force)?;
    for tool in [pandoc, crossref] {
        println!(
            "[OK] {}: {} [{}]",
            tool.name,
            tool.executable.display(),
            tool.source
        );
    }
    Ok(())
}

/// Diagnose the retained engine, native conversion capabilities and manuscript resources.
pub fn doctor(resources: &ResourcePaths, project: &Path) -> Result<i32> {
    let mut checks: Vec<(String, bool, String)> = Vec::new();
    match ToolManager::new(resources.clone()).ensure_pandoc_tools() {
        Ok((pandoc, crossref)) => {
            for tool in [pandoc, crossref] {
                match tool.version_output() {
                    Ok(output) => checks.push((
                        format!("{} --version", tool.name),
                        true,
                        format!(
                            "{} [{}: {}]",
                            output.lines().next().unwrap_or("available"),
                            tool.source,
                            tool.executable.display()
                        ),
                    )),
                    Err(error) => {
                        checks.push((format!("{} --version", tool.name), false, error.to_string()))
                    }
                }
            }
        }
        Err(error) => checks.push((
            "Pandoc and embedded crossref".into(),
            false,
            error.to_string(),
        )),
    }
    checks.push((
        "native document processing".into(),
        true,
        "DOCX, YAML, XML, Pandoc AST and SVG rasterization available".into(),
    ));
    let resources_to_check = [
        ("papper pandoc defaults", "pandoc/pandoc-docx.yml"),
        ("papper HTML defaults", "pandoc/pandoc-html.yml"),
        (
            "papper DOCX metadata filter",
            "pandoc/filters/docx/docx_metadata.lua",
        ),
        (
            "papper shared numbering filter",
            "pandoc/filters/shared/normalize_chinese_numbering.lua",
        ),
        (
            "papper shared table filter",
            "pandoc/filters/shared/merge_table_cells.lua",
        ),
        (
            "papper shared paragraph filter",
            "pandoc/filters/shared/paragraph_custom_styles.lua",
        ),
        (
            "papper HTML revision filter",
            "pandoc/filters/html/revision_table_styles.lua",
        ),
        (
            "papper HTML subfigure filter",
            "pandoc/filters/html/subfigure_layout_styles.lua",
        ),
        (
            "papper DOCX AST filters",
            "pandoc/filters/docx/inline_math_spacing.lua",
        ),
    ];
    for (label, relative) in resources_to_check {
        let path = resources.root.join(relative);
        checks.push((label.into(), path.is_file(), display_path(&path)));
    }
    checks.push((
        "project directory".into(),
        project.is_dir(),
        display_path(project),
    ));
    for name in ["manuscript.md", "style.yml"] {
        let path = project.join(name);
        checks.push((
            format!("project {name}"),
            path.is_file(),
            display_path(&path),
        ));
    }
    let mut errors = false;
    for (label, ok, detail) in checks {
        println!("{} {label}: {detail}", if ok { "[OK]" } else { "[ERROR]" });
        errors |= !ok;
    }
    Ok(i32::from(errors))
}

/// Return the native executable filename on the current platform.
fn executable_name(name: &str) -> String {
    if cfg!(windows) {
        format!("{name}.exe")
    } else {
        name.into()
    }
}

/// Resolve managed executable paths without altering the process environment.
fn managed_executable(name: &str) -> PathBuf {
    tools_bin_dir().join(executable_name(name))
}

/// Parse the tool's own version line and enforce Pandoc's supported ABI floor.
fn validate_tool(tool: &ResolvedTool) -> Result<()> {
    let output = tool.version_output()?;
    let expression = Regex::new(&format!(
        r"(?im)^{}(?:\.exe)?\s+v?(\d+(?:\.\d+)*)",
        regex::escape(&tool.name)
    ))?;
    let matched = expression
        .captures(&output)
        .context("Unrecognized version output")?;
    if tool.name == "pandoc" {
        let components: Vec<u64> = matched[1]
            .split('.')
            .map(str::parse)
            .collect::<std::result::Result<_, _>>()?;
        ensure!(
            components.as_slice() >= [3, 11].as_slice(),
            "Pandoc {} is older than 3.11",
            &matched[1]
        );
    }
    Ok(())
}

/// Capture diagnostic output without letting a broken executable hang setup.
fn checked_output(executable: &Path, arguments: &[String], timeout: Duration) -> Result<String> {
    let mut command = Command::new(executable);
    command
        .args(arguments)
        .stdin(Stdio::null())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped());
    hide_console(&mut command);
    let mut child = command
        .spawn()
        .with_context(|| format!("Cannot launch {}", executable.display()))?;
    let stdout = child.stdout.take().context("Missing diagnostic stdout")?;
    let stderr = child.stderr.take().context("Missing diagnostic stderr")?;
    let out_reader = std::thread::spawn(move || read_bounded_output(stdout));
    let err_reader = std::thread::spawn(move || read_bounded_output(stderr));
    let start = Instant::now();
    let status = loop {
        if let Some(status) = child.try_wait()? {
            break status;
        }
        if start.elapsed() >= timeout {
            let _ = child.kill();
            let _ = child.wait();
            bail!("Diagnostic command timed out: {}", executable.display());
        }
        std::thread::sleep(Duration::from_millis(10));
    };
    let stdout = out_reader
        .join()
        .map_err(|_| anyhow::anyhow!("Diagnostic reader failed"))??;
    let stderr = err_reader
        .join()
        .map_err(|_| anyhow::anyhow!("Diagnostic reader failed"))??;
    let output = Output {
        status,
        stdout,
        stderr,
    };
    ensure!(
        output.status.success(),
        "{} failed ({}): {}",
        executable.display(),
        output.status,
        String::from_utf8_lossy(&output.stderr).trim()
    );
    Ok([
        String::from_utf8_lossy(&output.stdout).trim().to_string(),
        String::from_utf8_lossy(&output.stderr).trim().to_string(),
    ]
    .into_iter()
    .filter(|part| !part.is_empty())
    .collect::<Vec<_>>()
    .join("\n"))
}

/// Drain both child pipes while retaining a bounded diagnostic prefix.
fn read_bounded_output(mut reader: impl Read) -> std::io::Result<Vec<u8>> {
    let mut output = Vec::new();
    let mut buffer = [0_u8; 8192];
    loop {
        let count = reader.read(&mut buffer)?;
        if count == 0 {
            break;
        }
        let retain = count.min((1024 * 1024usize).saturating_sub(output.len()));
        output.extend_from_slice(&buffer[..retain]);
    }
    Ok(output)
}

/// Keep native diagnostics and detached workers from creating console windows.
fn hide_console(command: &mut Command) {
    #[cfg(windows)]
    {
        use std::os::windows::process::CommandExt;
        command.creation_flags(0x0800_0000);
    }
    #[cfg(not(windows))]
    let _ = command;
}

/// Select release archive suffixes using the legacy platform compatibility contract.
fn asset_preference(tool: &str) -> Result<&'static str> {
    let arm = std::env::consts::ARCH == "aarch64";
    match (tool, std::env::consts::OS, arm) {
        ("pandoc", "windows", _) => Ok("windows-x86_64.zip"),
        ("pandoc", "macos", true) => Ok("arm64-macOS.zip"),
        ("pandoc", "macos", false) => Ok("x86_64-macOS.zip"),
        ("pandoc", "linux", true) => Ok("linux-arm64.tar.gz"),
        ("pandoc", "linux", false) => Ok("linux-amd64.tar.gz"),
        ("pandoc-crossref", "windows", _) => Ok("Windows-X64.7z"),
        ("pandoc-crossref", "macos", true) => Ok("macOS-ARM64.tar.xz"),
        ("pandoc-crossref", "macos", false) => Ok("macOS-X64.tar.xz"),
        ("pandoc-crossref", "linux", true) => Ok("Linux-ARM64.tar.xz"),
        ("pandoc-crossref", "linux", false) => Ok("Linux-X64.tar.xz"),
        _ => bail!(
            "Unsupported platform for automatic {tool} install: {}/{}",
            std::env::consts::OS,
            std::env::consts::ARCH
        ),
    }
}

/// Preserve release asset names, URLs and GitHub-provided SHA-256 digests.
#[derive(Debug)]
struct ReleaseAsset {
    name: String,
    url: String,
    digest: Option<String>,
}

/// Find the first platform-compatible asset and reject unsafe cache filenames.
fn select_asset(tool: &str, release: &Value) -> Result<ReleaseAsset> {
    let preference = asset_preference(tool)?.to_lowercase();
    let assets = release
        .get("assets")
        .and_then(Value::as_array)
        .context("Release contains no assets")?;
    for asset in assets {
        let name = asset
            .get("name")
            .and_then(Value::as_str)
            .unwrap_or_default();
        let url = asset
            .get("browser_download_url")
            .and_then(Value::as_str)
            .unwrap_or_default();
        if name.to_lowercase().contains(&preference) && !url.is_empty() {
            ensure!(
                !name.contains(['/', '\\']) && name != "." && name != "..",
                "Unsafe release asset name"
            );
            return Ok(ReleaseAsset {
                name: name.into(),
                url: url.into(),
                digest: asset
                    .get("digest")
                    .and_then(Value::as_str)
                    .map(str::to_string),
            });
        }
    }
    bail!(
        "No compatible {tool} release asset found. Available assets: {}",
        assets
            .iter()
            .filter_map(|asset| asset.get("name").and_then(Value::as_str))
            .collect::<Vec<_>>()
            .join(", ")
    )
}

/// Fetch release metadata using the selected user's proxy and bounded deadlines.
fn request_json(url: &str, timeout: Duration) -> Result<Value> {
    let response = download_agent(timeout, url)?
        .get(url)
        .set("User-Agent", "papper")
        .call()
        .with_context(|| format!("Could not fetch release metadata from {url}"))?;
    let mut bytes = Vec::new();
    response
        .into_reader()
        .take(16 * 1024 * 1024)
        .read_to_end(&mut bytes)?;
    serde_json::from_slice(&bytes).context("Invalid release metadata JSON")
}

/// Build a network client only on setup/update paths, never during ordinary CLI startup.
fn download_agent(timeout: Duration, url: &str) -> Result<ureq::Agent> {
    let mut builder = ureq::AgentBuilder::new()
        .try_proxy_from_env(false)
        .timeout(timeout)
        .timeout_connect(timeout.min(Duration::from_secs(30)));
    if let Some(proxy) = url.starts_with("https://").then(download_proxy).flatten() {
        builder = builder.proxy(ureq::Proxy::new(proxy).context("Invalid download proxy")?);
    }
    Ok(builder.build())
}

/// Prefer explicit HTTPS proxy settings before platform system proxy settings.
fn download_proxy() -> Option<String> {
    std::env::var("HTTPS_PROXY")
        .ok()
        .filter(|value| !value.is_empty())
        .or_else(|| {
            std::env::var("https_proxy")
                .ok()
                .filter(|value| !value.is_empty())
        })
        .or_else(system_proxy)
}

/// Read the Windows or macOS proxy without importing Python or changing global settings.
fn system_proxy() -> Option<String> {
    #[cfg(windows)]
    {
        let key = r"HKCU\Software\Microsoft\Windows\CurrentVersion\Internet Settings";
        let executable = Path::new("reg.exe");
        let enabled = checked_output(
            executable,
            &[
                "query".into(),
                key.into(),
                "/v".into(),
                "ProxyEnable".into(),
            ],
            Duration::from_secs(2),
        )
        .ok()?;
        if !enabled
            .split_whitespace()
            .last()
            .is_some_and(|value| value == "0x1")
        {
            return None;
        }
        let output = checked_output(
            executable,
            &[
                "query".into(),
                key.into(),
                "/v".into(),
                "ProxyServer".into(),
            ],
            Duration::from_secs(2),
        )
        .ok()?;
        let proxy = output
            .lines()
            .find_map(|line| line.split_once("REG_SZ").map(|(_, value)| value.trim()))?;
        if !proxy.contains('=') {
            return Some(proxy_url(proxy));
        }
        for scheme in ["https=", "http="] {
            if let Some(value) = proxy
                .split(';')
                .find_map(|entry| entry.trim().strip_prefix(scheme))
            {
                return Some(proxy_url(value));
            }
        }
        None
    }
    #[cfg(target_os = "macos")]
    {
        let output = checked_output(
            Path::new("/usr/sbin/scutil"),
            &["--proxy".into()],
            Duration::from_secs(2),
        )
        .ok()?;
        let entries: BTreeMap<_, _> = output
            .lines()
            .filter_map(|line| line.trim().split_once(" : "))
            .collect();
        for scheme in ["HTTPS", "HTTP"] {
            if entries.get(format!("{scheme}Enable").as_str()) == Some(&"1") {
                let host = entries.get(format!("{scheme}Proxy").as_str())?;
                let port = entries.get(format!("{scheme}Port").as_str())?;
                return Some(format!("http://{host}:{port}"));
            }
        }
        None
    }
    #[cfg(not(any(windows, target_os = "macos")))]
    {
        None
    }
}

/// Add the standard HTTP scheme to registry proxy addresses lacking one.
#[cfg(windows)]
fn proxy_url(value: &str) -> String {
    if value.contains("://") {
        value.into()
    } else {
        format!("http://{value}")
    }
}

/// Return the upstream GitHub repository associated with a managed tool.
fn repository(tool: &str) -> Result<&'static str> {
    match tool {
        "pandoc" => Ok("jgm/pandoc"),
        "pandoc-crossref" => Ok("lierdakil/pandoc-crossref"),
        _ => bail!("Unsupported managed tool: {tool}"),
    }
}

/// Fetch the latest release, or the Pandoc ABI release explicitly requested by crossref.
fn release_metadata(tool: &str, tag: Option<&str>) -> Result<Value> {
    let suffix = tag
        .map(|tag| format!("tags/{tag}"))
        .unwrap_or_else(|| "latest".into());
    request_json(
        &format!(
            "https://api.github.com/repos/{}/releases/{suffix}",
            repository(tool)?
        ),
        Duration::from_secs(60),
    )
}

/// Match a managed Pandoc download to crossref's reported compile-time version.
fn matching_pandoc_release(crossref: &ResolvedTool) -> Option<Value> {
    let output = crossref.version_output().ok()?;
    let version = output
        .split_once("built with Pandoc v")?
        .1
        .split(',')
        .next()?
        .trim();
    if !Regex::new(r"^\d+(?:\.\d+)*$").ok()?.is_match(version) {
        return None;
    }
    let components: Vec<u64> = version
        .split('.')
        .map(str::parse)
        .collect::<std::result::Result<_, _>>()
        .ok()?;
    if components.as_slice() < [3, 11].as_slice() {
        return None;
    }
    match release_metadata("pandoc", Some(version)) {
        Ok(release) => Some(release),
        Err(_) => {
            println!(
                "[TOOLS] No Pandoc release found for pandoc-crossref version {version}; using latest Pandoc"
            );
            None
        }
    }
}

/// Stream complete assets to a temporary file before replacing a cached archive.
fn download_asset(asset: &ReleaseAsset, target: &Path, force: bool) -> Result<()> {
    if !force
        && fs::metadata(target).is_ok_and(|metadata| metadata.len() > 0)
        && verify_digest(target, asset.digest.as_deref()).is_ok()
    {
        return Ok(());
    }
    let parent = target.parent().context("Invalid download target")?;
    fs::create_dir_all(parent)?;
    println!("[TOOLS] Downloading {} -> {}", asset.url, target.display());
    let response = download_agent(Duration::from_secs(300), &asset.url)?
        .get(&asset.url)
        .set("User-Agent", "papper")
        .call()
        .context("Could not download release asset")?;
    let total = response
        .header("Content-Length")
        .and_then(|value| value.parse::<u64>().ok())
        .filter(|size| *size > 0);
    let mut reader = response.into_reader();
    let mut temporary = tempfile::Builder::new()
        .prefix("papper-download-")
        .suffix(".part")
        .tempfile_in(parent)?;
    let mut buffer = vec![0_u8; 256 * 1024];
    let mut downloaded = 0_u64;
    let mut last_progress = Instant::now();
    loop {
        let count = reader
            .read(&mut buffer)
            .context("Incomplete release download")?;
        if count == 0 {
            break;
        }
        temporary.write_all(&buffer[..count])?;
        downloaded += count as u64;
        if last_progress.elapsed() >= Duration::from_secs(1) {
            eprint!(
                "\r[TOOLS] Downloaded {:.1} MiB",
                downloaded as f64 / (1024.0 * 1024.0)
            );
            last_progress = Instant::now();
        }
    }
    ensure!(downloaded > 0, "Download returned an empty response");
    if let Some(total) = total {
        ensure!(
            downloaded == total,
            "Incomplete download: expected {total} bytes, received {downloaded}"
        );
    }
    temporary.flush()?;
    verify_digest(temporary.path(), asset.digest.as_deref())?;
    temporary.persist(target).map_err(|error| error.error)?;
    eprintln!(
        "\r[TOOLS] Downloaded {:.1} MiB",
        downloaded as f64 / (1024.0 * 1024.0)
    );
    Ok(())
}

/// Validate a GitHub SHA-256 digest before an archive is accepted or reused.
fn verify_digest(path: &Path, digest: Option<&str>) -> Result<()> {
    let Some(digest) = digest else {
        return Ok(());
    };
    let Some(expected) = digest.strip_prefix("sha256:") else {
        return Ok(());
    };
    ensure!(
        expected.len() == 64 && expected.bytes().all(|byte| byte.is_ascii_hexdigit()),
        "Invalid release SHA-256 digest"
    );
    let mut source = File::open(path)?;
    let mut hasher = Sha256::new();
    let mut buffer = [0_u8; 64 * 1024];
    loop {
        let count = source.read(&mut buffer)?;
        if count == 0 {
            break;
        }
        hasher.update(&buffer[..count]);
    }
    let actual: String = hasher
        .finalize()
        .iter()
        .map(|byte| format!("{byte:02x}"))
        .collect();
    ensure!(
        actual.eq_ignore_ascii_case(expected),
        "Release archive checksum mismatch"
    );
    Ok(())
}

/// Resolve archive entries inside a fresh extraction directory on every platform.
fn archive_destination(root: &Path, name: &str) -> Result<PathBuf> {
    let normalized = name.replace('\\', "/");
    let relative = Path::new(&normalized);
    // Checking backslashes and drive prefixes on Unix as well prevents an
    // archive accepted there from escaping when processed by a Windows client.
    ensure!(
        !normalized.starts_with('/')
            && !normalized.contains(':')
            && relative
                .components()
                .all(|component| matches!(component, Component::Normal(_) | Component::CurDir)),
        "Archive entry escapes extraction directory: {name}"
    );
    Ok(root.join(relative))
}

/// Extract ZIP, tar or 7z without allowing entries to overwrite unrelated files.
fn extract_archive(archive: &Path, destination: &Path) -> Result<()> {
    let name = archive
        .file_name()
        .context("Invalid archive path")?
        .to_string_lossy()
        .to_lowercase();
    fs::create_dir_all(destination)?;
    if name.ends_with(".zip") {
        let mut zip = zip::ZipArchive::new(File::open(archive)?)?;
        for index in 0..zip.len() {
            let mut entry = zip.by_index(index)?;
            let target = archive_destination(destination, entry.name())?;
            ensure!(
                entry
                    .unix_mode()
                    .is_none_or(|mode| mode & 0o170000 != 0o120000),
                "Tool archives may not contain symlinks"
            );
            if entry.is_dir() {
                fs::create_dir_all(target)?;
            } else {
                fs::create_dir_all(target.parent().context("Invalid archive entry")?)?;
                std::io::copy(&mut entry, &mut File::create(target)?)?;
            }
        }
    } else if name.ends_with(".tar.gz") || name.ends_with(".tar.xz") {
        let file = File::open(archive)?;
        let reader: Box<dyn Read> = if name.ends_with(".gz") {
            Box::new(flate2::read::GzDecoder::new(file))
        } else {
            Box::new(xz2::read::XzDecoder::new(file))
        };
        for entry in tar::Archive::new(reader).entries()? {
            let mut entry = entry?;
            let target = archive_destination(destination, &entry.path()?.to_string_lossy())?;
            let kind = entry.header().entry_type();
            if kind.is_dir() {
                fs::create_dir_all(target)?;
            } else if kind.is_file() {
                fs::create_dir_all(target.parent().context("Invalid archive entry")?)?;
                std::io::copy(&mut entry, &mut File::create(target)?)?;
            } else {
                bail!(
                    "Unsupported special entry in tool archive: {}",
                    target.display()
                );
            }
        }
    } else if name.ends_with(".7z") {
        sevenz_rust2::decompress_file_with_extract_fn(archive, destination, |entry, reader, _| {
            let target = archive_destination(destination, entry.name())
                .map_err(|error| sevenz_rust2::Error::Other(error.to_string().into()))?;
            if entry.is_directory() {
                fs::create_dir_all(target)?;
            } else {
                fs::create_dir_all(
                    target
                        .parent()
                        .ok_or_else(|| std::io::Error::other("Invalid archive entry"))?,
                )?;
                std::io::copy(reader, &mut File::create(target)?)?;
            }
            Ok(true)
        })?;
    } else {
        bail!("Unsupported tool archive format: {name}");
    }
    Ok(())
}

/// Find the shallowest command in an extracted release without following symlinks.
fn find_executable(root: &Path, tool: &str) -> Result<PathBuf> {
    let expected = executable_name(tool);
    let mut pending = vec![root.to_path_buf()];
    let mut found = Vec::new();
    while let Some(directory) = pending.pop() {
        for entry in fs::read_dir(directory)? {
            let entry = entry?;
            let kind = entry.file_type()?;
            if kind.is_dir() {
                pending.push(entry.path());
            } else if kind.is_file()
                && entry
                    .file_name()
                    .to_string_lossy()
                    .eq_ignore_ascii_case(&expected)
            {
                found.push(entry.path());
            }
        }
    }
    found.sort_by_key(|path| path.components().count());
    found.into_iter().next().with_context(|| {
        format!(
            "Could not find {expected} in extracted archive: {}",
            root.display()
        )
    })
}

/// Mark an extracted or staged command executable on POSIX installations.
fn make_executable(path: &Path) -> Result<()> {
    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        let mut permissions = fs::metadata(path)?.permissions();
        permissions.set_mode(permissions.mode() | 0o111);
        fs::set_permissions(path, permissions)?;
    }
    #[cfg(not(unix))]
    let _ = path;
    Ok(())
}

/// Recover one poisoned archive and atomically install only a validated executable.
fn install_release(tool: &str, release: Option<Value>, force: bool) -> Result<ResolvedTool> {
    let release = match release {
        Some(release) => release,
        None => release_metadata(tool, None)?,
    };
    let tag = release
        .get("tag_name")
        .or_else(|| release.get("name"))
        .and_then(Value::as_str)
        .unwrap_or("latest")
        .trim_start_matches('v');
    let asset = select_asset(tool, &release)?;
    let archive = home_dir().join("tools/downloads").join(&asset.name);
    download_asset(&asset, &archive, force)?;
    let bin = tools_bin_dir();
    fs::create_dir_all(&bin)?;
    let installed = bin.join(executable_name(tool));
    for attempt in 0..2 {
        let working = tempfile::Builder::new().prefix("papper-tool-").tempdir()?;
        let result: Result<PathBuf> = (|| {
            extract_archive(&archive, working.path())?;
            let executable = find_executable(working.path(), tool)?;
            make_executable(&executable)?;
            validate_tool(&ResolvedTool::ordinary(
                tool,
                executable.clone(),
                "release archive",
            ))?;
            Ok(executable)
        })();
        match result {
            Ok(executable) => {
                let mut staging = tempfile::Builder::new()
                    .prefix("papper-install-")
                    .suffix(".part")
                    .tempfile_in(&bin)?;
                std::io::copy(&mut File::open(executable)?, &mut staging)?;
                staging.flush()?;
                make_executable(staging.path())?;
                staging.persist(&installed).map_err(|error| error.error)?;
                let metadata = json!({"tool":tool,"version":tag,"asset":asset.name,"url":asset.url,"executable":installed.to_string_lossy().replace('\\',"/")});
                atomic_write(
                    &bin.join(format!("{tool}.json")),
                    &serde_json::to_vec_pretty(&metadata)?,
                )?;
                println!("[TOOLS] Installed {tool} {tag}: {}", installed.display());
                return Ok(ResolvedTool::ordinary(
                    tool,
                    installed,
                    &format!("~/.papper/tools ({tag})"),
                ));
            }
            Err(error) => {
                let _ = fs::remove_file(&archive);
                if attempt == 1 {
                    return Err(error).context(format!(
                        "Could not unpack a usable {tool} release from {}",
                        asset.url
                    ));
                }
                println!("[TOOLS] Invalid {tool} archive ({error}); downloading again");
                download_asset(&asset, &archive, true)?;
            }
        }
    }
    unreachable!("bounded install loop returns after its final attempt")
}

/// Latest known PyPI version and timestamps used by detached refresh workers.
#[derive(Clone, Debug, Deserialize, Serialize)]
pub struct UpdateCache {
    pub latest_version: Option<String>,
    pub checked_at: Option<f64>,
    pub attempted_at: f64,
}

/// Locate the per-user update cache without touching the manuscript or creating directories.
pub fn update_cache_path() -> PathBuf {
    if let Some(path) = std::env::var_os("PAPPER_UPDATE_CACHE") {
        return PathBuf::from(path);
    }
    let home = std::env::var_os("USERPROFILE")
        .or_else(|| std::env::var_os("HOME"))
        .map(PathBuf::from)
        .unwrap_or_else(std::env::temp_dir);
    let root = if cfg!(windows) {
        std::env::var_os("LOCALAPPDATA")
            .map(PathBuf::from)
            .unwrap_or_else(|| home.join("AppData/Local"))
    } else if cfg!(target_os = "macos") {
        home.join("Library/Caches")
    } else {
        std::env::var_os("XDG_CACHE_HOME")
            .map(PathBuf::from)
            .unwrap_or_else(|| home.join(".cache"))
    };
    root.join("papper/update.json")
}

/// Read only structurally valid cache records, including finite numeric timestamps.
pub fn read_update_cache(path: &Path) -> Option<UpdateCache> {
    let cache: UpdateCache = serde_json::from_slice(&fs::read(path).ok()?).ok()?;
    (cache.attempted_at.is_finite() && cache.checked_at.is_none_or(f64::is_finite)).then_some(cache)
}

/// Respect the success TTL and failed-attempt cooldown, including clock rollback.
pub fn update_cache_needs_refresh(cache: Option<&UpdateCache>, now: f64) -> bool {
    let Some(cache) = cache else {
        return true;
    };
    if cache
        .checked_at
        .is_some_and(|checked| (0.0..3600.0).contains(&(now - checked)))
    {
        return false;
    }
    now - cache.attempted_at >= 900.0
}

/// Compare cached versions using PyPI's PEP 440 ordering without a network request.
pub fn cached_available_update(installed: &str, cache: Option<&UpdateCache>) -> Option<String> {
    let latest = cache?.latest_version.as_ref()?;
    let current = Version::from_str(installed).ok()?;
    let remote = Version::from_str(latest).ok()?;
    (remote > current).then(|| latest.clone())
}

/// Return the wall-clock Unix timestamp used by persisted update records.
fn timestamp() -> f64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap_or_default()
        .as_secs_f64()
}

/// Print a cached hint and spawn a silent refresh only after a completed public command.
pub fn notify_and_schedule_update_check(installed: &str) {
    if std::env::var("PAPPER_DISABLE_UPDATE_CHECK").is_ok_and(|value| value == "1") {
        return;
    }
    let cache = read_update_cache(&update_cache_path());
    if let Some(latest) = cached_available_update(installed, cache.as_ref()) {
        eprintln!("[UPDATE] Papper {latest} is available, upgrade with `uv tool upgrade papper`");
    }
    if update_cache_needs_refresh(cache.as_ref(), timestamp())
        && let Ok(executable) = std::env::current_exe()
    {
        let mut command = Command::new(executable);
        command
            .arg("__update")
            .stdin(Stdio::null())
            .stdout(Stdio::null())
            .stderr(Stdio::null());
        hide_console(&mut command);
        #[cfg(unix)]
        {
            use std::os::unix::process::CommandExt;
            command.process_group(0);
        }
        // Captured Windows CLI handles must not remain open in the helper;
        // otherwise callers waiting for pipe EOF also wait for network I/O.
        let _ = papper_server::spawn_background(&mut command);
    }
}

/// Retain the previous successful hint when a refresh fails or returns invalid metadata.
fn refreshed_cache(
    previous: Option<&UpdateCache>,
    latest: Option<String>,
    now: f64,
) -> UpdateCache {
    match latest {
        Some(latest) => UpdateCache {
            latest_version: Some(latest),
            checked_at: Some(now),
            attempted_at: now,
        },
        None => UpdateCache {
            latest_version: previous.and_then(|cache| cache.latest_version.clone()),
            checked_at: previous.and_then(|cache| cache.checked_at),
            attempted_at: now,
        },
    }
}

/// Refresh once in the detached `__update` entry; failures never affect document builds.
pub fn run_update_worker() -> Result<()> {
    let path = update_cache_path();
    let previous = read_update_cache(&path);
    let now = timestamp();
    let latest = request_json("https://pypi.org/pypi/papper/json", Duration::from_secs(2))
        .ok()
        .and_then(|payload| {
            payload
                .get("info")?
                .get("version")?
                .as_str()
                .map(str::to_string)
        })
        .filter(|version| Version::from_str(version).is_ok());
    let cache = refreshed_cache(previous.as_ref(), latest, now);
    let _ = atomic_write(&path, &serde_json::to_vec(&cache)?);
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    /// Exercise real HTTP truncation, atomic cache preservation and successful recovery.
    #[test]
    fn interrupted_download_keeps_previous_archive_and_recovers() -> Result<()> {
        use std::net::TcpListener;
        let directory = tempfile::tempdir()?;
        let target = directory.path().join("archive.zip");
        fs::write(&target, b"existing complete archive")?;
        let listener = TcpListener::bind("127.0.0.1:0")?;
        let url = format!("http://{}/archive", listener.local_addr()?);
        let server = std::thread::spawn(move || -> std::io::Result<()> {
            for body in [b"bad".as_slice(), b"restored archive".as_slice()] {
                let (mut stream, _) = listener.accept()?;
                let mut request = [0_u8; 4096];
                let _ = stream.read(&mut request)?;
                write!(
                    stream,
                    "HTTP/1.1 200 OK\r\nContent-Length: 16\r\nConnection: close\r\n\r\n"
                )?;
                stream.write_all(body)?;
            }
            Ok(())
        });
        let asset = ReleaseAsset {
            name: "archive.zip".into(),
            url,
            digest: None,
        };
        assert!(download_asset(&asset, &target, true).is_err());
        assert_eq!(fs::read(&target)?, b"existing complete archive");
        // The second real response uses a matching declared length.
        let second = download_asset(&asset, &target, true);
        server.join().unwrap()?;
        second?;
        assert_eq!(fs::read(&target)?, b"restored archive");
        assert_eq!(fs::read_dir(directory.path())?.count(), 1);
        Ok(())
    }

    /// Catch poisoned release caches independently of archive parsing or executable validation.
    #[test]
    fn release_checksum_rejects_corrupt_bytes() -> Result<()> {
        let directory = tempfile::tempdir()?;
        let path = directory.path().join("asset");
        fs::write(&path, b"original archive")?;
        let expected: String = Sha256::digest(b"original archive")
            .iter()
            .map(|byte| format!("{byte:02x}"))
            .collect();
        let digest = format!("sha256:{expected}");
        verify_digest(&path, Some(&digest))?;
        fs::write(&path, b"modified archive")?;
        assert!(verify_digest(&path, Some(&digest)).is_err());
        Ok(())
    }

    /// Ensure valid ZIP contents survive extraction while traversal entries cannot write outside it.
    #[test]
    fn archive_extraction_preserves_contents_and_rejects_traversal() -> Result<()> {
        use zip::write::SimpleFileOptions;
        let directory = tempfile::tempdir()?;
        let archive = directory.path().join("release.zip");
        let mut writer = zip::ZipWriter::new(File::create(&archive)?);
        writer.start_file(
            format!("bin/{}", executable_name("pandoc")),
            SimpleFileOptions::default(),
        )?;
        writer.write_all(b"binary payload")?;
        writer.finish()?;
        let extracted = directory.path().join("extracted");
        extract_archive(&archive, &extracted)?;
        assert_eq!(
            fs::read(find_executable(&extracted, "pandoc")?)?,
            b"binary payload"
        );
        let mut writer = zip::ZipWriter::new(File::create(&archive)?);
        writer.start_file("../outside", SimpleFileOptions::default())?;
        writer.write_all(b"must not be written")?;
        writer.finish()?;
        assert!(extract_archive(&archive, &extracted).is_err());
        assert!(!directory.path().join("outside").exists());
        Ok(())
    }

    /// Keep update hints across offline refreshes and compare prereleases by PEP 440.
    #[test]
    fn offline_refresh_preserves_hint_and_throttles_retries() -> Result<()> {
        let directory = tempfile::tempdir()?;
        let path = directory.path().join("update.json");
        let previous = UpdateCache {
            latest_version: Some("1.0rc2".into()),
            checked_at: Some(1000.0),
            attempted_at: 1000.0,
        };
        let failed = refreshed_cache(Some(&previous), None, 5000.0);
        atomic_write(&path, &serde_json::to_vec(&failed)?)?;
        let cached = read_update_cache(&path).context("Missing valid cache")?;
        assert_eq!(
            cached_available_update("1.0rc1", Some(&cached)),
            Some("1.0rc2".into())
        );
        assert_eq!(cached_available_update("1.0", Some(&cached)), None);
        assert_eq!(cached.checked_at, Some(1000.0));
        assert!(!update_cache_needs_refresh(Some(&cached), 5899.0));
        assert!(update_cache_needs_refresh(Some(&cached), 5900.0));
        fs::write(
            &path,
            br#"{"latest_version":null,"checked_at":null,"attempted_at":true}"#,
        )?;
        assert!(read_update_cache(&path).is_none());
        Ok(())
    }
}
