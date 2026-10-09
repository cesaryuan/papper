//! Coordinate native commands using shared metadata, document and engine modules.

use anyhow::{Context, Result, bail};
use papper_core::metadata::{
    MetadataOptions, load_effective_metadata_text, markdown_without_yaml_header,
    reply_manuscript_text, write_pandoc_metadata,
};
use papper_core::paths::{
    atomic_write, canonical_project, display_path, pandoc_path, project_state_dir,
};
use papper_core::resources::ResourcePaths;
use papper_engine::{PandocCli, discover_engine};
use papper_server::{HtmlBuildRequest, build_html};
use std::collections::BTreeMap;
use std::ffi::OsString;
use std::path::{Path, PathBuf};

use crate::{BuildArgs, BuildTarget, CleanArgs, CliCommand, InitArgs};

/// Dispatch to native implementations, loading resources only when a command needs them.
pub fn dispatch(command: CliCommand) -> Result<()> {
    match command {
        CliCommand::Guide(args) => crate::guide::print(args),
        CliCommand::NativeServer(args) => papper_server::run_server_with_options(
            &args.config,
            &args.host,
            args.port,
            args.refresh_runtime,
        ),
        CliCommand::NativeDecode(args) => {
            let decoded = papper_platform::native::decode_documents(&args.documents)?;
            serde_json::to_writer(std::io::stdout().lock(), &decoded)?;
            Ok(())
        }
        CliCommand::Build(args) => build(args),
        CliCommand::Init(args) => initialize(args),
        CliCommand::Clean(args) => clean(args),
        CliCommand::Setup(args) => crate::tools::setup(&ResourcePaths::discover()?, args.force),
        CliCommand::Doctor(_) => doctor(),
        CliCommand::NativeUpdate => crate::tools::run_update_worker(),
        CliCommand::Convert(args) => convert_docx(args),
        CliCommand::BuildReply(args) => crate::reply::build(&args),
        CliCommand::NativePdf(args) => crate::reply::extract_pdf_command(&args.input),
    }
}

/// Resolve files relative to the project and strip Windows extended prefixes.
fn absolute(path: &Path, project: &Path) -> PathBuf {
    if path.is_absolute() {
        path.to_path_buf()
    } else {
        project.join(path)
    }
}

/// Validate target-specific options before creating any output or service state.
fn validate_build(args: &BuildArgs) -> Result<()> {
    anyhow::ensure!(
        args.markdown.is_none() || args.manuscript.is_none(),
        "Specify the markdown file either positionally or with --manuscript, not both."
    );
    if args.target != BuildTarget::Docx {
        anyhow::ensure!(
            args.mathtype.is_none() && !args.no_mathtype,
            "--mathtype/--no-mathtype is only supported by the docx target."
        );
        anyhow::ensure!(
            args.lang.is_none(),
            "--lang is only supported by the docx target."
        );
        anyhow::ensure!(
            args.reference_doc.is_none(),
            "--reference-doc is only supported by the docx target."
        );
    }
    anyhow::ensure!(
        !args.start_server || args.target == BuildTarget::Html,
        "--start-server is only supported by the html target."
    );
    anyhow::ensure!(
        args.manuscript_line_source.is_none()
            || matches!(args.target, BuildTarget::Docx | BuildTarget::Html),
        "--manuscript-line-source is only supported by html/docx reply builds."
    );
    Ok(())
}

