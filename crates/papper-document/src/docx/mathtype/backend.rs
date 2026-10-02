//! Generate complete equation artifacts with versioned caches and portable fallbacks.

use super::{Binding, Equation, wmf_size};
use anyhow::{Context, Result, bail, ensure};
use papper_core::metadata::EffectiveMetadata;
use papper_core::resources::ResourcePaths;
use papper_platform::native::{NativeConverter, unhex};
use serde_json::{Value, json};
use sha2::{Digest, Sha256};
use std::io::Read;
use std::path::{Path, PathBuf};
use std::process::{Command, Stdio};
use std::time::{Duration, Instant};

const SET_DATA_FAILURE: &str = "由于 Exception.ToString() 失败，因此无法打印异常字符串";

/// Resolve selected options before generating any document artifacts.
struct Options {
    method: String,
    backend: String,
    font: String,
}

impl Options {
    /// Preserve the established backend aliases while rejecting unsupported values.
    fn read(effective: &EffectiveMetadata) -> Result<Self> {
        let method = match effective
            .pmt_settings
            .get_str("mathtypeConversionMethod")
            .unwrap_or("auto")
            .trim()
            .to_ascii_lowercase()
            .as_str()
        {
            "rust" | "mathtype-rust" | "mtef" => "rust",
            "rust-sdk" | "sdk" | "sdk-xform-ole" => "rust-sdk",
            "set-data" | "setdata" | "tex" | "tex-input" | "mathtype" | "ole" => "set-data",
            "auto" | "fallback" => "auto",
            "both" => "both",
            value => bail!("Unsupported MathType conversion method: {value}"),
        }
        .to_owned();
        let backend = effective
            .pmt_settings
            .get_str("mathtypeSvgBackend")
            .unwrap_or("typst")
            .trim()
            .to_ascii_lowercase();
        let backend = if backend == "typst-as-lib" {
            "typst".into()
        } else {
            backend
        };
        ensure!(
            matches!(backend.as_str(), "typst" | "ratex"),
            "Unsupported MathType SVG backend: {backend}"
        );
        let font = effective
            .pmt_settings
            .get_str("mathtypeTypstMathFont")
            .unwrap_or("XITS Math")
            .trim()
            .to_owned();
        ensure!(!font.is_empty(), "mathtypeTypstMathFont must not be blank");
        ensure!(
            method != "both" || cfg!(windows),
            "mathtypeConversionMethod=both is only supported on Windows"
        );
        Ok(Self {
            method,
            backend,
            font,
        })
    }
}

/// Own one loaded converter and backend health state across all document equations.
pub(super) struct Generator {
    options: Options,
    resources: ResourcePaths,
    converter: Option<NativeConverter>,
    helper: Option<PathBuf>,
    template: Option<String>,
    work: PathBuf,
    cache: PathBuf,
    native_digest: Option<String>,
    helper_digest: Option<String>,
    font_digest: Option<String>,
    source_digest: Option<String>,
    auto_sdk: bool,
    sdk_disabled: bool,
    sdk_failure_streak: usize,
    _temporary: Option<tempfile::TempDir>,
}

/// Find an installed resource before using the retained development source tree.
fn resource(resources: &ResourcePaths, relative: &str) -> Option<PathBuf> {
    [
        resources.root.join(relative),
        resources.root.join("src/pandoc_manuscript").join(relative),
    ]
    .into_iter()
    .find(|path| path.is_file())
}

/// Locate the unchanged helper executable; no Word application is created or controlled.
fn helper(resources: &ResourcePaths) -> Option<PathBuf> {
    resource(
        resources,
        "mathtype/ole_helper/bin/Release/net48/MathTypeOleHelper.exe",
    )
}

/// Check COM registration read-only before permitting automatic set-data attempts.
fn sdk_available(resources: &ResourcePaths) -> bool {
    if !cfg!(windows) || helper(resources).is_none() {
        return false;
    }
    let mut command = Command::new("reg.exe");
    command.args(["query", r"HKCR\Equation.DSMT4\CLSID", "/ve"]);
    hide(&mut command);
    command.output().is_ok_and(|output| output.status.success())
}

