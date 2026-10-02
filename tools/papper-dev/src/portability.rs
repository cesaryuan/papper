//! Stage and repair native runtime dependencies before they enter the embedded archive.

use anyhow::{Context, Result, bail, ensure};
use serde_json::{Value, json};
use std::collections::{BTreeMap, BTreeSet, VecDeque};
use std::fs;
use std::path::{Component, Path, PathBuf};
use std::process::Command;

/// A copied runtime component whose original location still resolves build-time dependencies.
#[derive(Clone)]
struct NativeFile {
    key: String,
    original: PathBuf,
    staged: PathBuf,
}

/// Repair copied components and add distributable libraries without changing original build outputs.
pub fn prepare_runtime_libraries(
    files: &mut BTreeMap<String, PathBuf>,
    runtime_prefix: &str,
    stage: &Path,
) -> Result<()> {
    let prefix = format!("{}/", runtime_prefix.trim_end_matches('/'));
    let mut components = Vec::new();
    for (key, source) in files.iter() {
        let Some(relative) = key.strip_prefix(&prefix) else {
            continue;
        };
        if !is_native_file(relative) {
            continue;
        }
        ensure!(
            Path::new(relative)
                .components()
                .all(|part| matches!(part, Component::Normal(_))),
            "Unsafe native runtime path: {relative}"
        );
        let target = stage.join(relative);
        ensure!(
            !target.is_file() || source.canonicalize()? != target.canonicalize()?,
            "Native staging destination overlaps the original binary: {}",
            source.display()
        );
        fs::create_dir_all(
            target
                .parent()
                .context("Native runtime path has no parent")?,
        )?;
        copy_native_file(source, &target)
            .with_context(|| format!("Cannot stage native runtime {}", source.display()))?;
        components.push(NativeFile {
            key: key.clone(),
            original: source.clone(),
            staged: target,
        });
    }
    for component in &components {
        files.insert(component.key.clone(), component.staged.clone());
    }
    let mut report = Vec::new();
    if cfg!(windows) {
        prepare_windows(files, runtime_prefix, stage, &components, &mut report)?;
    } else if cfg!(target_os = "macos") {
        prepare_macos(files, runtime_prefix, stage, &components, &mut report)?;
    } else if cfg!(target_os = "linux") {
        prepare_linux(files, runtime_prefix, stage, &components, &mut report)?;
    } else {
        bail!(
            "Native runtime repair is unsupported on {}",
            std::env::consts::OS
        );
    }
    let report_path = stage.join("bin/native-notices/dependencies.json");
    fs::create_dir_all(report_path.parent().unwrap())?;
    fs::write(
        &report_path,
        serde_json::to_vec_pretty(&json!({"platform":std::env::consts::OS,"libraries":report}))?,
    )?;
    files.insert(
        format!("{runtime_prefix}/bin/native-notices/dependencies.json"),
        report_path,
    );
    Ok(())
}

/// Recognize runtime executables and versioned shared-library filenames, excluding notices.
fn is_native_file(name: &str) -> bool {
    name.ends_with(".exe")
        || name.ends_with(".dll")
        || name.ends_with(".dylib")
        || name.ends_with(".so")
        || name.contains(".so.")
        || name.ends_with("/pmt-pandoc-worker")
}

/// Make only staged Unix copies owner-writable because packaged bottles can ship read-only binaries.
fn copy_native_file(source: &Path, target: &Path) -> Result<()> {
    fs::copy(source, target)?;
    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        let mode = fs::metadata(target)?.permissions().mode();
        fs::set_permissions(target, fs::Permissions::from_mode(mode | 0o200))?;
    }
    Ok(())
}

/// Capture dependency inspection or repair output and fail the wheel on native command errors.
fn output(command: &mut Command) -> Result<String> {
    let description = format!("{command:?}");
    let result = command
        .output()
        .with_context(|| format!("Cannot run native dependency command {description}"))?;
    ensure!(
        result.status.success(),
        "Native dependency command failed: {description}\n{}",
        String::from_utf8_lossy(&result.stderr)
    );
    Ok(String::from_utf8_lossy(&result.stdout).into_owned())
}

/// Calculate loader-relative paths from staged file locations without absolute build-machine paths.
fn relative_to(directory: &Path, target: &Path) -> Result<String> {
    let source: Vec<_> = directory.components().collect();
    let target: Vec<_> = target.components().collect();
    let shared = source
        .iter()
        .zip(&target)
        .take_while(|(left, right)| left == right)
        .count();
    ensure!(shared > 0, "Native staging paths do not share a root");
    let mut relative = PathBuf::new();
    for _ in shared..source.len() {
        relative.push("..");
    }
    for component in &target[shared..] {
        relative.push(component.as_os_str());
    }
    Ok(relative.to_string_lossy().replace('\\', "/"))
}

