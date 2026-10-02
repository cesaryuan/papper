//! Prepare line sources and map unique reviewer regexes to actual PDF line numbers.

#[path = "pdf.rs"]
mod pdf;
#[cfg(windows)]
#[path = "word.rs"]
mod word;

use anyhow::{Context, Result};
use fancy_regex::Regex as FancyRegex;
use papper_core::paths::{atomic_write, display_path, project_state_dir};
use regex::{Captures, Regex};
use serde_json::Value;
use sha2::{Digest, Sha256};
use std::collections::{BTreeMap, BTreeSet};
use std::io::Read;
use std::path::{Path, PathBuf};
use std::process::Command;

/// Write PDF extraction JSON in the isolated helper, keeping diagnostics on stderr.
pub fn extract_pdf_command(path: &Path) -> Result<()> {
    println!("{}", pdf::extract(path)?);
    Ok(())
}

/// Hash layout-relevant DOCX parts while ignoring ZIP and generated core timestamps.
fn docx_digest(path: &Path) -> Result<String> {
    let mut archive = zip::ZipArchive::new(std::fs::File::open(path)?)?;
    let mut parts = BTreeMap::new();
    let clock = Regex::new(
        r"(?s)(<(?:[\w.-]+:)?(?:created|modified)\b[^>]*>).*?(</(?:[\w.-]+:)?(?:created|modified)>)",
    )?;
    for index in 0..archive.len() {
        let mut entry = archive.by_index(index)?;
        if entry.is_dir() {
            continue;
        }
        let name = entry.name().to_owned();
        let mut data = Vec::new();
        entry.read_to_end(&mut data)?;
        if name == "docProps/core.xml" {
            data = clock
                .replace_all(&String::from_utf8(data)?, "$1$2")
                .as_bytes()
                .to_vec();
        }
        parts.insert(name, data);
    }
    let mut digest = Sha256::new();
    for (name, data) in parts {
        digest.update((name.len() as u64).to_be_bytes());
        digest.update(name.as_bytes());
        digest.update((data.len() as u64).to_be_bytes());
        digest.update(&data);
    }
    Ok(digest
        .finalize()
        .iter()
        .map(|byte| format!("{byte:02x}"))
        .collect())
}

/// Export a layout-identical DOCX through the platform backend, caching successful PDFs.
fn docx_pdf(source: &Path, identity: &Path, work: &Path) -> Result<PathBuf> {
    let project = std::env::current_dir()?;
    // Markdown intermediates have random temporary paths; key the original source instead.
    let identity = identity.canonicalize()?;
    let backend = if cfg!(windows) { "word-com" } else { "soffice" };
    let key: String = Sha256::digest(
        format!(
            "rust-reply-v1:{}:{backend}:{}:{}",
            env!("CARGO_PKG_VERSION"),
            display_path(&identity),
            docx_digest(source)?
        )
        .as_bytes(),
    )
    .iter()
    .map(|byte| format!("{byte:02x}"))
    .collect();
    let cache = project_state_dir(&project)?
        .join("cache/reply/line-source/pdf")
        .join(format!("{key}.pdf"));
    let output = work.join(format!("{key}.pdf"));
    if cache.is_file() {
        std::fs::copy(&cache, &output)?;
        return Ok(output);
    }
    eprintln!(
        "[LINE] Converting DOCX line source to PDF: {}",
        source.display()
    );
    #[cfg(windows)]
    word::export(source, &output)?;
    #[cfg(not(windows))]
    {
        let result = Command::new("soffice")
            .args(["--headless", "--convert-to", "pdf", "--outdir"])
            .arg(work)
            .arg(source)
            .output()
            .context("LibreOffice soffice is required for DOCX reply line sources")?;
        anyhow::ensure!(
            result.status.success(),
            "soffice DOCX-to-PDF conversion failed: {}",
            String::from_utf8_lossy(&result.stderr)
        );
        let generated = work
            .join(source.file_stem().unwrap_or_default())
            .with_extension("pdf");
        anyhow::ensure!(
            generated.is_file(),
            "soffice conversion did not create PDF: {}",
            generated.display()
        );
        std::fs::rename(generated, &output)?;
    }
    atomic_write(&cache, &std::fs::read(&output)?)?;
    Ok(output)
}