/// Confirm required backends while keeping auto mode usable without a MathType installation.
pub(super) fn check(resources: &ResourcePaths, effective: &EffectiveMetadata) -> Result<()> {
    let options = Options::read(effective)?;
    if matches!(options.method.as_str(), "rust-sdk" | "set-data" | "both") {
        ensure!(
            sdk_available(resources),
            "MathType SDK requires Windows, Equation.DSMT4 registration, and the installed C# helper"
        );
    }
    if options.method != "set-data" {
        NativeConverter::load(resources, "mathtype-rust")?;
    }
    Ok(())
}

impl Generator {
    /// Derive project-local work and cache directories and fingerprint executable inputs once.
    pub(super) fn new(
        resources: &ResourcePaths,
        effective: &EffectiveMetadata,
        project: &Path,
        work_dir: Option<&Path>,
    ) -> Result<Self> {
        let options = Options::read(effective)?;
        let helper = helper(resources);
        let template = resource(resources, "mathtype/Times+Symbol 12.eqp")
            .map(std::fs::read_to_string)
            .transpose()?;
        let native = library_path(resources);
        let state = papper_core::paths::project_state_dir(project)?;
        let temporary = if work_dir.is_none() {
            Some(
                tempfile::Builder::new()
                    .prefix("papper-mathtype-")
                    .tempdir()?,
            )
        } else {
            None
        };
        let work = work_dir
            .map(Path::to_path_buf)
            .unwrap_or_else(|| temporary.as_ref().unwrap().path().to_path_buf());
        let cache = state.join("cache/mathtype/native-v1");
        std::fs::create_dir_all(&work)?;
        let auto_sdk = options.method == "auto" && sdk_available(resources);
        let font_digest = if Path::new(&options.font).is_file() {
            digest_file(Path::new(&options.font))?
        } else {
            None
        };
        Ok(Self {
            options,
            resources: resources.clone(),
            converter: None,
            native_digest: native.as_deref().map(digest_file).transpose()?.flatten(),
            helper_digest: helper.as_deref().map(digest_file).transpose()?.flatten(),
            source_digest: source_digest(resources)?,
            helper,
            template,
            work,
            cache,
            font_digest,
            auto_sdk,
            sdk_disabled: false,
            sdk_failure_streak: 0,
            _temporary: temporary,
        })
    }

    /// Select valid backends per equation and disable the known repeated SDK diagnostic failure.
    pub(super) fn generate(&mut self, binding: &Binding, index: usize) -> Result<Equation> {
        let prefs = self.preferences(binding.size)?;
        let methods = match self.options.method.as_str() {
            "both" => vec!["rust", "set-data"],
            "auto" if self.auto_sdk && !self.sdk_disabled => vec!["set-data", "rust"],
            "auto" => vec!["rust"],
            method => vec![method],
        }
        .into_iter()
        .map(str::to_owned)
        .collect::<Vec<_>>();
        let mut successful_rust = None;
        let mut last_error = None;
        for method in methods {
            if method == "set-data" && self.sdk_disabled {
                continue;
            }
            match self.cached(binding, index, &method, prefs.as_deref()) {
                Ok(equation) => {
                    self.write_debug_parts(index, binding, &equation, &method, prefs.as_deref())?;
                    if method == "set-data" {
                        self.sdk_failure_streak = 0;
                    }
                    if self.options.method == "both" && method == "rust" {
                        successful_rust = Some(equation);
                        continue;
                    }
                    if let Some(rust) = &successful_rust
                        && rust.ole != equation.ole
                    {
                        eprintln!(
                            "[WARN] MathType Rust and set-data outputs differ for equation {index}; using set-data"
                        );
                    }
                    return Ok(equation);
                }
                Err(error) => {
                    if method == "set-data" {
                        if format!("{error:#}").contains(SET_DATA_FAILURE) {
                            self.sdk_failure_streak += 1;
                        } else {
                            self.sdk_failure_streak = 0;
                        }
                        if self.sdk_failure_streak >= 3 {
                            self.sdk_disabled = true;
                            eprintln!(
                                "[WARN] MathType set-data failed three consecutive times; using Rust for subsequent equations"
                            );
                        }
                    }
                    eprintln!("[WARN] MathType {method} failed for equation {index}: {error:#}");
                    last_error = Some(error);
                }
            }
        }
        successful_rust.ok_or_else(|| {
            last_error.unwrap_or_else(|| anyhow::anyhow!("No MathType backend is available"))
        })
    }