/// Insert a copied dependency, refusing basename collisions between distinct native libraries.
fn stage_dependency(
    files: &mut BTreeMap<String, PathBuf>,
    prefix: &str,
    stage: &Path,
    source: &Path,
    name: &str,
) -> Result<NativeFile> {
    ensure!(
        Path::new(name).components().count() == 1
            && matches!(
                Path::new(name).components().next(),
                Some(Component::Normal(_))
            )
            && !name.contains(['/', '\\']),
        "Invalid native dependency name: {name}"
    );
    let relative = format!("bin/native-libs/{name}");
    let key = format!("{prefix}/{relative}");
    let target = stage.join(relative);
    if target.is_file() {
        ensure!(
            fs::read(&target)? == fs::read(source)?,
            "Conflicting native dependencies named {name}"
        );
    } else {
        fs::create_dir_all(target.parent().unwrap())?;
        copy_native_file(source, &target)?;
    }
    files.insert(key.clone(), target.clone());
    Ok(NativeFile {
        key,
        original: source.into(),
        staged: target,
    })
}

/// Read a little-endian field while rejecting malformed PE headers before indexing them.
fn pe_u32(bytes: &[u8], offset: usize) -> Result<u32> {
    Ok(u32::from_le_bytes(
        bytes
            .get(offset..offset + 4)
            .context("Truncated PE header")?
            .try_into()?,
    ))
}

/// Read a PE32+ image base for older delay-import tables that store virtual addresses.
fn pe_u64(bytes: &[u8], offset: usize) -> Result<u64> {
    Ok(u64::from_le_bytes(
        bytes
            .get(offset..offset + 8)
            .context("Truncated PE image base")?
            .try_into()?,
    ))
}

/// Resolve one PE virtual address using its section's on-disk byte range.
fn pe_offset(bytes: &[u8], sections: usize, count: usize, rva: u32) -> Result<usize> {
    for index in 0..count {
        let section = sections + index * 40;
        let address = pe_u32(bytes, section + 12)?;
        let size = pe_u32(bytes, section + 16)?;
        if rva >= address && rva - address < size {
            return Ok((pe_u32(bytes, section + 20)? + rva - address) as usize);
        }
    }
    bail!("PE import address is outside mapped sections")
}

/// Inspect PE imports directly so wheel building does not require a developer-prompt PATH.
fn pe_dependencies(path: &Path) -> Result<Vec<String>> {
    let bytes = fs::read(path)?;
    ensure!(
        bytes.starts_with(b"MZ"),
        "Expected PE runtime: {}",
        path.display()
    );
    let header = pe_u32(&bytes, 0x3c)? as usize;
    ensure!(
        bytes.get(header..header + 4) == Some(b"PE\0\0"),
        "Invalid PE signature"
    );
    let count = u16::from_le_bytes(
        bytes
            .get(header + 6..header + 8)
            .context("Truncated PE header")?
            .try_into()?,
    ) as usize;
    let optional_size = u16::from_le_bytes(
        bytes
            .get(header + 20..header + 22)
            .context("Truncated PE header")?
            .try_into()?,
    ) as usize;
    let optional = header + 24;
    let magic = u16::from_le_bytes(
        bytes
            .get(optional..optional + 2)
            .context("Truncated PE optional header")?
            .try_into()?,
    );
    let (directories, directory_count, image_base) = match magic {
        0x10b => (
            optional + 96,
            pe_u32(&bytes, optional + 92)?,
            u64::from(pe_u32(&bytes, optional + 28)?),
        ),
        0x20b => (
            optional + 112,
            pe_u32(&bytes, optional + 108)?,
            pe_u64(&bytes, optional + 24)?,
        ),
        _ => bail!("Unsupported PE optional header"),
    };
    let sections = optional + optional_size;
    let mut dependencies = Vec::new();
    for (index, size, name_field) in [(1u32, 20, 12), (13, 32, 4)] {
        if directory_count <= index {
            continue;
        }
        let imports = pe_u32(&bytes, directories + index as usize * 8)?;
        if imports == 0 {
            continue;
        }
        let mut descriptor = pe_offset(&bytes, sections, count, imports)?;
        loop {
            let mut name = pe_u32(&bytes, descriptor + name_field)?;
            if name == 0 {
                break;
            }
            // The legacy delay-load descriptor used virtual addresses; modern
            // linkers set bit 0 and store RVAs like the normal import directory.
            if index == 13 && pe_u32(&bytes, descriptor)? & 1 == 0 {
                name = u32::try_from(
                    u64::from(name)
                        .checked_sub(image_base)
                        .context("Invalid PE delay-import address")?,
                )?;
            }
            let offset = pe_offset(&bytes, sections, count, name)?;
            let raw = bytes.get(offset..).context("Invalid PE import name")?;
            let length = raw
                .iter()
                .position(|byte| *byte == 0)
                .context("Unterminated PE import name")?;
            dependencies.push(std::str::from_utf8(&raw[..length])?.to_string());
            descriptor += size;
            ensure!(
                dependencies.len() < 1024,
                "PE import count exceeds supported runtime limits"
            );
        }
    }
    dependencies.sort();
    dependencies.dedup();
    Ok(dependencies)
}