/// Build a fully specified output through native Rust configuration and the retained engine.
fn build(mut args: BuildArgs) -> Result<()> {
    validate_build(&args)?;
    let project = canonical_project(&std::env::current_dir()?)?;
    let source_arg = args.manuscript.as_deref().or(args.markdown.as_deref());
    let default_source = std::env::var_os("PMT_MANUSCRIPT_FILE")
        .map(PathBuf::from)
        .unwrap_or_else(|| PathBuf::from("manuscript.md"));
    let source = absolute(source_arg.unwrap_or(&default_source), &project);
    anyhow::ensure!(
        source.is_file(),
        "Markdown file not found: {}",
        source.display()
    );
    let source = PathBuf::from(display_path(&source.canonicalize()?));
    let name = if source_arg.is_some() {
        source
            .file_stem()
            .unwrap_or_default()
            .to_string_lossy()
            .to_string()
    } else {
        std::env::var("PMT_PROJECT_NAME").unwrap_or_else(|_| "manuscript".into())
    };
    let (directory, extension) = match args.target {
        BuildTarget::Html => ("html", "html"),
        BuildTarget::Docx => ("docx", "docx"),
        BuildTarget::Latex => ("latex", "tex"),
        BuildTarget::Json => ("json", "json"),
    };
    let output = if let Some(explicit) = &args.output {
        absolute(explicit, &project)
    } else {
        let variable = format!("PMT_{}_DIR", directory.to_uppercase());
        let output_dir = std::env::var_os(variable)
            .map(PathBuf::from)
            .unwrap_or_else(|| PathBuf::from("output").join(directory));
        absolute(&output_dir, &project).join(format!("{name}.{extension}"))
    };
    // Preserve runtime settings that the old CLI left configurable through PMT_.
    // Explicit CLI settings still win, and metadata fields never inherit env vars.
    if args.style_file.is_none() {
        args.style_file = std::env::var_os("PMT_STYLE_FILE").map(PathBuf::from);
    }
    if let Some(style) = &args.style_file {
        let selected = absolute(style, &project);
        anyhow::ensure!(
            selected.exists(),
            "Style file not found: {}",
            style.display()
        );
        anyhow::ensure!(
            selected.is_file(),
            "Style path is not a file: {}",
            style.display()
        );
    }
    if args.reference_doc.is_none() {
        args.reference_doc = std::env::var_os("PMT_REFERENCE_DOC").map(PathBuf::from);
    }
    let resources = ResourcePaths::discover()?;
    if args.logging.verbose {
        eprintln!(
            "[DEBUG] Native input: {}; output: {}",
            source.display(),
            output.display()
        );
    }
    println!("[{}] Building {}...", directory.to_uppercase(), directory);
    if args.target == BuildTarget::Html {
        let request = HtmlBuildRequest {
            project,
            source,
            output: output.clone(),
            style_file: args
                .style_file
                .map(|style| absolute(&style, &std::env::current_dir().unwrap_or_default())),
            resource_path: args.resource_path,
            manuscript_line_source: args.manuscript_line_source,
            resources,
        };
        let command = args
            .server_command
            .or_else(|| std::env::var("PMT_PANDOC_SERVER_COMMAND").ok());
        build_html(
            &request,
            args.start_server,
            &args.server_host,
            args.server_port,
            command.as_deref(),
        )?;
    } else {
        build_other(&args, &project, &source, &output, &resources)?;
    }
    println!(
        "[OK] {} created: {}",
        directory.to_uppercase(),
        output.display()
    );
    Ok(())
}

