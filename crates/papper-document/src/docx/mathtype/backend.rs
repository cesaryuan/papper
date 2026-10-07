//! Generate complete equation artifacts with versioned caches and portable fallbacks.

use super::{Binding, Equation, wmf_size};
use anyhow::{Context, Result, bail, ensure};
use papper_core::metadata::{
    ConversionMethod, EffectiveMetadata, MathFontConfig, SvgBackend as ConfigSvgBackend,
};
use papper_core::resources::ResourcePaths;
use papper_platform::native::{
    self, FormulaStyle, MathFontSelection, SvgBackend, WmfRenderOptions,
};
use serde_json::{Value, json};
use sha2::{Digest, Sha256};
use std::io::Read;
use std::path::{Path, PathBuf};
use std::process::{Command, Stdio};
use std::time::{Duration, Instant};

const SET_DATA_FAILURE: &str = "由于 Exception.ToString() 失败，因此无法打印异常字符串";

/// Resolve selected options before generating any document artifacts.
struct Options {
    method: ConversionMethod,
    backend: ConfigSvgBackend,
    font: MathFontConfig,
}

impl Options {
    /// Read validated configuration once and enforce the platform-specific SDK constraint.
    fn read(effective: &EffectiveMetadata) -> Result<Self> {
        let fields = effective.pmt_settings.fields();
        ensure!(
            fields.mathtype_conversion_method != ConversionMethod::Both || cfg!(windows),
            "mathtypeConversionMethod=both is only supported on Windows"
        );
        Ok(Self {
            method: fields.mathtype_conversion_method,
            backend: fields.mathtype_svg_backend,
            font: fields.mathtype_typst_math_font.clone(),
        })
    }
}

/// Keep equation options, SDK health, and output paths together across one document.
pub(super) struct Generator {
    options: Options,
    helper: Option<PathBuf>,
    template: Option<String>,
    work: PathBuf,
    cache: PathBuf,
    helper_digest: Option<String>,
    font_digest: Option<String>,
    calligraphic_font_digest: Option<String>,
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
    if matches!(
        options.method,
        ConversionMethod::RustSdk | ConversionMethod::SetData | ConversionMethod::Both
    ) {
        ensure!(
            sdk_available(resources),
            "MathType SDK requires Windows, Equation.DSMT4 registration, and the installed C# helper"
        );
    }
    Ok(())
}