/// Find installed Visual Studio distributions without reading or caching private Git credentials.
fn visual_studio_roots() -> Vec<PathBuf> {
    let mut roots = Vec::new();
    if let Some(root) = std::env::var_os("VSINSTALLDIR") {
        roots.push(PathBuf::from(root));
    }
    for variable in ["ProgramFiles", "ProgramFiles(x86)"] {
        let Some(root) = std::env::var_os(variable) else {
            continue;
        };
        let base = PathBuf::from(root).join("Microsoft Visual Studio");
        let Ok(years) = fs::read_dir(&base) else {
            continue;
        };
        for year in years.flatten().filter(|entry| entry.path().is_dir()) {
            let Ok(editions) = fs::read_dir(year.path()) else {
                continue;
            };
            for edition in editions
                .flatten()
                .filter(|entry| entry.path().join("VC/Redist/MSVC").is_dir())
            {
                roots.push(edition.path());
            }
        }
    }
    roots.sort();
    roots.dedup();
    roots
}

/// Select an official release CRT distribution; debug/runtime files from arbitrary applications are rejected.
fn windows_redist() -> Result<(PathBuf, Option<PathBuf>)> {
    if let Some(root) = std::env::var_os("PAPPER_MSVC_REDIST_DIR") {
        let root = PathBuf::from(root);
        ensure!(
            root.join("msvcp140.dll").is_file() && root.join("vcruntime140_1.dll").is_file(),
            "PAPPER_MSVC_REDIST_DIR must identify the official release CRT directory"
        );
        return Ok((root, None));
    }
    let architecture = if std::env::consts::ARCH == "aarch64" {
        "arm64"
    } else {
        "x64"
    };
    let mut candidates = Vec::new();
    for root in visual_studio_roots() {
        let Ok(versions) = fs::read_dir(root.join("VC/Redist/MSVC")) else {
            continue;
        };
        for version in versions.flatten() {
            let name = version.file_name().to_string_lossy().to_string();
            let Ok(numbers) = name
                .split('.')
                .map(str::parse::<u32>)
                .collect::<std::result::Result<Vec<_>, _>>()
            else {
                continue;
            };
            let Ok(directories) = fs::read_dir(version.path().join(architecture)) else {
                continue;
            };
            for directory in directories.flatten() {
                let path = directory.path();
                if directory.file_name().to_string_lossy().ends_with(".CRT")
                    && path.join("msvcp140.dll").is_file()
                {
                    candidates.push((numbers.clone(), path, root.clone()));
                }
            }
        }
    }
    let (_,directory,root) = candidates.into_iter().max_by_key(|candidate|candidate.0.clone()).context("Official Visual C++ Redistributable CRT not found; install Visual Studio C++ tools or set PAPPER_MSVC_REDIST_DIR")?;
    let notice = ["Licenses/1033/Redist.txt", "Licenses/2052/Redist.txt"]
        .into_iter()
        .map(|relative| root.join(relative))
        .find(|path| path.is_file());
    Ok((directory, notice))
}

/// Distinguish Windows OS/API-set DLLs from application-local redistributables.
fn windows_system_library(name: &str) -> bool {
    let name = name.to_ascii_lowercase();
    name.starts_with("api-ms-win-")
        || name.starts_with("ext-ms-win-")
        || matches!(
            name.as_str(),
            "kernel32.dll"
                | "kernelbase.dll"
                | "ntdll.dll"
                | "user32.dll"
                | "gdi32.dll"
                | "gdi32full.dll"
                | "shell32.dll"
                | "shlwapi.dll"
                | "advapi32.dll"
                | "secur32.dll"
                | "bcrypt.dll"
                | "bcryptprimitives.dll"
                | "crypt32.dll"
                | "ws2_32.dll"
                | "winmm.dll"
                | "ole32.dll"
                | "oleaut32.dll"
                | "combase.dll"
                | "rpcrt4.dll"
                | "dbghelp.dll"
                | "version.dll"
                | "msvcrt.dll"
                | "ucrtbase.dll"
                | "mscoree.dll"
                | "imm32.dll"
                | "dwmapi.dll"
                | "setupapi.dll"
                | "cfgmgr32.dll"
                | "psapi.dll"
                | "iphlpapi.dll"
                | "winhttp.dll"
                | "wininet.dll"
                | "normaliz.dll"
                | "dnsapi.dll"
                | "netapi32.dll"
                | "userenv.dll"
                | "powrprof.dll"
                | "wtsapi32.dll"
                | "usp10.dll"
                | "winspool.drv"
        )
}