    /// Preserve reviewed TeX and artifacts only when the caller opts into persistent work.
    fn write_debug_parts(
        &mut self,
        index: usize,
        binding: &Binding,
        equation: &Equation,
        method: &str,
        prefs: Option<&Path>,
    ) -> Result<()> {
        if self._temporary.is_some() {
            return Ok(());
        }
        let prefix = if self.options.method == "both" && method == "rust" {
            format!("eq_{index:03}.rust")
        } else {
            format!("eq_{index:03}")
        };
        let payload = tex_payload(&binding.latex)?;
        let metadata = serde_json::to_vec(&equation.metadata)?;
        for (suffix, bytes) in [
            ("tex", payload.as_bytes()),
            ("ole.bin", equation.ole.as_slice()),
            ("wmf", equation.wmf.as_slice()),
            ("json", metadata.as_slice()),
        ] {
            papper_core::paths::atomic_write(&self.work.join(format!("{prefix}.{suffix}")), bytes)?;
        }
        if method != "set-data" {
            // Explicit work directories are for inspection, so also retain bare
            // MTEF even when a warm cache avoided the normal conversion request.
            if self.converter.is_none() {
                self.converter = Some(NativeConverter::load(&self.resources, "mathtype-rust")?);
            }
            let parts = self.converter.as_ref().unwrap().call(
                &json!({"latex":payload,"prefs_file":prefs.map(papper_core::paths::display_path)}),
            )?;
            let mtef = unhex(
                parts["mtef"]
                    .as_str()
                    .context("Native converter omitted MTEF")?,
            )?;
            papper_core::paths::atomic_write(&self.work.join(format!("{prefix}.mtef.bin")), &mtef)?;
        }
        Ok(())
    }

    /// Change only Full size so relative script, symbol, and spacing preferences remain intact.
    fn preferences(&self, size: Option<f64>) -> Result<Option<PathBuf>> {
        let (Some(size), Some(template)) = (size, &self.template) else {
            return Ok(None);
        };
        let size = (size * 2.0).round_ties_even() / 2.0;
        let formatted = decimal(size);
        let mut sizes = false;
        let mut updated = false;
        let mut lines = Vec::new();
        for line in template.lines() {
            let trimmed = line.trim();
            if trimmed.starts_with('[') && trimmed.ends_with(']') {
                sizes = trimmed.eq_ignore_ascii_case("[Sizes]");
            }
            if sizes && !updated && trimmed.starts_with("Full=") {
                lines.push(format!("Full={formatted} pt"));
                updated = true;
            } else {
                lines.push(line.into());
            }
        }
        ensure!(
            updated,
            "MathType preferences template has no [Sizes]/Full setting"
        );
        let output = self
            .work
            .join("prefs")
            .join(format!("full-{}pt.eqp", formatted.replace('.', "_")));
        papper_core::paths::write_if_changed(&output, (lines.join("\n") + "\n").as_bytes())?;
        Ok(Some(output))
    }