impl Generator {
    /// Derive project-local cache/work directories and fingerprint optional SDK/font inputs once.
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
        // v2 separates compile-time engine identity from the former DLL/source cache keys.
        let cache = state.join("cache/mathtype/native-v2");
        std::fs::create_dir_all(&work)?;
        let auto_sdk = options.method == ConversionMethod::Auto && sdk_available(resources);
        let font_digest = if Path::new(&options.font.font).is_file() {
            digest_file(Path::new(&options.font.font))?
        } else {
            None
        };
        let calligraphic_font_digest = if options.font.calligraphic_font == options.font.font {
            font_digest.clone()
        } else if Path::new(&options.font.calligraphic_font).is_file() {
            digest_file(Path::new(&options.font.calligraphic_font))?
        } else {
            None
        };
        Ok(Self {
            options,
            helper_digest: helper.as_deref().map(digest_file).transpose()?.flatten(),
            helper,
            template,
            work,
            cache,
            font_digest,
            calligraphic_font_digest,
            auto_sdk,
            sdk_disabled: false,
            sdk_failure_streak: 0,
            _temporary: temporary,
        })
    }

    /// Select valid backends per equation and disable the known repeated SDK diagnostic failure.
    pub(super) fn generate(&mut self, binding: &Binding, index: usize) -> Result<Equation> {
        let prefs = self.preferences(binding.size)?;
        let requested = self.options.method;
        let methods: &[ConversionMethod] = match requested {
            ConversionMethod::Both => &[ConversionMethod::Rust, ConversionMethod::SetData],
            ConversionMethod::Auto if self.auto_sdk && !self.sdk_disabled => {
                &[ConversionMethod::SetData, ConversionMethod::Rust]
            }
            ConversionMethod::Auto => &[ConversionMethod::Rust],
            _ => std::slice::from_ref(&requested),
        };
        let mut successful_rust = None;
        let mut last_error = None;
        for &method in methods {
            if method == ConversionMethod::SetData && self.sdk_disabled {
                continue;
            }
            match self.cached(binding, index, method, prefs.as_deref()) {
                Ok(equation) => {
                    self.write_debug_parts(index, binding, &equation, method)?;
                    if method == ConversionMethod::SetData {
                        self.sdk_failure_streak = 0;
                    }
                    if requested == ConversionMethod::Both && method == ConversionMethod::Rust {
                        successful_rust = Some(equation);
                        continue;
                    }
                    if let Some(rust) = &successful_rust
                        && let Some(warning) = conversion_warning(index, &rust.ole, &equation.ole)
                    {
                        eprintln!("{warning}");
                    }
                    return Ok(equation);
                }
                Err(error) => {
                    if method == ConversionMethod::SetData {
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
                    eprintln!(
                        "[WARN] MathType {} failed for equation {index}: {error:#}",
                        method.as_str()
                    );
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
        &self,
        index: usize,
        binding: &Binding,
        equation: &Equation,
        method: ConversionMethod,
    ) -> Result<()> {
        if self._temporary.is_some() {
            return Ok(());
        }
        let prefix =
            if self.options.method == ConversionMethod::Both && method == ConversionMethod::Rust {
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
        if method != ConversionMethod::SetData {
            // Cached OLE already carries the exact MTEF; extracting it avoids
            // defeating a warm equation cache merely to write debug sidecars.
            let mtef = native::mtef_from_ole(&equation.ole)?;
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
        &self,
        binding: &Binding,
        index: usize,
        method: ConversionMethod,
        prefs: Option<&Path>,
    ) -> Result<Equation> {
        let payload = tex_payload(&binding.latex)?;
        let native = method != ConversionMethod::SetData;
        let portable = method == ConversionMethod::Rust;
        let key = digest(&serde_json::to_vec(&json!({
            "version": 2, "payload": payload, "font_size": binding.size,
            "prefs": prefs.map(digest_file).transpose()?.flatten(),
            "method": method.as_str(), "engine": if native { Some(native::EQUATION_ENGINE_FINGERPRINT) } else { None },
            "helper": if portable { &None } else { &self.helper_digest },
            "backend": if portable { Some(self.options.backend.as_str()) } else { None },
            "style": if portable { Some(&binding.style) } else { None },
            "font": if portable && self.options.backend == ConfigSvgBackend::Typst { Some(&self.options.font) } else { None },
            "font_digest": if portable && self.options.backend == ConfigSvgBackend::Typst { &self.font_digest } else { &None },
            "calligraphic_font_digest": if portable && self.options.backend == ConfigSvgBackend::Typst { &self.calligraphic_font_digest } else { &None },
        }))?);
        let folder = self.cache.join(&key[..2]).join(&key);
        if let Ok(mut equation) = restore(&folder) {
            validate(&equation)?;
            equation.cache_hit = true;
            return Ok(equation);
        }
        let equation = if method == ConversionMethod::SetData {
            self.sdk(index, &payload, None, prefs)?
        } else {
            let parts = native::encode_latex(&payload, prefs)?;
            if method == ConversionMethod::RustSdk {
                let mut equation = self.sdk(index, &payload, Some(&parts.mtef), prefs)?;
                equation.ole = parts.ole;
                equation
            } else {
                let preview = native::render_wmf(
                    &payload,
                    WmfRenderOptions {
                        svg_backend: match self.options.backend {
                            ConfigSvgBackend::Typst => SvgBackend::Typst,
                            ConfigSvgBackend::Ratex => SvgBackend::Ratex,
                        },
                        formula_style: FormulaStyle::parse(&binding.style)
                            .map_err(anyhow::Error::msg)?,
                        font_size_pt: binding.size.unwrap_or(12.0),
                    },
                    MathFontSelection {
                        font: &self.options.font.font,
                        calligraphic_font: &self.options.font.calligraphic_font,
                    },
                )?;
                let metadata = serde_json::from_slice(&preview.metadata_json)?;
                Equation {
                    ole: parts.ole,
                    wmf: preview.wmf,
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

/// Compare only MTEF: backend-specific OLE containers must not trigger mismatch warnings.
fn conversion_warning(index: usize, rust_ole: &[u8], set_data_ole: &[u8]) -> Option<String> {
    let rust = native::mtef_from_ole(rust_ole).context("Cannot read Rust OLE MTEF");
    let set_data = native::mtef_from_ole(set_data_ole).context("Cannot read set-data OLE MTEF");
    match (rust, set_data) {
        (Ok(rust), Ok(set_data)) if rust == set_data => None,
        (Ok(_), Ok(_)) => Some(format!(
            "[WARN] MathType Rust and set-data MTEF outputs differ for equation {index}; using set-data"
        )),
        // An unreadable comparison must not discard a successful set-data conversion.
        (Err(error), _) | (_, Err(error)) => Some(format!(
            "[WARN] MathType MTEF comparison failed for equation {index}: {error:#}; using set-data"
        )),
    }
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
    // Bug-fix: MathType preserves input newlines in its TeX source record,
    // while Rust canonicalizes delimited math to CRLF. Give both the same bytes
    // so multiline equations cannot report a source-only MTEF difference.
    let canonical = native::trim_latex_whitespace(raw)
        .replace("\r\n", "\n")
        .replace('\r', "\n")
        .replace('\n', "\r\n");
    let text = begin.replace_all(&canonical, r"\begin{align}");
    let text = end.replace_all(&text, r"\end{align}");
    if text.starts_with('$') && text.ends_with('$') {
        return Ok(text.into_owned());
    }
    if let Some(inner) = text
        .strip_prefix(r"\(")
        .and_then(|text| text.strip_suffix(r"\)"))
    {
        return Ok(format!("${}$", native::trim_latex_whitespace(inner)));
    }
    if let Some(inner) = text
        .strip_prefix(r"\[")
        .and_then(|text| text.strip_suffix(r"\]"))
    {
        return Ok(format!("$${}$$", native::trim_latex_whitespace(inner)));
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

#[cfg(test)]
mod tests {
    use super::*;
    use papper_core::metadata::PmtSettings;

    /// Catch the Rust migration regression while preserving warnings for actual equation differences.
    #[test]
    fn both_mode_warns_only_for_mtef_differences() -> Result<()> {
        let rust = native::encode_latex("$x+y$", None)?.ole;
        let mut set_data = rust.clone();
        // Change the CFB transaction signature, leaving the equation streams intact.
        set_data[52..56].copy_from_slice(&1_u32.to_le_bytes());
        assert_ne!(rust, set_data);
        assert!(
            conversion_warning(24, &rust, &set_data).is_none(),
            "OLE container differences must not produce a formula mismatch warning"
        );

        let different = native::encode_latex("$x-y$", None)?.ole;
        let warning = conversion_warning(24, &rust, &different)
            .context("Different equations must produce a mismatch warning")?;
        assert!(warning.contains("MTEF outputs differ for equation 24"));
        assert!(warning.contains("using set-data"));
        Ok(())
    }

    /// Report unreadable artifacts as comparison failures without failing a successful conversion.
    #[test]
    fn both_mode_reports_unreadable_mtef_as_a_nonfatal_warning() -> Result<()> {
        let valid = native::encode_latex("$x$", None)?.ole;
        for (rust, set_data, backend) in [
            (b"invalid OLE".as_slice(), valid.as_slice(), "Rust"),
            (valid.as_slice(), b"invalid OLE".as_slice(), "set-data"),
        ] {
            let warning = conversion_warning(24, rust, set_data)
                .context("Unreadable MTEF must produce a diagnostic")?;
            assert!(warning.contains("MTEF comparison failed for equation 24"));
            assert!(warning.contains(&format!("Cannot read {backend} OLE MTEF")));
            assert!(warning.contains("using set-data"));
        }
        Ok(())
    }

    /// Reject changed calligraphic files instead of serving a stale preview, then reuse restored bytes.
    #[test]
    fn calligraphic_font_changes_invalidate_document_cache() -> Result<()> {
        let project = tempfile::tempdir()?;
        let resources = ResourcePaths {
            root: project.path().join("resources"),
            pandoc: project.path().join("resources/pandoc"),
            template: project.path().join("resources/template"),
        };
        let original = std::fs::read(
            Path::new(env!("CARGO_MANIFEST_DIR"))
                .join("../../scripts/latex2wmf/assets/fonts/XITSMath-Regular.otf"),
        )?;
        let font_path = project.path().join("calligraphy.otf");
        std::fs::write(&font_path, &original)?;
        let effective = EffectiveMetadata {
            pmt_settings: PmtSettings::from_mapping(
                json!({
                    "mathtypeConversionMethod": "rust",
                    "mathtypeSvgBackend": "typst",
                    "mathtypeTypstMathFont": {
                        "font": "New Computer Modern Math",
                        "calligraphicFont": font_path.to_string_lossy(),
                    },
                })
                .as_object()
                .unwrap(),
            )?,
            pandoc_metadata: Default::default(),
            has_yaml_header: false,
        };
        let binding = Binding {
            latex: r"\mathcal{F}".into(),
            style: "inline".into(),
            marker: Vec::new(),
            math: Vec::new(),
            size: Some(12.0),
        };
        let first =
            Generator::new(&resources, &effective, project.path(), None)?.generate(&binding, 0)?;
        assert!(!first.cache_hit);
        std::fs::write(&font_path, b"invalid replacement font")?;
        assert!(
            Generator::new(&resources, &effective, project.path(), None)?
                .generate(&binding, 0)
                .is_err(),
            "a changed font file must be validated rather than hidden by a cache hit"
        );
        std::fs::write(&font_path, original)?;
        let restored =
            Generator::new(&resources, &effective, project.path(), None)?.generate(&binding, 0)?;
        assert!(restored.cache_hit);
        assert!(first.wmf == restored.wmf);
        Ok(())
    }

    /// Share caches for equivalent string/object values while separating different effective font pairs.
    #[test]
    fn font_override_changes_preview_and_keeps_separate_cache_entries() -> Result<()> {
        let project = tempfile::tempdir()?;
        let resources = ResourcePaths {
            root: project.path().join("resources"),
            pandoc: project.path().join("resources/pandoc"),
            template: project.path().join("resources/template"),
        };
        let binding = Binding {
            latex: r"\mathcal{F}_i+x".into(),
            style: "inline".into(),
            marker: Vec::new(),
            math: Vec::new(),
            size: Some(12.0),
        };
        let mut previews = Vec::new();
        let mixed = json!({"font": "XITS Math", "calligraphicFont": "New Computer Modern Math"});
        let single = json!({"font": "XITS Math", "calligraphicFont": "XITS Math"});
        let selections = [
            (None, false),
            (Some(json!("XITS Math")), false),
            (Some(mixed), true),
            (Some(single), true),
            (None, true),
        ];
        for (font, cache_hit) in selections {
            let mut values = json!({
                "mathtypeConversionMethod": "rust",
                "mathtypeSvgBackend": "typst",
            });
            if let Some(font) = font {
                values["mathtypeTypstMathFont"] = font;
            }
            let effective = EffectiveMetadata {
                pmt_settings: PmtSettings::from_mapping(values.as_object().unwrap())?,
                pandoc_metadata: Default::default(),
                has_yaml_header: false,
            };
            let mut generator = Generator::new(&resources, &effective, project.path(), None)?;
            let equation = generator.generate(&binding, 0)?;
            assert_eq!(equation.cache_hit, cache_hit);
            previews.push(equation);
        }
        assert!(
            previews[0].wmf != previews[1].wmf,
            "an explicit font must affect calligraphy"
        );
        assert!(
            previews[0].wmf == previews[2].wmf,
            "default cache must restore its original preview"
        );
        assert!(
            previews[1].wmf == previews[3].wmf,
            "override cache must restore its original preview"
        );
        assert_eq!(
            previews[0].metadata["renderer"]["calligraphic_font"],
            "New Computer Modern Math"
        );
        assert_eq!(
            previews[1].metadata["renderer"]["calligraphic_font"],
            "XITS Math"
        );
        assert_eq!(
            previews[0].ole, previews[1].ole,
            "preview fonts must not alter editable MTEF"
        );
        Ok(())
    }
}
