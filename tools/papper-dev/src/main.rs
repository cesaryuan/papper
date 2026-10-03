//! Build native PyPI wheels and stage their retained engines and authored resources.
//!
//! `cargo run -p papper-dev -- wheel --output dist` compiles the Rust CLI, existing
//! linked Rust equation crates, image helper, Haskell worker and Windows C# helper.
//! `--prebuilt` reuses explicitly selected native components for local verification.
//! Wheels contain native scripts and a shared runtime directory; no Python entry
//! point or application module is included. Python remains only a test oracle.

use anyhow::{Context, Result, bail};
use base64::{Engine, engine::general_purpose::URL_SAFE_NO_PAD};
use clap::{Args, Parser, Subcommand};
use papper_core::paths::display_path;
use sha2::{Digest, Sha256};
use std::collections::BTreeMap;
use std::io::{Read, Write};
use std::path::{Path, PathBuf};
use std::process::Command;
use zip::write::SimpleFileOptions;
mod portability;
mod smoke;

/// Keep build-time operations separate from the product's native command parser.
#[derive(Parser)]
struct Cli {
    #[command(subcommand)]
    command: DevCommand,
}

/// Native development operations with no package installation or publication.
#[derive(Subcommand)]
enum DevCommand {
    /// Build a Python-runtime-free platform wheel.
    Wheel(WheelArgs),
    /// Build a native development wheel, retaining the Python oracle only in dev.
    Editable(EditableArgs),
    /// Exercise an installed wheel using only public native commands.
    Smoke(SmokeArgs),
}

/// Place an editable native wheel in the build frontend's requested directory.
#[derive(Args)]
struct EditableArgs {
    #[arg(long)]
    output: PathBuf,
}

/// Select a local wheel for an isolated installation and complete output checks.
#[derive(Args)]
struct SmokeArgs {
    #[arg(long)]
    wheel: PathBuf,
}

/// Select source builds or existing compiled components for reproducible staging.
#[derive(Args)]
struct WheelArgs {
    #[arg(long, default_value = "dist")]
    output: PathBuf,
    #[arg(long)]
    prebuilt: bool,
    #[arg(long)]
    embed: bool,
    #[arg(long)]
    executable: Option<PathBuf>,
    #[arg(long)]
    worker: Option<PathBuf>,
    #[arg(long)]
    pdf_library: Option<PathBuf>,
    #[arg(long)]
    platform: Option<String>,
}

/// Report a build failure once and return a nonzero status to uv/CI.
fn main() {
    if let Err(error) = run() {
        eprintln!("[papper package] {error:#}");
        std::process::exit(1);
    }
}

/// Dispatch build operations after resolving the source checkout once.
fn run() -> Result<()> {
    match Cli::parse().command {
        DevCommand::Wheel(args) => build_wheel(&args),
        DevCommand::Editable(args) => build_editable(&args.output),
        DevCommand::Smoke(args) => smoke::run(&args.wheel),
    }
}

/// Resolve the checkout from the build tool, independently of the caller's cwd.
fn root() -> Result<PathBuf> {
    let path = Path::new(env!("CARGO_MANIFEST_DIR")).join("../..");
    Ok(PathBuf::from(display_path(&path.canonicalize()?)))
}

/// Match Cargo's shared target-directory semantics in local and CI builds.
fn target_dir(root: &Path) -> PathBuf {
    let target = std::env::var_os("CARGO_TARGET_DIR")
        .map(PathBuf::from)
        .unwrap_or_else(|| root.join("target"));
    if target.is_absolute() {
        target
    } else {
        root.join(target)
    }
}

/// Execute one native build and include its stderr in a useful build error.
fn execute(command: &mut Command, description: &str) -> Result<()> {
    println!("[papper package] {description}");
    let status = command
        .status()
        .with_context(|| format!("Could not run {description}"))?;
    anyhow::ensure!(status.success(), "{description} failed ({status})");
    Ok(())
}

