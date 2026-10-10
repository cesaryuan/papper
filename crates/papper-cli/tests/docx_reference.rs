//! Verify reference export, custom Word formatting and diagnostics on public CLI output.

use anyhow::Result;
use std::fs;
use std::io::{Read, Write};
use std::path::{Path, PathBuf};
use std::process::{Command, Output};

/// Locate authored runtime resources independently of the temporary manuscript directory.
fn resources() -> PathBuf {
    Path::new(env!("CARGO_MANIFEST_DIR")).join("../..")
}

/// Run the real CLI with isolated state and no ambient manuscript/style/reference selection.
fn cli(project: &Path, arguments: &[&str], reference: Option<&Path>) -> Output {
    cli_with_resources(project, arguments, reference, &resources())
}

/// Select a runtime fixture to verify export works even with an unusable Pandoc installation.
fn cli_with_resources(
    project: &Path,
    arguments: &[&str],
    reference: Option<&Path>,
    runtime: &Path,
) -> Output {
    let mut command = Command::new(env!("CARGO_BIN_EXE_papper"));
    command
        .current_dir(project)
        .args(arguments)
        .env("PAPPER_RESOURCE_ROOT", runtime)
        .env("PAPPER_HOME", project.join(".papper-home"))
        .env("PAPPER_DISABLE_UPDATE_CHECK", "1")
        .env_remove("PMT_MANUSCRIPT_FILE")
        .env_remove("PMT_STYLE_FILE")
        .env_remove("PMT_REFERENCE_DOC")
        .env_remove("PMT_ENABLE_DOCX_POSTPROCESS");
    if let Some(reference) = reference {
        command.env("PMT_REFERENCE_DOC", reference);
    }
    command.output().expect("Papper must run")
}

/// Export without a manuscript or functioning engine and preserve existing manuscript outputs.
#[test]
fn reference_export_skips_build_and_does_not_require_manuscript() -> Result<()> {
    let project = tempfile::tempdir()?;
    let runtime = tempfile::tempdir()?;
    for relative in [
        "pandoc/pandoc-html.yml",
        "pandoc/manuscript-template/reference-doc.docx",
        "defaults/style.yml",
        "defaults/style-cn.yml",
    ] {
        let destination = runtime.path().join(relative);
        fs::create_dir_all(destination.parent().unwrap())?;
        fs::copy(resources().join(relative), destination)?;
    }
    fs::create_dir_all(runtime.path().join(".pmt/pandoc-worker"))?;
    fs::write(
        runtime.path().join(".pmt/pandoc-worker/current.json"),
        r#"{"executable":"missing-engine.exe"}"#,
    )?;
    fs::write(
        project.path().join("style.yml"),
        "mathtype: true\ndocxStyle: null\ndocxPageMargins: {left: 1in}\n",
    )?;
    let previous = project.path().join("output/docx/manuscript.docx");
    fs::create_dir_all(previous.parent().unwrap())?;
    fs::write(&previous, b"previous manuscript output")?;
    let output = successful(cli_with_resources(
        project.path(),
        &["build", "docx", "--export-reference-doc"],
        None,
        runtime.path(),
    ));
    assert!(!output.contains("Building"));
    assert_eq!(fs::read(&previous)?, b"previous manuscript output");
    assert!(
        part(
            &project.path().join("reference-doc.docx"),
            "word/document.xml"
        )?
        .contains("w:left=\"1440\"")
    );

    // Invalid body/reply references must not trigger conversion or resolution;
    // only the YAML settings are needed to export the configured reference.
    fs::write(
        project.path().join("reply.md"),
        "---\nreply: missing-manuscript.md\npapperSettings:\n  docxPageMargins: {left: 2in}\n---\n\n@fig:unresolved\n",
    )?;
    successful(cli_with_resources(
        project.path(),
        &[
            "build",
            "docx",
            "reply.md",
            "--export-reference-doc",
            "exports/reply-reference.docx",
        ],
        None,
        runtime.path(),
    ));
    assert!(
        part(
            &project.path().join("exports/reply-reference.docx"),
            "word/document.xml"
        )?
        .contains("w:left=\"2880\"")
    );
    assert!(!project.path().join("output/docx/reply.docx").exists());
    Ok(())
}

/// Keep real build diagnostics in assertion failures.
fn successful(output: Output) -> String {
    assert!(
        output.status.success(),
        "stdout: {}\nstderr: {}",
        String::from_utf8_lossy(&output.stdout),
        String::from_utf8_lossy(&output.stderr)
    );
    String::from_utf8(output.stdout).expect("CLI output must be UTF-8")
}