/// Bundle official VC release runtimes beside both PDF and formula libraries and audit every PE import.
fn prepare_windows(
    files: &mut BTreeMap<String, PathBuf>,
    prefix: &str,
    stage: &Path,
    components: &[NativeFile],
    report: &mut Vec<Value>,
) -> Result<()> {
    let (redist, redist_notice) = windows_redist()?;
    let mut crt = BTreeMap::new();
    for entry in fs::read_dir(&redist)? {
        let entry = entry?;
        if entry
            .path()
            .extension()
            .is_some_and(|extension| extension.eq_ignore_ascii_case("dll"))
        {
            crt.insert(
                entry.file_name().to_string_lossy().to_ascii_lowercase(),
                entry.path(),
            );
        }
    }
    let mut needed = BTreeSet::from([
        "msvcp140.dll".into(),
        "vcruntime140.dll".into(),
        "vcruntime140_1.dll".into(),
    ]);
    let mut pending: VecDeque<PathBuf> = components
        .iter()
        .map(|component| component.original.clone())
        .collect();
    let mut inspected = BTreeSet::new();
    while let Some(path) = pending.pop_front() {
        if !inspected.insert(path.clone()) {
            continue;
        }
        for dependency in pe_dependencies(&path)? {
            let lower = dependency.to_ascii_lowercase();
            if windows_system_library(&lower) {
                continue;
            }
            if let Some(runtime) = crt.get(&lower) {
                needed.insert(lower);
                pending.push_back(runtime.clone());
            } else if let Some(component) = components.iter().find(|component| {
                component
                    .original
                    .file_name()
                    .is_some_and(|name| name.to_string_lossy().eq_ignore_ascii_case(&dependency))
            }) {
                pending.push_back(component.original.clone());
            } else {
                bail!(
                    "Unbundled Windows dependency {dependency} required by {}",
                    path.display()
                );
            }
        }
    }
    for name in needed {
        let source = crt
            .get(&name)
            .with_context(|| format!("Official CRT distribution is missing {name}"))?;
        for directory in ["bin", "mathtype/bin"] {
            let target = stage.join(directory).join(&name);
            fs::create_dir_all(target.parent().unwrap())?;
            fs::copy(source, &target)?;
            files.insert(format!("{prefix}/{directory}/{name}"), target);
        }
        report.push(json!({"name":name,"source":source,"distribution":"Microsoft Visual C++ release redistributable","destinations":["bin","mathtype/bin"]}));
    }
    let notice = stage.join("bin/native-notices/MICROSOFT-VC-RUNTIME-NOTICE.txt");
    fs::create_dir_all(notice.parent().unwrap())?;
    fs::write(
        &notice,
        "Microsoft Visual C++ Runtime\nCopyright Microsoft Corporation\n\nUnmodified release CRT DLLs are supplied from the official installed Visual Studio VC/Redist/MSVC distribution. Redistribution is subject to the Visual Studio license and requires a licensed distributor. Debug runtimes are excluded.\n\nOfficial redistribution list:\nhttps://learn.microsoft.com/en-us/visualstudio/releases/2022/redistribution\nRedistribution guidance:\nhttps://learn.microsoft.com/en-us/cpp/windows/redistributing-visual-cpp-files\nLicense terms:\nhttps://visualstudio.microsoft.com/license-terms/\n",
    )?;
    files.insert(
        format!("{prefix}/bin/native-notices/MICROSOFT-VC-RUNTIME-NOTICE.txt"),
        notice,
    );
    if let Some(source) = redist_notice {
        let destination = stage.join("bin/native-notices/MICROSOFT-REDIST.txt");
        fs::copy(source, &destination)?;
        files.insert(
            format!("{prefix}/bin/native-notices/MICROSOFT-REDIST.txt"),
            destination,
        );
    }
    Ok(())
}