/// Build the worker from the same source revision that is shipped in the wheel.
fn build_worker(root: &Path) -> Result<PathBuf> {
    let source = root.join("scripts/pandoc-server");
    let flags = [
        "exe:pmt-pandoc-worker",
        "--disable-executable-dynamic",
        "--disable-shared",
    ];
    execute(
        Command::new("cabal")
            .arg("build")
            .args(flags)
            .arg("--jobs=2")
            .current_dir(&source),
        "build retained Haskell worker",
    )?;
    let output = Command::new("cabal")
        .arg("list-bin")
        .args(flags)
        .arg("-v0")
        .current_dir(source)
        .output()?;
    anyhow::ensure!(
        output.status.success(),
        "Could not locate compiled Haskell worker: {}",
        String::from_utf8_lossy(&output.stderr)
    );
    let executable = PathBuf::from(String::from_utf8(output.stdout)?.trim());
    anyhow::ensure!(
        executable.is_file(),
        "Cabal did not create {}",
        executable.display()
    );
    Ok(executable)
}

/// Rebuild the retained C# SDK bridge without copying source/debug files into wheels.
fn build_csharp(root: &Path) -> Result<PathBuf> {
    let output = root.join(".pmt/native-wheel/MathTypeOleHelper");
    execute(
        Command::new("dotnet")
            .args(["build"])
            .arg(root.join("src/pandoc_manuscript/mathtype/ole_helper/MathTypeOleHelper.csproj"))
            .args(["-c", "Release", "-v:quiet", "--output"])
            .arg(&output)
            .current_dir(root),
        "build retained C# MathType helper",
    )?;
    let executable = output.join("MathTypeOleHelper.exe");
    anyhow::ensure!(
        executable.is_file(),
        "C# helper was not built: {}",
        executable.display()
    );
    Ok(executable)
}

/// Determine a native wheel platform tag; Linux CI must explicitly assert its baseline.
fn platform_tag() -> Result<String> {
    Ok(match (std::env::consts::OS, std::env::consts::ARCH) {
        ("windows", "x86_64") => "win_amd64".into(),
        ("windows", "aarch64") => "win_arm64".into(),
        ("macos", "aarch64") => "macosx_14_0_arm64".into(),
        ("macos", "x86_64") => "macosx_14_0_x86_64".into(),
        ("linux", "x86_64") => "linux_x86_64".into(),
        ("linux", "aarch64") => "linux_aarch64".into(),
        (system, architecture) => bail!("Unsupported wheel platform: {system}/{architecture}"),
    })
}

/// Reject generated artifacts and interpreter filters while keeping authored data.
fn excluded(path: &Path) -> bool {
    path.components().any(|component| {
        matches!(
            component.as_os_str().to_str(),
            Some(
                ".git"
                    | ".papper"
                    | ".pmt"
                    | ".pandoc-cache"
                    | ".venv"
                    | "__pycache__"
                    | "target"
                    | "output"
                    | "tmp"
                    | "work"
                    | "test"
            )
        )
    }) || path
        .extension()
        .is_some_and(|extension| extension == "py" || extension == "pyc")
}

/// Add regular authored files without following project-local symlinks or build outputs.
fn add_tree(files: &mut BTreeMap<String, PathBuf>, source: &Path, prefix: &str) -> Result<()> {
    if !source.exists() {
        return Ok(());
    }
    for entry in std::fs::read_dir(source)? {
        let entry = entry?;
        let kind = entry.file_type()?;
        let path = entry.path();
        // Inspect authored-relative entries only. A checkout under /tmp or
        // E:/work is valid and must not make every resource look generated.
        if excluded(Path::new(&entry.file_name())) || kind.is_symlink() {
            continue;
        }
        let name = format!("{prefix}/{}", entry.file_name().to_string_lossy());
        if kind.is_dir() {
            add_tree(files, &path, &name)?;
        } else if kind.is_file() {
            files.insert(name, path);
        }
    }
    Ok(())
}