/// Apply source-first style/resource lookup and run non-HTML conversion from a source snapshot.
fn build_other(
    args: &BuildArgs,
    project: &Path,
    source: &Path,
    output: &Path,
    resources: &ResourcePaths,
) -> Result<()> {
    let temporary = tempfile::Builder::new().prefix("papper-build-").tempdir()?;
    // An explicit style (including style.yml) selects exactly one project-relative
    // file; source-directory discovery must not override that user selection.
    let mut styles = if let Some(style) = &args.style_file {
        vec![absolute(style, project)]
    } else {
        vec![
            source.parent().unwrap().join("style.yml"),
            project.join("style.yml"),
        ]
    };
    styles.dedup();
    let roots = vec![
        source.parent().unwrap().to_path_buf(),
        project.to_path_buf(),
        resources.root.clone(),
    ];
    let text = std::fs::read_to_string(source)?
        .trim_start_matches('\u{feff}')
        .replace("\r\n", "\n");
    let reply = if args.target == BuildTarget::Docx {
        reply_manuscript_text(&text, source)?
    } else {
        None
    };
    if let Some(manuscript) = &reply {
        anyhow::ensure!(
            manuscript.is_file(),
            "Reply manuscript not found: {}",
            manuscript.display()
        );
        eprintln!("[REPLY] Using manuscript: {}", manuscript.display());
    }
    let options = MetadataOptions {
        style_paths: styles.into_iter().filter(|path| path.is_file()).collect(),
        bundled_style_dir: resources.resource("defaults"),
        allow_missing_header: true,
        lang_override: args.lang.clone(),
        reply: reply.is_some(),
        resource_roots: roots,
        ..MetadataOptions::default()
    };
    let mut effective = load_effective_metadata_text(&text, source, &options)?;
    let mut environment = BTreeMap::from([(
        "PMT_CITATION_NUMBER_RANGE_DELIMITER".into(),
        effective
            .pmt_settings
            .fields()
            .citation_number_range_delimiter
            .as_deref()
            .filter(|raw| *raw != "–")
            .map(str::to_string),
    )]);
    let mut use_mathtype = false;
    environment.insert(
        "PMT_TABLE_AUTOFIT".into(),
        Some(
            effective
                .pmt_settings
                .fields()
                .table_autofit
                .as_str()
                .into(),
        ),
    );
    if args.target == BuildTarget::Docx {
        effective = papper_document::docx::prepare_docx_metadata(&effective)?;
        if args.no_mathtype {
            effective.pmt_settings.set_mathtype(false);
        }
        if let Some(raw) = &args.mathtype {
            let enabled = match raw.as_str() {
                "true" | "1" | "yes" => Some(true),
                "false" | "0" | "no" => Some(false),
                "auto" => None,
                _ => bail!("--mathtype requires true, false or auto"),
            };
            if let Some(enabled) = enabled {
                effective.pmt_settings.set_mathtype(enabled);
            }
        }
        if effective.pmt_settings.fields().mathtype {
            match papper_document::docx::check_mathtype_available(resources, &effective) {
                Ok(()) => use_mathtype = true,
                Err(error) => eprintln!(
                    "[WARN] MathType is unavailable; retaining native Word equations: {error:#}"
                ),
            }
        }
        let chinese = effective
            .pandoc_metadata
            .get("lang")
            .is_some_and(papper_core::metadata::is_chinese_language);
        environment.insert(
            "PMT_CHINESE_MODE".into(),
            if chinese { Some("true".into()) } else { None },
        );
        let native = reply.is_none() && effective.pmt_settings.fields().docx_native_crossref;
        environment.insert("PMT_DOCX_NATIVE_CROSSREFS".into(), Some(native.to_string()));
        environment.insert(
            "PMT_ENABLE_MATHTYPE_MARKERS".into(),
            use_mathtype.then(|| "true".into()),
        );
        if native {
            let namespace = format!(
                "{:x}-{}",
                std::process::id(),
                std::time::SystemTime::now()
                    .duration_since(std::time::UNIX_EPOCH)?
                    .as_nanos()
            );
            environment.insert(
                "PMT_DOCX_BOOKMARK_NAMESPACE".into(),
                Some(
                    namespace
                        .chars()
                        .filter(|c| c.is_ascii_hexdigit())
                        .take(24)
                        .collect(),
                ),
            );
        }
    }
    let pandoc_metadata = if args.target == BuildTarget::Docx {
        papper_document::docx::derive_docx_pandoc_metadata(&effective, use_mathtype)?
    } else {
        effective.pandoc_metadata.clone()
    };
    let metadata_file =
        write_pandoc_metadata(&pandoc_metadata, temporary.path().join("metadata.yml"))?;
    let input = temporary.path().join("input.md");
    let body = markdown_without_yaml_header(&text);
    let resolved;
    let body = if let Some(manuscript) = &reply {
        let engine = PandocCli::new(discover_engine(&resources.root)?);
        let resolver = papper_document::reply::ReplyResolver {
            manuscript,
            metadata: &metadata_file,
            work: temporary.path(),
            effective: &effective,
            from_format: "markdown",
            engine: &engine,
            environment: &environment,
        };
        let numbered = papper_document::reply::resolve_reply_markdown(
            body,
            &resolver,
            papper_document::reply::ReplyFormat::Docx,
        )?;
        let line_source = args
            .manuscript_line_source
            .as_deref()
            .map(|path| absolute(path, project))
            .unwrap_or_else(|| manuscript.clone());
        resolved = papper_document::reply::resolve_line_regexes(
            &numbered,
            &line_source,
            temporary.path(),
        )?;
        resolved.as_str()
    } else {
        body
    };
    atomic_write(&input, body.as_bytes())?;
    let defaults = if args.target == BuildTarget::Latex {
        "pandoc/pandoc-latex.yml"
    } else {
        "pandoc/pandoc-docx.yml"
    };
    let defaults_path = if reply.is_some() {
        papper_document::reply::reply_defaults(resources, temporary.path(), "docx")?
    } else if args.target == BuildTarget::Latex {
        native_latex_defaults(resources, temporary.path())?
    } else {
        resources.resource(defaults)
    };
    let generated = temporary.path().join(format!(
        "output.{}",
        if args.target == BuildTarget::Latex {
            "tex"
        } else if args.target == BuildTarget::Json {
            "json"
        } else {
            "docx"
        }
    ));
    let resource_path = args.resource_path.clone().unwrap_or_else(|| {
        [source.parent().unwrap(), project]
            .iter()
            .map(|path| pandoc_path(path))
            .collect::<Vec<_>>()
            .join(if cfg!(windows) { ";" } else { ":" })
    });
    let mut command: Vec<OsString> = vec![
        "--defaults".into(),
        defaults_path.into_os_string(),
        "--metadata-file".into(),
        metadata_file.into_os_string(),
        "--output".into(),
        generated.as_os_str().to_owned(),
        "--resource-path".into(),
        resource_path.clone().into(),
    ];
    let resource_roots: Vec<PathBuf> = std::env::split_paths(&resource_path)
        .map(|path| absolute(&path, project))
        .collect();
    if args.target == BuildTarget::Latex {
        environment.insert(
            "PMT_LATEX_TARGET_DIR".into(),
            Some(pandoc_path(output.parent().unwrap_or(project))),
        );
        environment.insert(
            "PMT_LATEX_RESOURCE_PATH".into(),
            Some(serde_json::to_string(&resource_roots)?),
        );
    }
    if args.target == BuildTarget::Json {
        command.extend(["--to".into(), "json".into()]);
    }
    if args.target == BuildTarget::Docx {
        let reference = args
            .reference_doc
            .as_deref()
            .map(|path| absolute(path, project));
        crate::docx_pipeline::append_reference(
            &mut command,
            resources,
            &effective,
            reference.as_deref(),
            temporary.path(),
        )?;
        crate::docx_pipeline::append_output_filters(&mut command, resources);
        let cache = project_state_dir(project)?.join("cache");
        environment.extend(crate::images::filter_environment(
            resources,
            &effective.pmt_settings,
            &resource_roots,
            &cache.join("svg-embedded"),
            &cache.join("svg-png"),
            &cache.join("svg-rsvg"),
        )?);
    }
    command.push(input.into_os_string());
    PandocCli::new(discover_engine(&resources.root)?).run(&command, project, &environment)?;
    if args.target == BuildTarget::Docx {
        let debug_parent = std::env::var_os("PMT_MATHTYPE_WORK_DIR").map(PathBuf::from);
        let project_name = if args.markdown.is_some() || args.manuscript.is_some() {
            source
                .file_stem()
                .unwrap_or_default()
                .to_string_lossy()
                .to_string()
        } else {
            std::env::var("PMT_PROJECT_NAME").unwrap_or_else(|_| "manuscript".into())
        };
        let formatted = if use_mathtype {
            debug_parent
                .as_ref()
                .map(|parent| parent.join(format!("{project_name}.marked.docx")))
                .unwrap_or_else(|| temporary.path().join("formatted.docx"))
        } else {
            output.to_path_buf()
        };
        let postprocess = std::env::var("PMT_ENABLE_DOCX_POSTPROCESS")
            .map(|raw| !matches!(raw.to_lowercase().as_str(), "false" | "no" | "0" | "off"))
            .unwrap_or(true);
        if postprocess {
            papper_document::docx::postprocess_docx(
                &generated,
                &formatted,
                &effective,
                &papper_document::docx::DocxPostprocessOptions {
                    skip_author_info: reply.is_some(),
                    reply_style_formatting: reply.is_some(),
                    native_crossrefs: reply.is_none()
                        && effective.pmt_settings.fields().docx_native_crossref,
                    ..Default::default()
                },
            )?;
        } else {
            atomic_write(&formatted, &std::fs::read(&generated)?)?;
        }
        if use_mathtype {
            let work = debug_parent.map(|parent| parent.join(project_name));
            papper_document::docx::convert_marked_docx_with_work_dir(
                &formatted,
                output,
                resources,
                &effective,
                project,
                work.as_deref(),
            )?;
        }
        Ok(())
    } else {
        atomic_write(output, &std::fs::read(generated)?)
    }
}