/// Preserve operating-system ELF dependencies allowed by the manylinux platform contract.
fn linux_system_library(name: &str) -> bool {
    matches!(
        name,
        "libc.so.6"
            | "libm.so.6"
            | "libdl.so.2"
            | "librt.so.1"
            | "libpthread.so.0"
            | "libutil.so.1"
            | "libresolv.so.2"
            | "libnsl.so.1"
            | "libgcc_s.so.1"
            | "libstdc++.so.6"
            | "libX11.so.6"
            | "libXext.so.6"
            | "libXrender.so.1"
            | "libICE.so.6"
            | "libSM.so.6"
            | "libGL.so.1"
            | "libglib-2.0.so.0"
            | "libgobject-2.0.so.0"
            | "libgthread-2.0.so.0"
            | "libz.so.1"
            | "libexpat.so.1"
            | "libatomic.so.1"
            | "libmvec.so.1"
            | "libanl.so.1"
    ) || name.starts_with("ld-linux-")
        || name == "linux-vdso.so.1"
}

/// Parse actual ELF dependency resolution and reject libraries unavailable on the build platform.
fn linux_dependencies(path: &Path) -> Result<Vec<(String, PathBuf)>> {
    let text = output(Command::new("ldd").arg(path))?;
    let mut dependencies = Vec::new();
    for line in text.lines().map(str::trim) {
        if line.contains("not found") {
            bail!("Unresolved ELF dependency in {}: {line}", path.display());
        }
        let Some((name, target)) = line.split_once(" => ") else {
            continue;
        };
        let target = target.split_whitespace().next().unwrap_or("");
        if target.starts_with('/') {
            dependencies.push((name.into(), PathBuf::from(target)));
        }
    }
    Ok(dependencies)
}

/// Repair each ELF RUNPATH to the archive-local library directory before embedding.
fn prepare_linux(
    files: &mut BTreeMap<String, PathBuf>,
    prefix: &str,
    stage: &Path,
    components: &[NativeFile],
    report: &mut Vec<Value>,
) -> Result<()> {
    let mut pending: VecDeque<_> = components.iter().cloned().collect();
    let mut inspected = BTreeSet::new();
    let mut staged = components.to_vec();
    while let Some(component) = pending.pop_front() {
        if !inspected.insert(component.original.clone()) {
            continue;
        }
        for (dependency, source) in linux_dependencies(&component.original)? {
            let name = Path::new(&dependency)
                .file_name()
                .context("ELF dependency has no filename")?
                .to_string_lossy()
                .to_string();
            if linux_system_library(&name) {
                continue;
            }
            // Preserve the actual SONAME even when the primary component is
            // packaged under a generic filename such as libmupdf.so.
            let library = stage_dependency(files, prefix, stage, &source, &name)?;
            if !staged.iter().any(|item| item.staged == library.staged) {
                report.push(json!({"name":name,"source":source,"destination":library.key}));
                add_dependency_notices(files, prefix, stage, &source, &name)?;
                pending.push_back(library.clone());
                staged.push(library);
            }
        }
    }
    for component in &staged {
        // Absolute DT_NEEDED entries bypass RUNPATH and otherwise retain the
        // build-machine path even after a successful RPATH rewrite.
        for dependency in output(
            Command::new("patchelf")
                .arg("--print-needed")
                .arg(&component.staged),
        )?
        .lines()
        {
            if dependency.contains('/') {
                let name = Path::new(dependency)
                    .file_name()
                    .context("ELF dependency has no basename")?
                    .to_string_lossy();
                output(
                    Command::new("patchelf")
                        .args(["--replace-needed", dependency, &name])
                        .arg(&component.staged),
                )?;
            }
        }
        let relative = relative_to(
            component.staged.parent().unwrap(),
            &stage.join("bin/native-libs"),
        )?;
        let rpath = if relative.is_empty() {
            "$ORIGIN".into()
        } else {
            format!("$ORIGIN:$ORIGIN/{relative}")
        };
        output(
            Command::new("patchelf")
                .args(["--set-rpath", &rpath])
                .arg(&component.staged),
        )?;
        let actual = output(
            Command::new("patchelf")
                .arg("--print-rpath")
                .arg(&component.staged),
        )?;
        ensure!(
            actual.trim() == rpath,
            "ELF runtime repair did not preserve the requested loader-relative path"
        );
    }
    for component in &staged {
        for (name, path) in linux_dependencies(&component.staged)? {
            ensure!(
                linux_system_library(&name) || path.starts_with(stage),
                "ELF dependency still resolves outside packaged runtime: {name} -> {}",
                path.display()
            );
        }
    }
    Ok(())
}

/// Inspect Mach-O install names while discarding otool's current-image heading.
fn macos_dependencies(path: &Path) -> Result<Vec<String>> {
    Ok(output(Command::new("otool").arg("-L").arg(path))?
        .lines()
        .skip(1)
        .filter_map(|line| {
            line.trim()
                .split_once(" (compatibility version")
                .map(|(name, _)| name.into())
        })
        .collect())
}