    /// Cache exact conversion inputs and verify all sidecars before reusing any artifact.
    fn cached(
        &mut self,
        binding: &Binding,
        index: usize,
        method: &str,
        prefs: Option<&Path>,
    ) -> Result<Equation> {
        let payload = tex_payload(&binding.latex)?;
        let native = method != "set-data";
        let portable = method == "rust";
        let key = digest(&serde_json::to_vec(&json!({
            "version": 1, "payload": payload, "font_size": binding.size,
            "prefs": prefs.map(digest_file).transpose()?.flatten(),
            "method": method, "native": if native { &self.native_digest } else { &None },
            "source": if native { &self.source_digest } else { &None },
            "helper": if portable { &None } else { &self.helper_digest },
            "backend": if portable { Some(&self.options.backend) } else { None },
            "style": if portable { Some(&binding.style) } else { None },
            "font": if portable && self.options.backend == "typst" { Some(&self.options.font) } else { None },
            "font_digest": if portable && self.options.backend == "typst" { &self.font_digest } else { &None },
        }))?);
        let folder = self.cache.join(&key[..2]).join(&key);
        if let Ok(mut equation) = restore(&folder) {
            validate(&equation)?;
            equation.cache_hit = true;
            return Ok(equation);
        }
        let equation = if method == "set-data" {
            self.sdk(binding, index, &payload, None, prefs)?
        } else {
            if self.converter.is_none() {
                self.converter = Some(NativeConverter::load(&self.resources, "mathtype-rust")?);
            }
            let converter = self.converter.as_ref().unwrap();
            let parts = converter.call(&json!({"latex": payload, "prefs_file": prefs.map(papper_core::paths::display_path)}))?;
            let ole = unhex(
                parts["ole"]
                    .as_str()
                    .context("Native converter omitted OLE")?,
            )?;
            if method == "rust-sdk" {
                let mtef = unhex(
                    parts["mtef"]
                        .as_str()
                        .context("Native converter omitted MTEF")?,
                )?;
                let mut equation = self.sdk(binding, index, &payload, Some(&mtef), prefs)?;
                equation.ole = ole;
                equation
            } else {
                let preview = converter.call(&json!({"operation":"render_wmf", "latex": payload, "svg_backend": self.options.backend, "math_style": binding.style, "font_size_pt": binding.size.unwrap_or(12.0), "math_font": self.options.font}))?;
                let wmf = unhex(
                    preview["wmf"]
                        .as_str()
                        .context("Native converter omitted WMF")?,
                )?;
                let metadata = serde_json::from_str(
                    preview["metadata_json"]
                        .as_str()
                        .context("Native converter omitted preview metadata")?,
                )?;
                Equation {
                    ole,
                    wmf,
                    metadata,
                    cache_hit: false,
                }
            }
        };
        validate(&equation)?;
        // Manifest is published last: readers reject incomplete or mixed concurrent cache writes.
        std::fs::create_dir_all(&folder)?;
        let metadata = serde_json::to_vec(&equation.metadata)?;
        for (name, bytes) in [
            ("equation.ole.bin", &equation.ole),
            ("preview.wmf", &equation.wmf),
            ("metadata.json", &metadata),
        ] {
            papper_core::paths::atomic_write(&folder.join(name), bytes)?;
        }
        let manifest = json!({"ole":digest(&equation.ole), "wmf":digest(&equation.wmf), "metadata":digest(&metadata)});
        papper_core::paths::atomic_write(
            &folder.join("manifest.json"),
            &serde_json::to_vec(&manifest)?,
        )?;
        Ok(equation)
    }

    /// Invoke the existing helper for TeX import or SDK MTEF previews, with owned-process timeout.
    fn sdk(
        &self,
        _binding: &Binding,
        index: usize,
        payload: &str,
        mtef: Option<&[u8]>,
        prefs: Option<&Path>,
    ) -> Result<Equation> {
        ensure!(cfg!(windows), "MathType SDK requires Windows");
        let helper = self
            .helper
            .as_ref()
            .context("MathType C# helper executable is missing")?;
        let directory = tempfile::Builder::new()
            .prefix(&format!("eq-{index}-"))
            .tempdir_in(&self.work)?;
        let input = directory.path().join(if mtef.is_some() {
            "input.mtef"
        } else {
            "input.tex"
        });
        std::fs::write(&input, mtef.unwrap_or(payload.as_bytes()))?;
        let ole = directory.path().join("equation.ole.bin");
        let wmf = directory.path().join("preview.wmf");
        let metadata = directory.path().join("metadata.json");
        let mut command = Command::new(helper);
        command
            .args([
                "--method",
                if mtef.is_some() {
                    "sdk-xform-ole"
                } else {
                    "set-data"
                },
                "--pre-verb",
                "2",
                "--format",
                if mtef.is_some() {
                    "MathType EF"
                } else {
                    "TeX Input Language"
                },
                "--input",
            ])
            .arg(&input)
            .arg("--output")
            .arg(&ole)
            .args(["--encoding", "utf16le", "--no-verb"])
            .arg("--preview-output")
            .arg(&wmf)
            .arg("--metadata-output")
            .arg(&metadata);
        if mtef.is_some() {
            command.arg("--binary");
        }
        if let Some(prefs) = prefs {
            command.arg("--prefs-file").arg(prefs);
        }
        run_helper(&mut command)?;
        Ok(Equation {
            ole: std::fs::read(&ole)?,
            wmf: std::fs::read(&wmf)?,
            metadata: if metadata.exists() {
                serde_json::from_slice(&std::fs::read(metadata)?)?
            } else {
                json!({})
            },
            cache_hit: false,
        })
    }
}

