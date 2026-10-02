//! Validate a platform wheel in an isolated uv tool installation without source imports.
//!
//! This checks copied native entry points, resource extraction, all output targets,
//! MathType export/import, reviewer replies, unsaved HTML, cache invalidation, worker
//! ownership and clean shutdown. Inputs use a bundled CSL so CI needs no network.

use anyhow::{Context, Result, ensure};
use serde_json::{Value, json};
use std::collections::BTreeMap;
use std::io::Read;
use std::path::{Path, PathBuf};
use std::process::Command;

/// Own only this validation's installed executables, files and service state.
struct Installed {
    executable: PathBuf,
    project: PathBuf,
    home: PathBuf,
    environment: BTreeMap<String, String>,
}

impl Installed {
    /// Execute the installed product without any Python module or source resource override.
    fn command(&self, arguments: &[&str]) -> Result<String> {
        self.command_at(&self.executable, arguments)
    }

    /// Apply identical isolation to both installed aliases and clear development loader overrides.
    fn command_at(&self, executable: &Path, arguments: &[&str]) -> Result<String> {
        println!(
            "[papper smoke] Running {} {arguments:?}",
            executable.file_name().unwrap().to_string_lossy()
        );
        let mut command = Command::new(executable);
        for (key, _) in std::env::vars_os() {
            let name = key.to_string_lossy().to_ascii_uppercase();
            if ["PAPPER_", "PMT_", "PANDOC_", "PYTHON"]
                .iter()
                .any(|prefix| name.starts_with(prefix))
                || matches!(
                    name.as_str(),
                    "LD_LIBRARY_PATH" | "DYLD_LIBRARY_PATH" | "DYLD_FALLBACK_LIBRARY_PATH"
                )
            {
                command.env_remove(key);
            }
        }
        let result = command
            .args(arguments)
            .envs(&self.environment)
            .current_dir(&self.project)
            .output()?;
        ensure!(
            result.status.success(),
            "Installed command {arguments:?} failed: {}\n{}",
            String::from_utf8_lossy(&result.stdout),
            String::from_utf8_lossy(&result.stderr)
        );
        Ok(String::from_utf8(result.stdout)?)
    }

    /// Locate the one content-addressed runtime extracted by the installed executable.
    fn resources(&self) -> Result<PathBuf> {
        std::fs::read_dir(self.home.join("runtime"))?
            .filter_map(|entry| entry.ok())
            .map(|entry| entry.path())
            .find(|path| path.join(".complete").is_file())
            .context("Installed binary did not extract its native resources")
    }
}

impl Drop for Installed {
    /// Stop only the project service created by this isolated validation run.
    fn drop(&mut self) {
        let _ = self.command(&["clean"]);
    }
}

/// Verify actual document contents and OPC references instead of launch-only success.
fn zip_part(path: &Path, part: &str) -> Result<String> {
    let mut archive = zip::ZipArchive::new(std::fs::File::open(path)?)?;
    let mut text = String::new();
    archive.by_name(part)?.read_to_string(&mut text)?;
    Ok(text)
}