/// Stage the worker's modified GPL sources and upstream notices beside its executable.
fn add_worker_sources(
    files: &mut BTreeMap<String, PathBuf>,
    root: &Path,
    prefix: &str,
) -> Result<()> {
    let source = root.join("scripts/pandoc-server");
    for name in [
        "Main.hs",
        "Papper/Citeproc.hs",
        "Papper/Locator.hs",
        "cabal.project",
        "pmt-pandoc-server.cabal",
        "README.md",
        "vendor/pandoc/COPYING.md",
        "vendor/pandoc/COPYRIGHT",
        "vendor/pandoc-cli/PandocCLI/Lua.hs",
        "vendor/pandoc-cli/PandocCLI/Server.hs",
    ] {
        let path = source.join(name);
        anyhow::ensure!(
            path.is_file(),
            "Worker source/notice is missing: {}",
            path.display()
        );
        files.insert(format!("{prefix}/bin/pandoc-worker-source/{name}"), path);
    }
    // Preserve the resolved provenance formerly written by the wheel hook;
    // exclude build-machine paths and record the actual Cabal source revision.
    let plan: serde_json::Value = serde_json::from_slice(
        &std::fs::read(source.join("dist-newstyle/cache/plan.json"))
            .context("Worker build plan is required to publish source provenance")?,
    )?;
    let mut packages = BTreeMap::new();
    for package in plan["install-plan"]
        .as_array()
        .context("Worker build plan has no packages")?
    {
        if package["pkg-src"]["type"] != "repo-tar" {
            continue;
        }
        let name = package["pkg-name"]
            .as_str()
            .context("Worker dependency has no name")?;
        let version = package["pkg-version"]
            .as_str()
            .context("Worker dependency has no version")?;
        let mut entry = serde_json::json!({
            "name":name, "version":version,
            "source":format!("https://hackage.haskell.org/package/{name}-{version}/{name}-{version}.tar.gz"),
            "revision":package["pkg-revision"], "flags":package["flags"]
        });
        for (input, output) in [
            ("pkg-src-sha256", "source_sha256"),
            ("pkg-cabal-sha256", "cabal_sha256"),
        ] {
            if let Some(value) = package.get(input) {
                entry[output] = value.clone();
            }
        }
        packages.insert((name.to_string(), version.to_string()), entry);
    }
    let manifest = serde_json::json!({
        "compiler":plan["compiler-id"], "cabal":plan["cabal-version"],
        "repository":"https://github.com/cesaryuan/papper/tree/main/scripts/pandoc-server",
        "packages":packages.into_values().collect::<Vec<_>>()
    });
    let mut bytes = serde_json::to_vec_pretty(&manifest)?;
    bytes.push(b'\n');
    let destination = root.join(".pmt/native-wheel/worker-source/SOURCES.json");
    papper_core::paths::write_if_changed(&destination, &bytes)?;
    files.insert(
        format!("{prefix}/bin/pandoc-worker-source/SOURCES.json"),
        destination,
    );
    Ok(())
}