/// Build Markdown intermediates natively and preserve explicitly provided PDF inputs.
fn prepare_pdf(source: &Path, work: &Path) -> Result<PathBuf> {
    anyhow::ensure!(
        source.is_file(),
        "Line source file not found: {}",
        source.display()
    );
    match source
        .extension()
        .and_then(|raw| raw.to_str())
        .unwrap_or("")
        .to_lowercase()
        .as_str()
    {
        "pdf" => Ok(source.to_path_buf()),
        "docx" | "docm" => docx_pdf(source, source, work),
        "md" | "markdown" => {
            let output = work.join("line-source.docx");
            let result = Command::new(std::env::current_exe()?)
                .args(["build", "docx"])
                .arg(source)
                .arg("-o")
                .arg(&output)
                .output()?;
            anyhow::ensure!(
                result.status.success(),
                "Markdown line-source DOCX build failed: {}\n{}",
                String::from_utf8_lossy(&result.stdout),
                String::from_utf8_lossy(&result.stderr)
            );
            docx_pdf(&output, source, work)
        }
        _ => anyhow::bail!(
            "Line source must be a Markdown, PDF, or Word document: {}",
            source.display()
        ),
    }
}

/// Represent one extracted line with its physical page coordinates.
#[derive(Clone)]
struct TextLine {
    text: String,
    bbox: [f64; 4],
}

/// Normalize extraction whitespace without changing searchable punctuation or content.
fn normalized(text: &str) -> String {
    text.split_whitespace().collect::<Vec<_>>().join(" ")
}

/// Accept MuPDF rectangle arrays or its structured-text x/y/w/h object representation.
fn rectangle(value: &Value) -> Option<[f64; 4]> {
    if let Some(items) = value.as_array().filter(|items| items.len() == 4) {
        return Some([
            items[0].as_f64()?,
            items[1].as_f64()?,
            items[2].as_f64()?,
            items[3].as_f64()?,
        ]);
    }
    let x = value["x"].as_f64()?;
    let y = value["y"].as_f64()?;
    Some([x, y, x + value["w"].as_f64()?, y + value["h"].as_f64()?])
}

/// Collect actual MuPDF lines without conflating block text and line text.
fn collect_lines(value: &Value, lines: &mut Vec<TextLine>) {
    if let Some(text) = value["text"].as_str()
        && let Some(bbox) = rectangle(&value["bbox"])
    {
        let text = normalized(text);
        if !text.is_empty() {
            lines.push(TextLine { text, bbox });
        }
        return;
    }
    if let Some(items) = value.as_array() {
        for child in items {
            collect_lines(child, lines);
        }
    }
    if let Some(items) = value.as_object() {
        for child in items.values() {
            if child.is_object() || child.is_array() {
                collect_lines(child, lines);
            }
        }
    }
}

/// Compute vertical centers for the same tolerance used by existing reply line matching.
fn center(line: &TextLine) -> f64 {
    (line.bbox[1] + line.bbox[3]) / 2.0
}

/// Pair left-margin numbers with body lines, rejecting footnote numbering resets.
fn layout_numbered(document: &Value) -> Vec<(usize, usize, String)> {
    let mut numbered = Vec::new();
    let mut previous = 0;
    for (page, value) in document["pages"]
        .as_array()
        .into_iter()
        .flatten()
        .enumerate()
    {
        let mut lines = Vec::new();
        collect_lines(&value["layout"], &mut lines);
        let numbers: Vec<_> = lines
            .iter()
            .filter_map(|line| line.text.parse::<usize>().ok().map(|number| (number, line)))
            .collect();
        let body: Vec<_> = lines
            .iter()
            .filter(|line| line.text.parse::<usize>().is_err())
            .collect();
        let left = body
            .iter()
            .map(|line| line.bbox[0])
            .fold(f64::INFINITY, f64::min);
        let mut groups: Vec<Vec<&TextLine>> = Vec::new();
        for line in body {
            if let Some(group) = groups
                .iter_mut()
                .find(|group| (center(group[0]) - center(line)).abs() <= 1.0)
            {
                group.push(line)
            } else {
                groups.push(vec![line])
            }
        }
        let body: Vec<_> = groups
            .iter()
            .filter_map(|group| {
                group
                    .iter()
                    .max_by_key(|line| line.text.chars().count())
                    .copied()
            })
            .collect();
        for (number, line) in numbers {
            if line.bbox[0] >= left - 2.0 || number <= previous {
                continue;
            }
            if let Some(text) = body
                .iter()
                .filter(|candidate| {
                    (center(candidate) - center(line)).abs()
                        <= 3.0f64.max((line.bbox[3] - line.bbox[1]) * 0.75)
                })
                .max_by_key(|candidate| candidate.text.chars().count())
            {
                numbered.push((number, page + 1, text.text.clone()));
                previous = number;
            }
        }
    }
    numbered
}