/// Install a wheel into isolated uv directories and run the complete native smoke pipeline.
pub fn run(wheel: &Path) -> Result<()> {
    let wheel = wheel.canonicalize()?;
    let archive = zip::ZipArchive::new(std::fs::File::open(&wheel)?)?;
    ensure!(
        !archive.file_names().any(|name| name.ends_with(".py")
            || name.ends_with(".pyc")
            || name.ends_with(".pth")
            || name.contains("site-packages/pandoc_manuscript")),
        "Production wheel contains Python application code"
    );
    let temporary = tempfile::Builder::new()
        .prefix("papper-native-smoke-")
        .tempdir()?;
    let tool_bin = temporary.path().join("bin");
    let tool_dir = temporary.path().join("tools");
    let status = Command::new("uv")
        .args(["tool", "install", "--no-index"])
        .arg(&wheel)
        .env("UV_TOOL_DIR", &tool_dir)
        .env("UV_TOOL_BIN_DIR", &tool_bin)
        .env("UV_CACHE_DIR", temporary.path().join("uv-cache"))
        .env("UV_PYTHON_DOWNLOADS", "never")
        .status()?;
    ensure!(
        status.success(),
        "Could not install native wheel with uv tool"
    );
    let project = temporary.path().join("paper");
    std::fs::create_dir_all(&project)?;
    let home = temporary.path().join("state");
    std::fs::create_dir_all(&home)?;
    let filename = if cfg!(windows) {
        "papper.exe"
    } else {
        "papper"
    };
    let mut paths = vec![tool_bin.clone()];
    if cfg!(windows) {
        let windows = PathBuf::from(
            std::env::var_os("SystemRoot").context("Windows system directory missing")?,
        );
        paths.extend([windows.join("System32"), windows]);
    } else {
        paths.extend(["/usr/bin", "/bin", "/usr/sbin", "/sbin"].map(PathBuf::from));
    }
    let installed = Installed {
        executable: tool_bin.join(filename),
        project,
        home: home.clone(),
        environment: BTreeMap::from([
            ("PAPPER_HOME".into(), home.to_string_lossy().into()),
            ("PAPPER_DISABLE_UPDATE_CHECK".into(), "1".into()),
            ("HOME".into(), home.to_string_lossy().into()),
            ("USERPROFILE".into(), home.to_string_lossy().into()),
            (
                "APPDATA".into(),
                home.join("config").to_string_lossy().into(),
            ),
            (
                "LOCALAPPDATA".into(),
                home.join("local").to_string_lossy().into(),
            ),
            (
                "XDG_CONFIG_HOME".into(),
                home.join("config").to_string_lossy().into(),
            ),
            (
                "XDG_DATA_HOME".into(),
                home.join("data").to_string_lossy().into(),
            ),
            (
                "PATH".into(),
                std::env::join_paths(paths)?.to_string_lossy().into(),
            ),
            (
                "PANDOC_DATA_DIR".into(),
                temporary
                    .path()
                    .join("no-pandoc-data")
                    .to_string_lossy()
                    .into(),
            ),
        ]),
    };
    let version = installed.command(&["--version"])?;
    ensure!(
        version.starts_with("papper "),
        "Native version output missing"
    );
    let alias = installed
        .executable
        .with_file_name(if cfg!(windows) { "pmt.exe" } else { "pmt" });
    ensure!(
        installed.command_at(&alias, &["--version"])? == version,
        "Native pmt alias differs from papper"
    );
    installed.command(&["init", ".", "--setup"])?;
    installed.command(&["doctor"])?;
    // Exercise the packaged C library in its isolated child, with no Python PDF
    // module or development DLL path available to hide a missing dependency.
    let fixture = installed.project.join("input.pdf");
    std::fs::copy(
        super::root()?.join("tests/fixtures/template-manuscript.pdf"),
        &fixture,
    )?;
    let pdf: Value =
        serde_json::from_str(&installed.command(&["__pdf_extract", &fixture.to_string_lossy()])?)?;
    ensure!(
        pdf["pages"][0]["text"]
            .as_str()
            .is_some_and(|text| !text.trim().is_empty()),
        "Installed PDF engine omitted page text"
    );
    let resources = installed.resources()?;
    let csl = resources.join("pandoc/csl/sage-vancouver.csl");
    let source = format!(
        "---\ntitle: Native wheel smoke\nbibliography: references.bib\ncsl: {}\n---\n\nOriginal wheel prose cites [@packaged].\n\n$$x_1+2$$ {{#eq:sum}}\n\n| Value |\n|-------|\n| 42    |\n\nTable: Packaged values {{#tbl:values}}\n\nSee @tbl:values and @eq:sum.\n",
        csl.to_string_lossy().replace('\\', "/")
    );
    std::fs::write(installed.project.join("manuscript.md"), &source)?;
    std::fs::write(
        installed.project.join("references.bib"),
        "@article{packaged,author={Developer, Wheel},title={Bundled citation entry},journal={Packaging Journal},year={2026},volume={1},pages={1--2}}\n",
    )?;
    std::fs::write(
        installed.project.join("style.yml"),
        "mathtype: true\nmathtypeConversionMethod: rust\n",
    )?;
    installed.command(&["build", "html"])?;
    let html_path = installed.project.join("output/html/manuscript.html");
    let cold = std::fs::read_to_string(&html_path)?;
    ensure!(
        cold.contains("Bundled citation entry")
            && cold.contains("Packaged values")
            && !cold.contains("@tbl:values"),
        "Installed HTML failed citations/crossrefs"
    );
    let socket = std::net::TcpListener::bind(("127.0.0.1", 0))?;
    let port = socket.local_addr()?.port().to_string();
    drop(socket);
    installed.command(&["build", "html", "--start-server", "--server-port", &port])?;
    ensure!(
        std::fs::read_to_string(&html_path)? == cold,
        "Installed service differs from cold HTML"
    );
    let address = format!("http://127.0.0.1:{port}");
    let agent = ureq::AgentBuilder::new()
        .try_proxy_from_env(false)
        .timeout(std::time::Duration::from_secs(30))
        .build();
    let first: Value = agent
        .post(&format!("{address}/convert"))
        .send_json(json!({"path":"manuscript.md"}))?
        .into_json()?;
    ensure!(
        first["cache_hit"] == true,
        "Installed service did not reuse identical HTML"
    );
    let unsaved = format!("{source}\nUnsaved wheel prose.\n");
    let edited: Value = agent
        .post(&format!("{address}/convert"))
        .send_json(json!({"path":"manuscript.md","text":unsaved}))?
        .into_json()?;
    ensure!(
        edited["output"]
            .as_str()
            .is_some_and(|text| text.contains("Unsaved wheel prose"))
            && edited["citeproc_cache_hit"] == true,
        "Installed worker did not reuse citations on an unsaved edit"
    );
    ensure!(
        std::fs::read_to_string(installed.project.join("manuscript.md"))? == source,
        "Unsaved request altered source data"
    );
    std::fs::write(
        installed.project.join("manuscript.md"),
        format!("{source}\nOn-disk wheel edit.\n"),
    )?;
    let disk: Value = agent
        .post(&format!("{address}/convert"))
        .send_json(json!({"path":"manuscript.md"}))?
        .into_json()?;
    ensure!(
        disk["cache_hit"] == false
            && disk["output"]
                .as_str()
                .is_some_and(|html| html.contains("On-disk wheel edit")
                    && !html.contains("Unsaved wheel prose")),
        "Installed service did not invalidate HTML after an on-disk source edit"
    );
    std::fs::write(installed.project.join("manuscript.md"), &source)?;
    installed.command(&["build", "docx"])?;
    let document = installed.project.join("output/docx/manuscript.docx");
    let xml = zip_part(&document, "word/document.xml")?;
    ensure!(
        xml.contains("OLEObject") && !xml.contains("MTLATEX:"),
        "Installed MathType build omitted native equation objects"
    );
    installed.command(&["convert", "output/docx/manuscript.docx", "-o", "converted"])?;
    let markdown = std::fs::read_to_string(installed.project.join("converted/manuscript.md"))?;
    ensure!(
        markdown.contains("x_{1}") || markdown.contains("x_1"),
        "Installed equation import omitted TeX"
    );
    installed.command(&["build", "latex"])?;
    ensure!(
        std::fs::read_to_string(installed.project.join("output/latex/manuscript.tex"))?
            .contains("Original wheel prose"),
        "Installed LaTeX omitted manuscript text"
    );
    installed.command(&["build", "json"])?;
    let ast: Value = serde_json::from_reader(std::fs::File::open(
        installed.project.join("output/json/manuscript.json"),
    )?)?;
    ensure!(
        ast["blocks"]
            .as_array()
            .is_some_and(|blocks| !blocks.is_empty()),
        "Installed JSON build is empty"
    );
    std::fs::write(
        installed.project.join("reply.md"),
        "# Response\n\nSee @eq:sum and @tbl:values [@packaged].\n",
    )?;
    installed.command(&["build-reply", "reply.md", "-o", "reply.txt"])?;
    let reply = std::fs::read_to_string(installed.project.join("reply.txt"))?;
    ensure!(
        !reply.contains("@eq:sum") && reply.contains('1'),
        "Installed reply omitted manuscript numbering"
    );
    installed.command(&["build-reply", "reply.md"])?;
    ensure!(
        zip_part(
            &installed.project.join("output/docx/reply.docx"),
            "word/document.xml"
        )?
        .contains("Response"),
        "Installed reply DOCX missing content"
    );
    installed.command(&["clean"])?;
    ensure!(
        !installed.project.join("output").exists(),
        "Installed clean retained generated outputs"
    );
    ensure!(
        agent.get(&format!("{address}/version")).call().is_err(),
        "Installed clean left its native service running"
    );
    println!(
        "[papper smoke] Installed native wheel passed CLI, resources, targets, MathType, import, reply, cache and shutdown checks"
    );
    Ok(())
}