/// Download pinned MuPDF native code from its published wheel without installing Python.
fn download_pdf_library(directory: &Path) -> Result<(PathBuf, Vec<PathBuf>)> {
    const VERSION: &str = "1.27.2.3";
    let metadata: serde_json::Value =
        ureq::get(&format!("https://pypi.org/pypi/PyMuPDF/{VERSION}/json"))
            .call()?
            .into_json()?;
    let architecture = std::env::consts::ARCH;
    let os = std::env::consts::OS;
    let filename_matches = |name: &str| {
        name.ends_with(".whl")
            && match (os, architecture) {
                ("windows", "x86_64") => name.ends_with("win_amd64.whl"),
                ("windows", "aarch64") => name.ends_with("win_arm64.whl"),
                ("macos", "aarch64") => {
                    name.contains("macosx")
                        && (name.ends_with("arm64.whl") || name.ends_with("universal2.whl"))
                }
                ("macos", "x86_64") => {
                    name.contains("macosx")
                        && (name.ends_with("x86_64.whl") || name.ends_with("universal2.whl"))
                }
                ("linux", "x86_64") => name.contains("manylinux") && name.ends_with("x86_64.whl"),
                ("linux", "aarch64") => name.contains("manylinux") && name.ends_with("aarch64.whl"),
                _ => false,
            }
    };
    let asset = metadata["urls"]
        .as_array()
        .context("MuPDF release has no artifacts")?
        .iter()
        .find(|asset| asset["filename"].as_str().is_some_and(filename_matches))
        .context("No compatible published MuPDF wheel")?;
    let mut bytes = Vec::new();
    ureq::get(asset["url"].as_str().context("MuPDF URL missing")?)
        .call()?
        .into_reader()
        .take(150 * 1024 * 1024)
        .read_to_end(&mut bytes)?;
    let actual = Sha256::digest(&bytes)
        .iter()
        .map(|byte| format!("{byte:02x}"))
        .collect::<String>();
    anyhow::ensure!(
        asset["digests"]["sha256"].as_str() == Some(actual.as_str()),
        "MuPDF download checksum mismatch"
    );
    let mut archive = zip::ZipArchive::new(std::io::Cursor::new(bytes))?;
    let mut library = None;
    let mut notices = Vec::new();
    for index in 0..archive.len() {
        let mut entry = archive.by_index(index)?;
        let filename = Path::new(entry.name())
            .file_name()
            .and_then(|name| name.to_str())
            .unwrap_or("")
            .to_string();
        let is_library = filename == "mupdfcpp64.dll"
            || filename.starts_with("libmupdf.so")
            || (filename.starts_with("libmupdf.")
                && filename.ends_with(".dylib")
                && !filename.contains("cpp"));
        let is_dependency =
            filename.ends_with(".dylib") || filename.contains(".so") || filename.ends_with(".dll");
        let is_notice = entry.name().contains("/licenses/")
            || filename == "COPYING"
            || filename == "COPYING.txt";
        if !entry.is_file() || (!is_dependency && !is_notice) {
            continue;
        }
        let destination = if is_dependency {
            directory.join(
                entry
                    .enclosed_name()
                    .context("MuPDF wheel path escaped extraction root")?,
            )
        } else {
            directory.join(&filename)
        };
        if let Some(parent) = destination.parent() {
            std::fs::create_dir_all(parent)?;
        }
        let mut file = std::fs::File::create(&destination)?;
        std::io::copy(&mut entry, &mut file)?;
        if is_library {
            library = Some(destination);
        } else if is_notice {
            notices.push(destination);
        }
    }
    anyhow::ensure!(
        !notices.is_empty(),
        "Published MuPDF wheel omitted license notices"
    );
    Ok((
        library.context("Published wheel omitted its standalone MuPDF library")?,
        notices,
    ))
}

/// Copy installed MuPDF notices only for a deliberately selected prebuilt library.
fn local_pdf_notices(library: &Path) -> Vec<PathBuf> {
    let Some(parent) = library.parent().and_then(Path::parent) else {
        return Vec::new();
    };
    let Ok(entries) = std::fs::read_dir(parent) else {
        return Vec::new();
    };
    let mut notices = Vec::new();
    for entry in entries.flatten() {
        if !entry
            .file_name()
            .to_string_lossy()
            .to_lowercase()
            .starts_with("pymupdf-")
        {
            continue;
        }
        let licenses = entry.path().join("licenses");
        let copying = entry.path().join("COPYING");
        if copying.is_file() {
            notices.push(copying);
        }
        if let Ok(files) = std::fs::read_dir(licenses) {
            notices.extend(
                files
                    .flatten()
                    .filter(|file| file.path().is_file())
                    .map(|file| file.path()),
            );
        }
    }
    notices
}

/// Write one wheel entry and its PEP 427 content digest without a Python packager.
fn append_entry(
    archive: &mut zip::ZipWriter<std::fs::File>,
    record: &mut Vec<String>,
    name: &str,
    bytes: &[u8],
    executable: bool,
) -> Result<()> {
    let options = SimpleFileOptions::default()
        .compression_method(zip::CompressionMethod::Deflated)
        .unix_permissions(if executable { 0o755 } else { 0o644 });
    archive.start_file(name, options)?;
    archive.write_all(bytes)?;
    let digest = URL_SAFE_NO_PAD.encode(Sha256::digest(bytes));
    record.push(format!("{name},sha256={digest},{}", bytes.len()));
    Ok(())
}