/// Identify the same compiled library selected by NativeConverter for cache invalidation.
fn library_path(resources: &ResourcePaths) -> Option<PathBuf> {
    let name = if cfg!(windows) {
        "mathtype_rust.dll"
    } else if cfg!(target_os = "macos") {
        "libmathtype_rust.dylib"
    } else {
        "libmathtype_rust.so"
    };
    [
        resources.root.join("mathtype/bin").join(name),
        resources
            .root
            .join("src/pandoc_manuscript/mathtype/bin")
            .join(name),
        resources
            .root
            .join("scripts/mathtype-rust/target/release")
            .join(name),
    ]
    .into_iter()
    .find(|path| path.is_file())
}

/// Hash retained native sources too, so development source changes cannot reuse stale artifacts.
fn source_digest(resources: &ResourcePaths) -> Result<Option<String>> {
    let root = resources.root.join("scripts/mathtype-rust");
    if !root.is_dir() {
        return Ok(None);
    }
    let mut files = Vec::new();
    collect_sources(&root.join("src"), &mut files)?;
    for name in ["Cargo.toml", "Cargo.lock"] {
        if root.join(name).is_file() {
            files.push(root.join(name));
        }
    }
    files.sort();
    let mut hash = Sha256::new();
    for file in files {
        hash.update(file.strip_prefix(&root)?.to_string_lossy().as_bytes());
        hash.update([0]);
        hash.update(std::fs::read(file)?);
        hash.update([0]);
    }
    Ok(Some(papper_platform::native::hex(&hash.finalize())))
}

/// Collect Rust implementation files deterministically without following directory symlinks.
fn collect_sources(directory: &Path, files: &mut Vec<PathBuf>) -> Result<()> {
    if !directory.is_dir() {
        return Ok(());
    }
    for item in std::fs::read_dir(directory)? {
        let item = item?;
        let kind = item.file_type()?;
        if kind.is_dir() {
            collect_sources(&item.path(), files)?;
        } else if kind.is_file() && item.path().extension().is_some_and(|value| value == "rs") {
            files.push(item.path());
        }
    }
    Ok(())
}

/// Hash bytes using a stable lowercase content digest.
fn digest(bytes: &[u8]) -> String {
    papper_platform::native::hex(&Sha256::digest(bytes))
}

/// Return no digest for absent optional resources, propagating unreadable-file errors.
fn digest_file(path: &Path) -> Result<Option<String>> {
    if !path.is_file() {
        return Ok(None);
    }
    Ok(Some(digest(&std::fs::read(path)?)))
}

/// Verify the cache publication manifest before trusting any sidecar's bytes.
fn restore(folder: &Path) -> Result<Equation> {
    let manifest: Value = serde_json::from_slice(&std::fs::read(folder.join("manifest.json"))?)?;
    let ole = std::fs::read(folder.join("equation.ole.bin"))?;
    let wmf = std::fs::read(folder.join("preview.wmf"))?;
    let metadata = std::fs::read(folder.join("metadata.json"))?;
    ensure!(
        manifest["ole"] == digest(&ole)
            && manifest["wmf"] == digest(&wmf)
            && manifest["metadata"] == digest(&metadata),
        "MathType cache artifact digest mismatch"
    );
    let equation = Equation {
        ole,
        wmf,
        metadata: serde_json::from_slice(&metadata)?,
        cache_hit: true,
    };
    validate(&equation)?;
    Ok(equation)
}

/// Reject incomplete OLE, malformed WMF, and mapping-free previews that export blank PDFs.
fn validate(equation: &Equation) -> Result<()> {
    ensure!(
        equation
            .ole
            .starts_with(&[0xd0, 0xcf, 0x11, 0xe0, 0xa1, 0xb1, 0x1a, 0xe1])
            && equation.ole.windows(4).any(|bytes| bytes == b"DSMT"),
        "Generated OLE lacks the MathType compound payload"
    );
    wmf_size(&equation.wmf)?;
    ensure!(
        equation.metadata.is_object(),
        "Equation metadata must be an object"
    );
    ensure!(equation.wmf.len() >= 40, "WMF record stream is missing");
    let mut position = 40;
    let mut origin = false;
    let mut extent = false;
    while position + 6 <= equation.wmf.len() {
        let words = u32::from_le_bytes(equation.wmf[position..position + 4].try_into()?) as usize;
        let function = u16::from_le_bytes(equation.wmf[position + 4..position + 6].try_into()?);
        ensure!(words >= 3, "WMF record has invalid size");
        let next = position
            .checked_add(words.checked_mul(2).context("WMF size overflow")?)
            .context("WMF offset overflow")?;
        ensure!(next <= equation.wmf.len(), "WMF record is truncated");
        if function == 0x020b {
            origin = true;
        }
        if function == 0x020c {
            extent = true;
        }
        if function == 0 {
            break;
        }
        position = next;
    }
    ensure!(origin && extent, "WMF preview lacks replay window mapping");
    Ok(())
}