/// Resolve authored Lua filter paths while preserving LaTeX defaults and order.
fn native_latex_defaults(resources: &ResourcePaths, work: &Path) -> Result<PathBuf> {
    let mut text = std::fs::read_to_string(resources.resource("pandoc/pandoc-latex.yml"))?;
    text = text.replace("${.}", &pandoc_path(&resources.pandoc));
    let path = work.join("pandoc-latex.yml");
    atomic_write(&path, text.as_bytes())?;
    Ok(path)
}

/// Import DOCX with retained Lua filters and the existing native MathType decoder.
fn convert_docx(args: crate::ConvertArgs) -> Result<()> {
    let project = canonical_project(&std::env::current_dir()?)?;
    let source = absolute(&args.input, &project);
    anyhow::ensure!(
        source.is_file()
            && source
                .extension()
                .is_some_and(|extension| extension.eq_ignore_ascii_case("docx")),
        "Expected an existing DOCX file: {}",
        source.display()
    );
    let source = PathBuf::from(display_path(&source.canonicalize()?));
    let destination = absolute(&args.output_dir, &project);
    let resources = ResourcePaths::discover()?;
    let decoded = papper_platform::native::decode_documents(std::slice::from_ref(&source))?;
    let temporary = tempfile::Builder::new()
        .prefix("papper-convert-")
        .tempdir()?;
    let map = temporary.path().join("equations.json");
    atomic_write(&map, &serde_json::to_vec(&decoded)?)?;
    let output_name = format!("{}.md", source.file_stem().unwrap().to_string_lossy());
    let output = temporary.path().join(&output_name);
    let mut command = vec![
        source.as_os_str().to_owned(),
        "--from=docx".into(),
        // Simple/multiline tables can split long image syntax at a column
        // boundary. Pipe/grid tables retain conservatively preserved layouts.
        "--to=markdown-simple_tables-multiline_tables".into(),
        "--wrap=none".into(),
        // Filters can add metadata such as subfigGrid even to English documents.
        "--standalone".into(),
    ];
    let chinese_ratio = papper_document::docx::chinese_character_ratio(&source)?;
    if chinese_ratio >= 0.6 {
        command.push("--metadata=lang:zh-CN".into());
        println!(
            "[convert] Chinese characters: {:.1}%; adding lang: zh-CN",
            chinese_ratio * 100.0
        );
    }
    for filename in [
        "mtef_parser.lua",
        "equation_tables.lua",
        "remove_toc_anchors.lua",
        "detect_subfigures.lua",
        "extract_inline_images.lua",
        "detect_figure.lua",
        "detect_table.lua",
        "crossrefs.lua",
        "round_image_dimensions.lua",
    ] {
        let filter = resources.resource(format!("pandoc/filters/convert/{filename}"));
        anyhow::ensure!(
            filter.is_file(),
            "Convert filter is missing: {}",
            filter.display()
        );
        command.extend(["--lua-filter".into(), filter.into_os_string()]);
    }
    if args.fuzzy_crossrefs {
        let filter = resources.resource("pandoc/filters/convert/crossrefs_fuzz.lua");
        anyhow::ensure!(
            filter.is_file(),
            "Convert filter is missing: {}",
            filter.display()
        );
        command.extend([
            "--metadata=papper-fuzzy-crossrefs:true".into(),
            "--lua-filter".into(),
            filter.into_os_string(),
        ]);
        println!("[convert] fuzzy figure/table/equation cross-reference recovery enabled");
    }
    command.extend([
        "--lua-filter".into(),
        resources
            .resource("pandoc/filters/convert/media_paths.lua")
            .into_os_string(),
    ]);
    command.extend([
        "--extract-media=.".into(),
        "--output".into(),
        output_name.clone().into(),
    ]);
    let environment = BTreeMap::from([
        ("MATHTYPE_LATEX_MAP".into(), Some(display_path(&map))),
        (
            "PAPPER_CONVERT_OUTPUT_DIR".into(),
            Some(pandoc_path(&destination)),
        ),
    ]);
    PandocCli::new(discover_engine(&resources.root)?).run(
        &command,
        temporary.path(),
        &environment,
    )?;
    // Pandoc emits CRLF on Windows; the original importer published portable LF
    // Markdown, so preserve that user-visible format when leaving Python.
    let markdown = std::fs::read_to_string(&output)?
        .replace("\r\n", "\n")
        .replace('\r', "\n")
        .replace("](./media/", "](media/");
    // Do not expose half-built Markdown if decoding or Pandoc fails. Media is
    // published only after conversion succeeds, preserving existing user files.
    std::fs::create_dir_all(&destination)?;
    if temporary.path().join("media").is_dir() {
        publish_media_tree(&temporary.path().join("media"), &destination.join("media"))?;
    }
    atomic_write(&destination.join(&output_name), markdown.as_bytes())?;
    println!(
        "[convert] Wrote {}",
        destination.join(output_name).display()
    );
    Ok(())
}