/// Inspect a Word package part without relying on Papper's XML/package implementation.
fn part(path: &Path, name: &str) -> Result<String> {
    let mut archive = zip::ZipArchive::new(fs::File::open(path)?)?;
    let mut text = String::new();
    archive.by_name(name)?.read_to_string(&mut text)?;
    Ok(text)
}

/// Create a journal reference with a distinctive custom paragraph style.
fn journal_reference(source: &Path, destination: &Path) -> Result<()> {
    let mut archive = zip::ZipArchive::new(fs::File::open(source)?)?;
    let mut writer = zip::ZipWriter::new(fs::File::create(destination)?);
    for index in 0..archive.len() {
        let mut entry = archive.by_index(index)?;
        let mut bytes = Vec::new();
        entry.read_to_end(&mut bytes)?;
        if entry.name() == "word/styles.xml" {
            let text = String::from_utf8(bytes)?;
            bytes = text.replace(
                "</w:styles>",
                "<w:style w:type=\"paragraph\" w:styleId=\"JournalProbe\"><w:name w:val=\"Journal Probe\"/><w:basedOn w:val=\"BodyText\"/><w:rPr><w:color w:val=\"123ABC\"/></w:rPr></w:style></w:styles>",
            ).into_bytes();
        }
        writer.start_file(entry.name(), zip::write::SimpleFileOptions::default())?;
        writer.write_all(&bytes)?;
    }
    writer.finish()?;
    Ok(())
}

/// Catch destructive export aliases and invalid target options before any document build.
#[test]
fn invalid_reference_options_preserve_inputs_and_outputs() -> Result<()> {
    let project = tempfile::tempdir()?;
    let manuscript = "# Example\n\nPreserved manuscript.\n";
    fs::write(project.path().join("paper.md"), manuscript)?;
    let output = project.path().join("output.docx");
    fs::write(&output, b"previous document")?;
    fs::write(project.path().join("reference.docx"), b"reference input")?;
    for arguments in [
        vec!["build", "html", "paper.md", "--export-reference-doc"],
        vec!["build", "docx", "missing.md", "--export-reference-doc"],
        vec![
            "build",
            "json",
            "paper.md",
            "--reference-doc",
            "missing.docx",
        ],
        vec![
            "build",
            "docx",
            "paper.md",
            "--reference-doc",
            "missing.docx",
        ],
        vec![
            "build",
            "docx",
            "paper.md",
            "--export-reference-doc",
            "./paper.md",
        ],
        vec![
            "build",
            "docx",
            "paper.md",
            "-o",
            "output.docx",
            "--export-reference-doc",
            "new/../output.docx",
        ],
        vec![
            "build",
            "docx",
            "paper.md",
            "--reference-doc",
            "reference.docx",
            "--export-reference-doc",
            "./reference.docx",
        ],
    ] {
        let result = cli(project.path(), &arguments, None);
        assert!(
            !result.status.success(),
            "Unexpected success: {arguments:?}"
        );
        assert!(!String::from_utf8_lossy(&result.stdout).contains("Building"));
        assert_eq!(
            fs::read_to_string(project.path().join("paper.md"))?,
            manuscript
        );
        assert_eq!(fs::read(&output)?, b"previous document");
        assert_eq!(
            fs::read(project.path().join("reference.docx"))?,
            b"reference input"
        );
    }
    Ok(())
}