/// Normalize AMS aligned to the MathType-supported align and preserve existing math delimiters.
fn tex_payload(raw: &str) -> Result<String> {
    let begin = regex::Regex::new(r"\\begin\s*\{\s*aligned\s*\}")?;
    let end = regex::Regex::new(r"\\end\s*\{\s*aligned\s*\}")?;
    let text = begin.replace_all(raw.trim(), r"\begin{align}");
    let text = end.replace_all(&text, r"\end{align}");
    if text.starts_with('$') && text.ends_with('$') {
        return Ok(text.into_owned());
    }
    if let Some(inner) = text
        .strip_prefix(r"\(")
        .and_then(|text| text.strip_suffix(r"\)"))
    {
        return Ok(format!("${}$", inner.trim()));
    }
    if let Some(inner) = text
        .strip_prefix(r"\[")
        .and_then(|text| text.strip_suffix(r"\]"))
    {
        return Ok(format!("$${}$$", inner.trim()));
    }
    Ok(format!("${text}$"))
}

/// Format half-point preference sizes without unnecessary trailing zeroes.
fn decimal(value: f64) -> String {
    format!("{value:.2}")
        .trim_end_matches('0')
        .trim_end_matches('.')
        .to_owned()
}

/// Prevent background helpers and registry probes from opening console windows on Windows.
fn hide(command: &mut Command) {
    #[cfg(windows)]
    {
        use std::os::windows::process::CommandExt;
        command.creation_flags(0x08000000);
    }
    #[cfg(not(windows))]
    {
        let _ = command;
    }
}

/// Capture helper diagnostics without pipe deadlocks and stop only this owned child on timeout.
fn run_helper(command: &mut Command) -> Result<()> {
    hide(command);
    command
        .stdin(Stdio::null())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped());
    let mut child = command.spawn().context("Cannot start MathType C# helper")?;
    let stdout = child.stdout.take().context("Missing helper stdout pipe")?;
    let stderr = child.stderr.take().context("Missing helper stderr pipe")?;
    let out = std::thread::spawn(move || capture(stdout));
    let err = std::thread::spawn(move || capture(stderr));
    let deadline = Instant::now() + Duration::from_secs(120);
    let status = loop {
        if let Some(status) = child.try_wait()? {
            break status;
        }
        if Instant::now() >= deadline {
            let _ = child.kill();
            let _ = child.wait();
            bail!("MathType helper timed out after 120 seconds");
        }
        std::thread::sleep(Duration::from_millis(20));
    };
    let stdout = out.join().unwrap_or_default();
    let stderr = err.join().unwrap_or_default();
    ensure!(
        status.success(),
        "MathType helper failed ({status}): {}; {}",
        decode(&stdout),
        decode(&stderr)
    );
    if !stderr.is_empty() {
        eprintln!("[WARN] {}", decode(&stderr).trim());
    }
    Ok(())
}

/// Preserve Chinese diagnostics from old helpers so repeated SDK failures remain detectable.
fn decode(bytes: &[u8]) -> String {
    if let Ok(text) = std::str::from_utf8(bytes) {
        return text.to_owned();
    }
    let (text, _, errors) = encoding_rs::GB18030.decode(bytes);
    if !errors {
        text.into_owned()
    } else {
        String::from_utf8_lossy(bytes).into_owned()
    }
}

/// Drain one process stream while retaining bounded diagnostic output.
fn capture(mut pipe: impl Read) -> Vec<u8> {
    let mut result = Vec::new();
    let mut buffer = [0; 4096];
    while let Ok(count) = pipe.read(&mut buffer) {
        if count == 0 {
            break;
        }
        let keep = count.min(65536_usize.saturating_sub(result.len()));
        result.extend_from_slice(&buffer[..keep]);
    }
    result
}