/// Read LC_RPATH entries needed to resolve a source binary's original @rpath references.
fn macos_rpaths(path: &Path) -> Result<Vec<String>> {
    let text = output(Command::new("otool").arg("-l").arg(path))?;
    let mut paths = Vec::new();
    let mut rpath = false;
    for line in text.lines().map(str::trim) {
        if line == "cmd LC_RPATH" {
            rpath = true;
        } else if rpath && line.starts_with("path ") {
            paths.push(
                line.trim_start_matches("path ")
                    .split(" (offset")
                    .next()
                    .unwrap_or("")
                    .into(),
            );
            rpath = false;
        } else if line.starts_with("Load command ") {
            rpath = false;
        }
    }
    // Universal Mach-O files repeat load commands for each architecture;
    // install_name_tool applies one deletion to all slices at once.
    paths.sort();
    paths.dedup();
    Ok(paths)
}

/// Identify dependencies supplied by macOS itself rather than Homebrew or a build checkout.
fn macos_system_library(name: &str) -> bool {
    name.starts_with("/usr/lib/") || name.starts_with("/System/Library/")
}

/// Resolve a source Mach-O loader token using its source location and retained executable rpaths.
fn resolve_macos_dependency(
    name: &str,
    source: &Path,
    components: &[NativeFile],
) -> Result<PathBuf> {
    let parent = source.parent().context("Mach-O runtime has no parent")?;
    let executable = components
        .iter()
        .find(|component| component.key.ends_with("/pmt-pandoc-worker"))
        .map(|component| component.original.parent().unwrap())
        .unwrap_or(parent);
    let expand = |raw: &str, loader: &Path| -> PathBuf {
        if let Some(relative) = raw.strip_prefix("@loader_path/") {
            loader.join(relative)
        } else if let Some(relative) = raw.strip_prefix("@executable_path/") {
            executable.join(relative)
        } else if raw == "@loader_path" {
            loader.into()
        } else if raw == "@executable_path" {
            executable.into()
        } else {
            PathBuf::from(raw)
        }
    };
    if let Some(relative) = name.strip_prefix("@rpath/") {
        let mut roots: Vec<_> = macos_rpaths(source)?
            .into_iter()
            .map(|root| (root, parent.to_path_buf()))
            .collect();
        for component in components {
            // @loader_path in an inherited LC_RPATH belongs to the binary
            // defining that entry, rather than the library being resolved.
            roots.extend(
                macos_rpaths(&component.original)?
                    .into_iter()
                    .map(|root| (root, component.original.parent().unwrap().to_path_buf())),
            );
        }
        for (root, loader) in roots {
            let candidate = expand(&root, &loader).join(relative);
            if candidate.is_file() {
                return Ok(candidate.canonicalize()?);
            }
        }
        for directory in [
            parent.to_path_buf(),
            parent.join(".dylibs"),
            parent.join("../.dylibs"),
        ] {
            let candidate = directory.join(relative);
            if candidate.is_file() {
                return Ok(candidate.canonicalize()?);
            }
        }
        bail!(
            "Cannot resolve Mach-O dependency {name} in {}",
            source.display()
        )
    }
    let path = expand(name, parent);
    ensure!(
        path.is_file(),
        "Mach-O dependency not found: {name} in {}",
        source.display()
    );
    Ok(path.canonicalize()?)
}