/// Install direct debug executables for uv run while exposing the frozen test oracle.
fn build_editable(output: &Path) -> Result<()> {
    let root = root()?;
    execute(
        Command::new("cargo")
            .args(["build", "--locked", "-p", "papper-cli"])
            .current_dir(&root),
        "build native development CLI",
    )?;
    // Build this independent helper with its own dependency features, as release
    // packaging does; unrelated equation features otherwise alter PNG compression.
    execute(
        Command::new("cargo")
            .args(["build", "--locked", "-p", "papper-svg"])
            .current_dir(&root),
        "build native development image helper",
    )?;
    let version = env!("CARGO_PKG_VERSION");
    let tag = format!("py3-none-{}", platform_tag()?);
    let info = format!("papper-{version}.dist-info");
    let data = format!("papper-{version}.data");
    std::fs::create_dir_all(output)?;
    let mut archive = zip::ZipWriter::new(std::fs::File::create(
        output.join(format!("papper-{version}-{tag}.whl")),
    )?);
    let mut record = Vec::new();
    let suffix = if cfg!(windows) { ".exe" } else { "" };
    for name in ["papper", "pmt"] {
        let executable = target_dir(&root).join(format!("debug/{name}{suffix}"));
        append_entry(
            &mut archive,
            &mut record,
            &format!("{data}/scripts/{name}{suffix}"),
            &std::fs::read(executable)?,
            true,
        )?;
    }
    let oracle = root.join("tests/legacy");
    append_entry(
        &mut archive,
        &mut record,
        "papper_test_oracle.pth",
        format!("{}\n", oracle.display()).as_bytes(),
        false,
    )?;
    append_entry(&mut archive, &mut record, &format!("{info}/METADATA"), format!("Metadata-Version: 2.4\nName: papper\nVersion: {version}\nRequires-Python: >=3.11\nSummary: Native Papper development installation\n").as_bytes(), false)?;
    append_entry(
        &mut archive,
        &mut record,
        &format!("{info}/WHEEL"),
        format!("Wheel-Version: 1.0\nGenerator: papper-dev\nRoot-Is-Purelib: false\nTag: {tag}\n")
            .as_bytes(),
        false,
    )?;
    record.push(format!("{info}/RECORD,,"));
    archive.start_file(format!("{info}/RECORD"), SimpleFileOptions::default())?;
    archive.write_all(record.join("\n").as_bytes())?;
    archive.finish()?;
    Ok(())
}