/// Publish extracted media without replacing any existing file, including concurrent imports.
fn publish_media_tree(source: &Path, destination: &Path) -> Result<()> {
    for entry in std::fs::read_dir(source)? {
        let entry = entry?;
        let target = destination.join(entry.file_name());
        if entry.file_type()?.is_dir() {
            publish_media_tree(&entry.path(), &target)?;
        } else {
            papper_core::paths::publish_media(&target, &std::fs::read(entry.path())?)?;
        }
    }
    Ok(())
}

/// Validate the bundled engine and its embedded crossref before using managed tools.
fn setup() -> Result<()> {
    crate::tools::setup(&ResourcePaths::discover()?, false)
}

/// Report actual native capabilities and current project files without importing Python dependencies.
fn doctor() -> Result<()> {
    let status = crate::tools::doctor(&ResourcePaths::discover()?, &std::env::current_dir()?)?;
    anyhow::ensure!(status == 0, "Project diagnostics failed");
    Ok(())
}

/// Copy selected templates while retaining user data and generated-directory exclusions.
fn initialize(args: InitArgs) -> Result<()> {
    let resources = ResourcePaths::discover()?;
    let language = args
        .lang
        .as_deref()
        .unwrap_or("")
        .trim()
        .replace('_', "-")
        .to_lowercase();
    anyhow::ensure!(
        language.is_empty() || language == "zh-cn",
        "Only `--lang zh-cn` is currently supported by `papper init`."
    );
    anyhow::ensure!(
        !(args.force && args.merge),
        "Use only one of --force or --merge for `papper init`."
    );
    let root = absolute(&args.directory, &std::env::current_dir()?);
    std::fs::create_dir_all(&root)?;
    let manuscript = if language == "zh-cn" {
        "manuscript-cn.md"
    } else {
        "manuscript.md"
    };
    let reply = if language == "zh-cn" {
        "reply_to_reviewers-cn.md"
    } else {
        "reply_to_reviewers.md"
    };
    let entries = [
        (".agents", ".agents"),
        (".vscode", ".vscode"),
        ("examples", "examples"),
        (".gitignore", ".gitignore"),
        ("AGENTS.md", "AGENTS.md"),
        (manuscript, "manuscript.md"),
        (reply, "reply_to_reviewers.md"),
        ("style-project.yml", "style.yml"),
    ];
    let existing: Vec<_> = entries
        .iter()
        .map(|(_, target)| *target)
        .filter(|name| root.join(name).exists())
        .collect();
    anyhow::ensure!(
        existing.is_empty() || args.force || args.merge,
        "Target already contains template files: {}. Use --force to overwrite them: {}",
        existing.join(", "),
        root.display()
    );
    for (source_name, target_name) in entries {
        // Merge updates project support files without introducing template examples.
        if args.merge && target_name == "examples" {
            println!("[INFO] Skipped examples in merge mode");
            continue;
        }
        let source = resources.template.join(source_name);
        if !source.exists() {
            continue;
        }
        let destination = root.join(target_name);
        if destination.exists() && !args.force {
            if args.merge && target_name == ".agents" {
                copy_tree(&source, &destination, true)?;
            } else {
                println!("[INFO] Kept existing: {}", destination.display());
            }
            continue;
        }
        if destination.is_dir() {
            std::fs::remove_dir_all(&destination)?;
        } else if destination.exists() {
            std::fs::remove_file(&destination)?;
        }
        copy_tree(&source, &destination, false)?;
    }
    std::fs::create_dir_all(root.join("images"))?;
    if args.setup {
        setup()?;
    }
    println!("[OK] Initialized manuscript project: {}", root.display());
    Ok(())
}