/// Rewrite every non-system Mach-O reference and re-sign staged copies after load-command changes.
fn prepare_macos(
    files: &mut BTreeMap<String, PathBuf>,
    prefix: &str,
    stage: &Path,
    components: &[NativeFile],
    report: &mut Vec<Value>,
) -> Result<()> {
    let mut pending: VecDeque<_> = components.iter().cloned().collect();
    let mut inspected = BTreeSet::new();
    let mut libraries = BTreeMap::<PathBuf, NativeFile>::new();
    let mut rewritten = Vec::new();
    while let Some(component) = pending.pop_front() {
        if !inspected.insert(component.original.clone()) {
            continue;
        }
        let install_id = output(Command::new("otool").arg("-D").arg(&component.original))?
            .lines()
            .nth(1)
            .map(str::to_string);
        for dependency in macos_dependencies(&component.original)? {
            if macos_system_library(&dependency) || install_id.as_deref() == Some(&dependency) {
                continue;
            }
            let source = resolve_macos_dependency(&dependency, &component.original, components)?;
            let library = if let Some(existing) = components
                .iter()
                .find(|candidate| candidate.original.canonicalize().ok().as_ref() == Some(&source))
            {
                existing.clone()
            } else if let Some(existing) = libraries.get(&source) {
                existing.clone()
            } else {
                let name = source
                    .file_name()
                    .context("Mach-O dependency has no filename")?
                    .to_string_lossy();
                let library = stage_dependency(files, prefix, stage, &source, &name)?;
                add_dependency_notices(files, prefix, stage, &source, &name)?;
                report.push(json!({"name":name,"source":source,"destination":library.key}));
                libraries.insert(source, library.clone());
                pending.push_back(library.clone());
                library
            };
            let relative = relative_to(component.staged.parent().unwrap(), &library.staged)?;
            output(
                Command::new("install_name_tool")
                    .args(["-change", &dependency, &format!("@loader_path/{relative}")])
                    .arg(&component.staged),
            )?;
        }
        for rpath in macos_rpaths(&component.original)? {
            output(
                Command::new("install_name_tool")
                    .args(["-delete_rpath", &rpath])
                    .arg(&component.staged),
            )?;
        }
        let relative = relative_to(
            component.staged.parent().unwrap(),
            &stage.join("bin/native-libs"),
        )?;
        output(
            Command::new("install_name_tool")
                .args(["-add_rpath", &format!("@loader_path/{relative}")])
                .arg(&component.staged),
        )?;
        if install_id.is_some() {
            output(
                Command::new("install_name_tool")
                    .args([
                        "-id",
                        &format!(
                            "@loader_path/{}",
                            component.staged.file_name().unwrap().to_string_lossy()
                        ),
                    ])
                    .arg(&component.staged),
            )?;
        }
        rewritten.push(component);
    }
    for component in rewritten {
        output(
            Command::new("codesign")
                .args(["--force", "--sign", "-"])
                .arg(&component.staged),
        )?;
        output(
            Command::new("codesign")
                .args(["--verify", "--strict"])
                .arg(&component.staged),
        )?;
        for dependency in macos_dependencies(&component.staged)? {
            ensure!(
                macos_system_library(&dependency) || dependency.starts_with("@loader_path/"),
                "Mach-O dependency points outside runtime archive: {dependency}"
            );
            if let Some(relative) = dependency.strip_prefix("@loader_path/") {
                let resolved = component.staged.parent().unwrap().join(relative);
                ensure!(
                    resolved.is_file()
                        && resolved.canonicalize()?.starts_with(stage.canonicalize()?),
                    "Mach-O loader-relative dependency is absent from runtime: {dependency}"
                );
            }
        }
    }
    Ok(())
}

/// Copy third-party notices from their installed package or previously extracted MuPDF wheel.
fn add_dependency_notices(
    files: &mut BTreeMap<String, PathBuf>,
    prefix: &str,
    stage: &Path,
    source: &Path,
    name: &str,
) -> Result<()> {
    if name.starts_with("libmupdf") && files.keys().any(|key| key.contains("/mupdf-notices/")) {
        return Ok(());
    }
    let mut candidates = Vec::new();
    if cfg!(target_os = "linux") {
        if let Ok(package) = output(
            Command::new("rpm")
                .args(["-qf", "--qf", "%{NAME}\n"])
                .arg(source),
        ) && let Ok(paths) = output(Command::new("rpm").arg("-ql").arg(package.trim()))
        {
            candidates.extend(
                paths
                    .lines()
                    .filter(|path| {
                        path.contains("/licenses/")
                            || path.to_ascii_lowercase().contains("copying")
                            || path.to_ascii_lowercase().ends_with("/license")
                    })
                    .map(PathBuf::from)
                    .filter(|path| path.is_file()),
            );
        }
        if candidates.is_empty()
            && let Ok(package) = output(Command::new("dpkg-query").arg("-S").arg(source))
            && let Some((package, _)) = package.lines().find_map(|line| line.split_once(": "))
            && let Ok(paths) = output(Command::new("dpkg-query").args(["-L", package]))
        {
            candidates.extend(paths.lines().map(PathBuf::from).filter(|path| {
                path.is_file()
                    && path
                        .file_name()
                        .is_some_and(|name| license_filename(&name.to_string_lossy()))
            }));
            // Debian copyright files may refer to complete license texts in a
            // shared directory instead of repeating them in each package.
            let notices = candidates.clone();
            for notice in notices {
                let text = fs::read_to_string(notice).unwrap_or_default();
                for word in text.split_whitespace() {
                    let path = word.trim_matches(['\'', '"', ',', '.', ';', ')', '(']);
                    if path.starts_with("/usr/share/common-licenses/") && Path::new(path).is_file()
                    {
                        candidates.push(PathBuf::from(path));
                    }
                }
            }
        }
    } else if cfg!(target_os = "macos")
        && let Some(version) = homebrew_version_directory(source)
    {
        candidates.extend(local_license_files(version, 0)?);
        candidates.extend(local_license_files(&version.join("share/doc"), 2)?);
        candidates.extend(local_license_files(&version.join("share/licenses"), 2)?);
        if candidates.is_empty() {
            candidates.extend(homebrew_source_licenses(version, stage, name)?);
        }
    }
    // Standalone upstream distributions can carry notices beside the library
    // or one directory above, without requiring an installed package manager.
    for parent in source.ancestors().skip(1).take(2) {
        candidates.extend(local_license_files(parent, 0)?);
    }
    candidates.sort();
    candidates.dedup();
    ensure!(
        !candidates.is_empty(),
        "Cannot locate redistribution notices for native dependency {} ({name}); supply its upstream license beside the original library",
        source.display()
    );
    for (index, candidate) in candidates.into_iter().enumerate() {
        let basename = candidate.file_name().unwrap().to_string_lossy();
        let relative = format!("bin/native-notices/{name}-{index}-{basename}");
        let target = stage.join(&relative);
        fs::create_dir_all(target.parent().unwrap())?;
        fs::copy(candidate, &target)?;
        files.insert(format!("{prefix}/{relative}"), target);
    }
    Ok(())
}