/// Assemble native launchers, data, notices and validated wheel metadata atomically.
fn build_wheel(args: &WheelArgs) -> Result<()> {
    let root = root()?;
    let target = target_dir(&root);
    let exe_suffix = if cfg!(windows) { ".exe" } else { "" };
    let executable = args
        .executable
        .clone()
        .unwrap_or_else(|| target.join(format!("release/papper{exe_suffix}")));
    let renderer = target.join(format!("release/papper-svg{exe_suffix}"));
    if !args.prebuilt {
        let mut build = Command::new("cargo");
        build
            .args(["build", "--locked", "--release", "-p", "papper-svg"])
            .current_dir(&root);
        if cfg!(windows) {
            let flags = std::env::var("RUSTFLAGS").unwrap_or_default();
            build.env(
                "RUSTFLAGS",
                format!("{flags} -C target-feature=+crt-static"),
            );
        }
        execute(&mut build, "build small native image helper")?;
    }
    let worker = if let Some(path) = &args.worker {
        path.clone()
    } else if args.prebuilt {
        let engine = papper_engine::discover_engine(&root)?;
        anyhow::ensure!(
            engine.embedded_crossref,
            "A release wheel requires the retained native worker, not ordinary Pandoc"
        );
        engine.executable
    } else {
        build_worker(&root)?
    };
    for path in [&renderer, &worker] {
        anyhow::ensure!(
            path.is_file(),
            "Native runtime component missing: {}",
            path.display()
        );
    }
    let temporary = tempfile::Builder::new().prefix("papper-wheel-").tempdir()?;
    let (pdf, pdf_notices) = if let Some(path) = args
        .pdf_library
        .clone()
        .or_else(|| std::env::var_os("PAPPER_MUPDF_LIBRARY").map(PathBuf::from))
    {
        let notices = local_pdf_notices(&path);
        anyhow::ensure!(
            !notices.is_empty(),
            "Selected MuPDF library has no accompanying notices; use its original wheel directory"
        );
        (path, notices)
    } else {
        download_pdf_library(temporary.path())?
    };
    let version = env!("CARGO_PKG_VERSION");
    let platform = args.platform.clone().unwrap_or(platform_tag()?);
    let tag = format!("py3-none-{platform}");
    let data = format!("papper-{version}.data");
    let info = format!("papper-{version}.dist-info");
    let prefix = format!("{data}/data/share/papper");
    let mut files = BTreeMap::new();
    add_tree(
        &mut files,
        &root.join("pandoc"),
        &format!("{prefix}/pandoc"),
    )?;
    add_tree(
        &mut files,
        &root.join("template"),
        &format!("{prefix}/template"),
    )?;
    add_worker_sources(&mut files, &root, &prefix)?;
    files.insert(
        format!("{prefix}/bin/pmt-pandoc-worker{exe_suffix}"),
        worker,
    );
    files.insert(format!("{prefix}/bin/papper-svg{exe_suffix}"), renderer);
    let pdf_name = if cfg!(windows) {
        "mupdfcpp64.dll"
    } else if cfg!(target_os = "macos") {
        "libmupdf.dylib"
    } else {
        "libmupdf.so"
    };
    files.insert(format!("{prefix}/bin/{pdf_name}"), pdf);
    for notice in pdf_notices {
        files.insert(
            format!(
                "{prefix}/bin/mupdf-notices/{}",
                notice.file_name().unwrap().to_string_lossy()
            ),
            notice,
        );
    }
    files.insert(
        format!("{prefix}/mathtype/Times+Symbol 12.eqp"),
        root.join("src/pandoc_manuscript/mathtype/Times+Symbol 12.eqp"),
    );
    for name in ["XITS-NOTICE.txt", "XITS-OFL.txt", "XITS-README.txt"] {
        files.insert(
            format!("{prefix}/mathtype/bin/{name}"),
            root.join("scripts/latex2wmf/assets/fonts").join(name),
        );
    }
    files.insert(
        format!("{prefix}/mathtype/bin/MITEX-APACHE-2.0.txt"),
        root.join("scripts/latex2wmf/assets/mitex/MITEX-APACHE-2.0.txt"),
    );
    if cfg!(windows) {
        let helper = if args.prebuilt {
            root.join(
                "src/pandoc_manuscript/mathtype/ole_helper/bin/Release/net48/MathTypeOleHelper.exe",
            )
        } else {
            build_csharp(&root)?
        };
        anyhow::ensure!(
            helper.is_file(),
            "Retained C# helper missing: {}",
            helper.display()
        );
        files.insert(
            format!("{prefix}/mathtype/ole_helper/bin/Release/net48/MathTypeOleHelper.exe"),
            helper,
        );
    }
    files.insert(format!("{info}/licenses/LICENSE"), root.join("LICENSE"));
    portability::prepare_runtime_libraries(
        &mut files,
        &prefix,
        &temporary.path().join("native-stage"),
    )?;
    if !args.prebuilt || args.embed {
        let runtime_archive = root.join(".pmt/native-wheel/runtime.zip");
        std::fs::create_dir_all(runtime_archive.parent().unwrap())?;
        let mut zip = zip::ZipWriter::new(std::io::Cursor::new(Vec::new()));
        for (name, path) in &files {
            let Some(relative) = name.strip_prefix(&format!("{prefix}/")) else {
                continue;
            };
            let executable = runtime_executable(relative);
            zip.start_file(
                relative,
                SimpleFileOptions::default()
                    .compression_method(zip::CompressionMethod::Deflated)
                    .unix_permissions(if executable { 0o755 } else { 0o644 }),
            )?;
            zip.write_all(&std::fs::read(path)?)?;
        }
        papper_core::paths::write_if_changed(&runtime_archive, &zip.finish()?.into_inner())?;
        let mut build = Command::new("cargo");
        build
            .args(["build", "--locked", "--release", "-p", "papper-cli"])
            .env("PAPPER_RUNTIME_ARCHIVE", runtime_archive)
            .current_dir(&root);
        if cfg!(windows) {
            // uv copies this executable outside its environment. A static CRT
            // keeps that entry point independent of separately packaged DLLs.
            let flags = std::env::var("RUSTFLAGS").unwrap_or_default();
            build.env(
                "RUSTFLAGS",
                format!("{flags} -C target-feature=+crt-static"),
            );
        }
        execute(&mut build, "build portable native Papper executables")?;
        // uv copies Windows tool executables outside their environment. Keeping
        // data in the embedded runtime makes that copied program self-contained.
        // Adapted sources/notices remain separately visible for distribution.
        files.retain(|name, _| {
            name.contains("/licenses/")
                || name.contains("/pandoc-worker-source/")
                || name.contains("/mupdf-notices/")
                || name.contains("/native-notices/")
                || name.ends_with("-NOTICE.txt")
                || name.ends_with("-OFL.txt")
                || name.ends_with("-README.txt")
                || name.ends_with("-APACHE-2.0.txt")
        });
    }
    anyhow::ensure!(
        executable.is_file(),
        "Native CLI is missing: {}",
        executable.display()
    );
    std::fs::create_dir_all(&args.output)?;
    let filename = format!("papper-{version}-{tag}.whl");
    let destination = args.output.join(filename);
    let unfinished = args.output.join("papper-wheel.tmp");
    let mut archive = zip::ZipWriter::new(std::fs::File::create(&unfinished)?);
    let mut record = Vec::new();
    for (name, path) in files {
        let bytes =
            std::fs::read(&path).with_context(|| format!("Cannot package {}", path.display()))?;
        let executable = runtime_executable(&name);
        append_entry(&mut archive, &mut record, &name, &bytes, executable)?;
    }
    let bytes = std::fs::read(&executable)?;
    append_entry(
        &mut archive,
        &mut record,
        &format!("{data}/scripts/papper{exe_suffix}"),
        &bytes,
        true,
    )?;
    let alias = executable.with_file_name(format!("pmt{exe_suffix}"));
    let alias_bytes = if alias.is_file() {
        std::fs::read(alias)?
    } else {
        bytes
    };
    append_entry(
        &mut archive,
        &mut record,
        &format!("{data}/scripts/pmt{exe_suffix}"),
        &alias_bytes,
        true,
    )?;
    let metadata = format!(
        "Metadata-Version: 2.4\nName: papper\nVersion: {version}\nRequires-Python: >=3.11\nSummary: Native academic manuscript build workflow\nLicense-Expression: MIT\nLicense-File: licenses/LICENSE\nProject-URL: Repository, https://github.com/cesaryuan/papper\nDescription-Content-Type: text/markdown\n\n{}",
        std::fs::read_to_string(root.join("README.md"))?
    );
    append_entry(
        &mut archive,
        &mut record,
        &format!("{info}/METADATA"),
        metadata.as_bytes(),
        false,
    )?;
    append_entry(&mut archive, &mut record, &format!("{info}/WHEEL"), format!("Wheel-Version: 1.0\nGenerator: papper-dev {version}\nRoot-Is-Purelib: false\nTag: {tag}\n").as_bytes(), false)?;
    record.push(format!("{info}/RECORD,,"));
    archive.start_file(format!("{info}/RECORD"), SimpleFileOptions::default())?;
    archive.write_all(record.join("\n").as_bytes())?;
    archive.finish()?;
    papper_core::paths::atomic_write(&destination, &std::fs::read(&unfinished)?)?;
    std::fs::remove_file(unfinished)?;
    println!("[papper package] Created {}", destination.display());
    Ok(())
}

/// Preserve helper execution permissions in both the wheel and its embedded archive.
fn runtime_executable(name: &str) -> bool {
    name.ends_with(".exe")
        || matches!(
            name.rsplit('/').next(),
            Some("pmt-pandoc-worker" | "papper-svg")
        )
}