/// Recursively copy template assets; merge mode never replaces existing user files.
fn copy_tree(source: &Path, destination: &Path, merge: bool) -> Result<()> {
    if source.is_dir() {
        std::fs::create_dir_all(destination)?;
        for entry in std::fs::read_dir(source)? {
            let entry = entry?;
            let name = entry.file_name();
            let name_text = name.to_string_lossy();
            if [
                ".git",
                ".papper",
                ".pmt",
                ".pandoc-cache",
                ".venv",
                "__pycache__",
                "output",
                "tmp",
                "target",
            ]
            .contains(&name_text.as_ref())
                || name_text.ends_with(".pyc")
            {
                continue;
            }
            copy_tree(&entry.path(), &destination.join(name), merge)?;
        }
    } else if !merge || !destination.exists() {
        if let Some(parent) = destination.parent() {
            std::fs::create_dir_all(parent)?;
        }
        std::fs::copy(source, destination)?;
    }
    Ok(())
}

/// Stop the project service and remove outputs, work, and caches, refusing unsafe output paths.
fn clean(args: CleanArgs) -> Result<()> {
    let project = canonical_project(&std::env::current_dir()?)?;
    let output = absolute(&args.output_dir, &project);
    papper_server::stop_project_server(&project)?;
    if output.exists() {
        let resolved = PathBuf::from(display_path(&output.canonicalize()?));
        anyhow::ensure!(
            resolved != project && resolved.starts_with(&project),
            "Refusing to clean unsafe output directory: {}",
            output.display()
        );
        std::fs::remove_dir_all(&output).context("Could not clean generated output")?;
    }
    let state = project_state_dir(&project)?;
    if state.exists() {
        std::fs::remove_dir_all(state)?;
    }
    let legacy_cache = project.join(".pandoc-cache");
    if legacy_cache.exists() {
        let resolved = PathBuf::from(display_path(&legacy_cache.canonicalize()?));
        anyhow::ensure!(
            resolved.starts_with(&project) && resolved != project,
            "Refusing to remove unsafe legacy cache"
        );
        std::fs::remove_dir_all(legacy_cache)?;
    }
    println!("[OK] Clean complete (outputs, work and project caches).");
    Ok(())
}