/// Recognize license and copyright filenames without treating arbitrary README files as licenses.
fn license_filename(name: &str) -> bool {
    let lower = name.to_ascii_lowercase();
    ["license", "licence", "copying", "copyright", "notice"]
        .iter()
        .any(|prefix| lower == *prefix || lower.starts_with(&format!("{prefix}.")))
}

/// Find bounded installed notices without traversing symlink directories outside the package.
fn local_license_files(directory: &Path, depth: usize) -> Result<Vec<PathBuf>> {
    let mut paths = Vec::new();
    let Ok(entries) = fs::read_dir(directory) else {
        return Ok(paths);
    };
    for entry in entries {
        let entry = entry?;
        let kind = entry.file_type()?;
        if kind.is_file() && license_filename(&entry.file_name().to_string_lossy()) {
            paths.push(entry.path());
        } else if kind.is_dir() && depth > 0 {
            paths.extend(local_license_files(&entry.path(), depth - 1)?);
        }
    }
    Ok(paths)
}

/// Locate the exact Homebrew keg rather than notices for another installed package version.
fn homebrew_version_directory(source: &Path) -> Option<&Path> {
    source.ancestors().find(|path| {
        path.parent()
            .and_then(Path::parent)
            .and_then(Path::file_name)
            .is_some_and(|name| name == "Cellar")
    })
}

/// Obtain missing Homebrew notices from its checksum-verified source archive for the installed version.
fn homebrew_source_licenses(version: &Path, stage: &Path, name: &str) -> Result<Vec<PathBuf>> {
    let formula = version
        .parent()
        .and_then(Path::file_name)
        .context("Homebrew dependency has no formula")?
        .to_string_lossy();
    let info = output(Command::new("brew").args(["info", "--json=v2", &formula]))?;
    let info: Value = serde_json::from_str(&info)?;
    let stable = info["formulae"][0]["versions"]["stable"]
        .as_str()
        .context("Homebrew dependency has no stable source version")?;
    let installed = version.file_name().unwrap().to_string_lossy();
    ensure!(
        installed.split('_').next() == Some(stable),
        "Missing notices for Homebrew {formula} {installed}; current upstream source is {stable}, supply the matching license beside the installed library"
    );
    output(Command::new("brew").args(["fetch", "--build-from-source", &formula]))?;
    let archive = output(Command::new("brew").args(["--cache", "--build-from-source", &formula]))?;
    let archive = PathBuf::from(archive.trim());
    ensure!(archive.is_file(), "Homebrew source archive is absent");
    let members = output(Command::new("tar").arg("-tf").arg(&archive))?;
    let mut paths = Vec::new();
    for (index, member) in members
        .lines()
        .filter(|member| {
            !member.ends_with('/')
                && Path::new(member)
                    .file_name()
                    .is_some_and(|filename| license_filename(&filename.to_string_lossy()))
        })
        .enumerate()
    {
        // Read archive members to stdout instead of extracting paths supplied by
        // an upstream archive into the staging directory.
        let content = output(
            Command::new("tar")
                .arg("-xOf")
                .arg(&archive)
                .args(["--", member]),
        )?;
        let filename = Path::new(member).file_name().unwrap().to_string_lossy();
        let destination = stage.join(format!("source-notices/{name}-{index}-{filename}"));
        fs::create_dir_all(destination.parent().unwrap())?;
        fs::write(&destination, content)?;
        paths.push(destination);
    }
    Ok(paths)
}