/// Preserve text-stream order for producers whose numbers follow each body line.
fn text_numbered(document: &Value) -> Vec<(usize, usize, String)> {
    let mut numbered = Vec::new();
    for (page, value) in document["pages"]
        .as_array()
        .into_iter()
        .flatten()
        .enumerate()
    {
        let lines: Vec<_> = value["text"].as_str().unwrap_or("").lines().collect();
        let mut index = 0;
        while index + 1 < lines.len() {
            let text = normalized(lines[index]);
            if !text.is_empty()
                && let Ok(number) = lines[index + 1].trim().parse::<usize>()
            {
                numbered.push((number, page + 1, text));
                index += 2;
                continue;
            }
            index += 1;
        }
    }
    numbered
}

/// Invoke the isolated PDF helper and apply producer-specific extraction rules.
fn numbered_lines(path: &Path) -> Result<Vec<(usize, usize, String)>> {
    let result = Command::new(std::env::current_exe()?)
        .arg("__pdf_extract")
        .arg(path)
        .output()?;
    anyhow::ensure!(
        result.status.success(),
        "Could not extract PDF {}: {}",
        path.display(),
        String::from_utf8_lossy(&result.stderr)
    );
    let document: Value =
        serde_json::from_slice(&result.stdout).context("Invalid PDF extraction response")?;
    let source = document["metadata"]
        .as_object()
        .map(|items| {
            items
                .values()
                .filter_map(Value::as_str)
                .collect::<Vec<_>>()
                .join(" ")
                .to_lowercase()
        })
        .unwrap_or_default();
    if source.contains("libreoffice") {
        return Ok(layout_numbered(&document));
    }
    if source.contains("microsoft") && source.contains("word") {
        let lines = layout_numbered(&document);
        if !lines.is_empty() {
            return Ok(lines);
        }
        eprintln!(
            "[WARN] Microsoft Word PDF layout matching found no numbered lines; falling back to text order"
        );
    }
    Ok(text_numbered(&document))
}

/// Replace only unique case-insensitive regex hits, retaining unresolved user placeholders.
pub fn resolve_line_regexes(markdown: &str, source: &Path, work: &Path) -> Result<String> {
    let placeholder = Regex::new(r"\(Line `([^`]+)`\)")?;
    let patterns: BTreeSet<_> = placeholder
        .captures_iter(markdown)
        .map(|capture| capture[1].to_owned())
        .collect();
    if patterns.is_empty() {
        return Ok(markdown.into());
    }
    let pdf = prepare_pdf(source, work)?;
    let lines = numbered_lines(&pdf)?;
    let mut text = String::new();
    let mut offsets = Vec::new();
    for (number, page, line) in lines {
        if !text.is_empty() {
            text.push(' ')
        }
        offsets.push((text.len(), number, page));
        text.push_str(&line);
    }
    let mut replacements = BTreeMap::new();
    for pattern in patterns {
        let expression = match FancyRegex::new(&format!("(?i){pattern}")) {
            Ok(value) => value,
            Err(error) => {
                eprintln!("[WARN] Invalid regex in reply line placeholder: `{pattern}` ({error})");
                continue;
            }
        };
        let matches = expression
            .find_iter(&text)
            .collect::<std::result::Result<Vec<_>, _>>();
        let matches = match matches {
            Ok(value) => value,
            Err(error) => {
                eprintln!("[WARN] Invalid regex in reply line placeholder: `{pattern}` ({error})");
                continue;
            }
        };
        if matches.len() != 1 {
            eprintln!(
                "[WARN] Regex `{pattern}` matched {} PDF locations; leaving placeholder unchanged",
                matches.len()
            );
            continue;
        }
        let number = offsets
            .iter()
            .rev()
            .find(|(offset, _, _)| *offset <= matches[0].start())
            .map(|(_, number, _)| *number)
            .unwrap_or(0);
        replacements.insert(pattern, format!("(Line {number})"));
    }
    Ok(placeholder
        .replace_all(markdown, |capture: &Captures<'_>| {
            replacements
                .get(&capture[1])
                .cloned()
                .unwrap_or_else(|| capture[0].to_owned())
        })
        .into_owned())
}