/// Detect dropped journal styles, incorrect margin exports and CLI/environment precedence.
#[test]
#[ignore = "Requires a prepared Pandoc engine; run with --ignored for real DOCX builds"]
fn reference_exports_preserve_custom_styles_and_effective_margins() -> Result<()> {
    let project = tempfile::tempdir()?;
    fs::write(
        project.path().join("paper.md"),
        "# Example\n\n::: {custom-style=\"Journal Probe\"}\nJournal formatting.\n:::\n",
    )?;
    fs::write(
        project.path().join("style.yml"),
        "docxPageMargins: null\ndocxStyle: null\n",
    )?;
    successful(cli(
        project.path(),
        &[
            "build",
            "docx",
            "paper.md",
            "--no-mathtype",
            "--export-reference-doc",
        ],
        None,
    ));
    let bundled = resources().join("pandoc/manuscript-template/reference-doc.docx");
    let exported = project.path().join("reference-doc.docx");
    assert!(
        fs::read(&exported)? == fs::read(&bundled)?,
        "Unmodified reference must be exported byte-for-byte"
    );
    assert!(!project.path().join("output/docx/paper.docx").exists());

    let custom = project.path().join("journal.docx");
    journal_reference(&exported, &custom)?;
    let original = fs::read(&custom)?;
    fs::write(
        project.path().join("style.yml"),
        "docxPageMargins:\n  left: 1in\n  right: 2in\n",
    )?;
    successful(cli(
        project.path(),
        &[
            "build",
            "docx",
            "paper.md",
            "--no-mathtype",
            "-o",
            "output.docx",
            "--reference-doc",
            "journal.docx",
            "--export-reference-doc",
            "exports/active.docx",
        ],
        Some(&project.path().join("missing-env-reference.docx")),
    ));
    assert!(!project.path().join("output.docx").exists());
    successful(cli(
        project.path(),
        &[
            "build",
            "docx",
            "paper.md",
            "--no-mathtype",
            "-o",
            "output.docx",
            "--reference-doc",
            "journal.docx",
        ],
        None,
    ));
    let active = project.path().join("exports/active.docx");
    for document in [&active, &project.path().join("output.docx")] {
        let xml = part(document, "word/document.xml")?;
        assert!(
            xml.contains("w:left=\"1440\""),
            "Left margin missing from {document:?}"
        );
        assert!(
            xml.contains("w:right=\"2880\""),
            "Right margin missing from {document:?}"
        );
        assert!(part(document, "word/styles.xml")?.contains("123ABC"));
    }
    let output_xml = part(&project.path().join("output.docx"), "word/document.xml")?;
    assert!(output_xml.contains("w:pStyle w:val=\"JournalProbe\""));
    assert!(
        fs::read(&custom)? == original,
        "Custom reference input must remain unchanged"
    );

    fs::write(
        project.path().join("style.yml"),
        "docxPageMargins: null\ndocxStyle: null\n",
    )?;
    successful(cli(
        project.path(),
        &[
            "build",
            "docx",
            "paper.md",
            "--no-mathtype",
            "--export-reference-doc",
            "exports/retained.docx",
        ],
        Some(&custom),
    ));
    assert!(
        fs::read(project.path().join("exports/retained.docx"))? == original,
        "Null margins must preserve the custom reference bytes"
    );
    fs::write(project.path().join("invalid.docx"), b"invalid Word package")?;
    let failed = cli(
        project.path(),
        &[
            "build",
            "docx",
            "paper.md",
            "--reference-doc",
            "invalid.docx",
            "--export-reference-doc",
            "exports/retained.docx",
        ],
        None,
    );
    assert!(!failed.status.success());
    assert!(
        fs::read(project.path().join("exports/retained.docx"))? == original,
        "A failed reference preparation must preserve the previous export"
    );
    fs::write(
        project.path().join("reply.md"),
        "---\nreply: paper.md\n---\n\n# Response\n\nThank you for the review.\n",
    )?;
    successful(cli(
        project.path(),
        &[
            "build",
            "docx",
            "reply.md",
            "--no-mathtype",
            "--reference-doc",
            "journal.docx",
            "--export-reference-doc",
            "exports/reply.docx",
        ],
        None,
    ));
    assert!(
        part(
            &project.path().join("exports/reply.docx"),
            "word/styles.xml"
        )?
        .contains("123ABC"),
        "Reply builds must export styles from the selected reference"
    );
    assert!(
        fs::read(&custom)? == original,
        "Reply builds must preserve the reference input"
    );
    Ok(())
}

/// Doctor must succeed outside manuscript projects and when only one conventional file exists.
#[test]
#[ignore = "Requires a prepared Pandoc engine; run with --ignored for real diagnostics"]
fn doctor_does_not_require_conventional_project_files() -> Result<()> {
    let project = tempfile::tempdir()?;
    for name in [None, Some("manuscript.md"), Some("style.yml")] {
        if let Some(name) = name {
            fs::write(project.path().join(name), "")?;
        }
        let output = successful(cli(project.path(), &["doctor"], None));
        assert!(!output.contains("[ERROR] project manuscript.md"));
        assert!(!output.contains("[ERROR] project style.yml"));
        if let Some(name) = name {
            fs::remove_file(project.path().join(name))?;
        } else {
            assert!(output.contains("[INFO] project manuscript.md"));
            assert!(output.contains("[INFO] project style.yml"));
        }
    }
    Ok(())
}
