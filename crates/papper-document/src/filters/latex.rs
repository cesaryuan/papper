//! Preserve the deterministic LaTeX table and resource-copy filter behavior.

use anyhow::{Context, Result, bail};
use papper_core::resources::ResourcePaths;
use serde_json::{Value, json};
use std::fs;
use std::io::Write;
use std::path::{Path, PathBuf};
use std::process::{Command, Stdio};

use super::{FilterOptions, image_attribute, image_url, set_image_attribute, walk_mut};

/// Extract visible AST text without including image targets or element attributes.
pub fn stringify(value: &Value) -> String {
    if let Some(items) = value.as_array() {
        return items.iter().map(stringify).collect();
    }
    let content = value.get("c").unwrap_or(&Value::Null);
    match value.get("t").and_then(Value::as_str) {
        Some("Str" | "MetaString") => content.as_str().unwrap_or("").to_string(),
        Some("Space" | "SoftBreak" | "LineBreak") => " ".to_string(),
        Some("Code" | "CodeBlock" | "Math" | "RawInline" | "RawBlock") => content
            .get(1)
            .and_then(Value::as_str)
            .unwrap_or("")
            .to_string(),
        Some("Image" | "Link" | "Span" | "Cite") => stringify(&content[1]),
        Some("Quoted") => format!("\"{}\"", stringify(&content[1])),
        Some("Para") => format!("{}\n\n", stringify(content)),
        Some("MetaMap") => content
            .as_object()
            .map(|mapping| mapping.values().map(stringify).collect())
            .unwrap_or_default(),
        Some(_) => stringify(content),
        None => String::new(),
    }
}

/// Copy a resource into the LaTeX output tree while retaining its relative layout.
fn copy_resource(raw: &str, output_root: &Path) -> Result<()> {
    let source = Path::new(raw);
    if !source.is_file() {
        // Missing and remote paths were non-fatal in the old filter; keep JSON stdout clean.
        super::warn(format_args!(
            "[WARN] Resource file not found, leaving unchanged: {raw}"
        ));
        return Ok(());
    }
    let relative: PathBuf = if source.is_absolute() {
        source
            .file_name()
            .context("Resource has no filename")?
            .into()
    } else {
        source.into()
    };
    // Parent-relative references retain their layout because generated TeX
    // contains the original URL and resolves it from the output directory.
    let target = output_root.join(relative);
    if let Some(parent) = target.parent() {
        fs::create_dir_all(parent)?;
    }
    if source.canonicalize().ok() == target.canonicalize().ok() && target.is_file() {
        return Ok(());
    }
    fs::copy(source, &target).with_context(|| {
        format!(
            "Cannot copy LaTeX resource {} to {}",
            source.display(),
            target.display()
        )
    })?;
    Ok(())
}

/// Copy nested file-valued metadata without changing its Pandoc metadata representation.
fn copy_metadata(value: &Value, output_root: &Path) -> Result<()> {
    match value.get("t").and_then(Value::as_str) {
        Some("MetaString" | "MetaInlines") => copy_resource(&stringify(value), output_root),
        Some("MetaList") => {
            if let Some(items) = value["c"].as_array() {
                for item in items {
                    copy_metadata(item, output_root)?;
                }
            }
            Ok(())
        }
        Some("MetaMap") => {
            if let Some(mapping) = value["c"].as_object() {
                for item in mapping.values() {
                    copy_metadata(item, output_root)?;
                }
            }
            Ok(())
        }
        _ => Ok(()),
    }
}

/// Copy metadata, local images, and local file links while preserving all AST paths.
pub fn move_resources(document: &mut Value, options: &FilterOptions) -> Result<()> {
    if let Some(metadata) = document.get("meta").and_then(Value::as_object) {
        for key in [
            "bibliography",
            "csl",
            "reference-doc",
            "reference-docx",
            "template",
            "include-before-body",
            "include-after-body",
            "include-in-header",
            "css",
            "data-dir",
            "extract-media",
            "resource-path",
        ] {
            if let Some(value) = metadata.get(key) {
                copy_metadata(value, &options.latex_target_dir)?;
            }
        }
    }
    walk_mut(document, &mut |node| {
        let kind = node.get("t").and_then(Value::as_str);
        if let Some(url) = image_url(node)
            && (kind == Some("Image")
                || (kind == Some("Link") && url.contains('.') && !url.starts_with("http")))
        {
            copy_resource(url, &options.latex_target_dir)?;
        }
        Ok(())
    })
}

/// Escape literal strings using the existing ordered LaTeX replacement behavior.
fn escaped_literal(raw: &str) -> String {
    let mut text = raw.to_string();
    for (source, target) in [
        ("\\", "\\textbackslash{}"),
        ("_", "\\_"),
        ("%", "\\%"),
        ("$", "\\$"),
        ("#", "\\#"),
        ("&", "\\&"),
        ("{", "\\{"),
        ("}", "\\}"),
    ] {
        text = text.replace(source, target);
    }
    text
}

/// Convert cell inlines to LaTeX while preserving equations and inline formatting.
pub fn extract_text_from_inlines(inlines: &Value) -> String {
    let Some(inlines) = inlines.as_array() else {
        return String::new();
    };
    inlines
        .iter()
        .map(|inline| {
            let content = &inline["c"];
            match inline["t"].as_str().unwrap_or("") {
                "Str" => escaped_literal(content.as_str().unwrap_or("")),
                "Space" => " ".to_string(),
                "Quoted" => format!("\"{}\"", extract_text_from_inlines(&content[1])),
                "Strong" => format!("\\textbf{{{}}}", extract_text_from_inlines(content)),
                "Emph" => format!("\\textit{{{}}}", extract_text_from_inlines(content)),
                "Code" => format!("\\texttt{{{}}}", content[1].as_str().unwrap_or("")),
                "Math" => {
                    let delimiter = if content[0]["t"] == "InlineMath" {
                        "$"
                    } else {
                        "$$"
                    };
                    format!(
                        "{delimiter}{}{delimiter}",
                        content[1].as_str().unwrap_or("")
                    )
                }
                "Image" => format!(
                    "\\includegraphics[width=0.95\\linewidth]{{{}}}",
                    image_url(inline).unwrap_or("")
                ),
                "RawInline" if content[0] == "latex" => {
                    content[1].as_str().unwrap_or("").to_string()
                }
                "RawInline" => String::new(),
                _ => stringify(inline),
            }
        })
        .collect()
}

/// Extract only paragraph/plain cell blocks, preserving the legacy block join spacing.
fn cell_text(cell: &Value) -> String {
    cell.get(4)
        .and_then(Value::as_array)
        .map(|blocks| {
            blocks
                .iter()
                .filter(|block| matches!(block["t"].as_str(), Some("Para" | "Plain")))
                .map(|block| extract_text_from_inlines(&block["c"]))
                .collect::<Vec<_>>()
                .join(" ")
        })
        .unwrap_or_default()
}

/// Extract ordered table cells without mistaking attributes for table content.
fn row_cells(row: &Value) -> Vec<String> {
    row.get(1)
        .and_then(Value::as_array)
        .map(|cells| cells.iter().map(cell_text).collect())
        .unwrap_or_default()
}

/// Remove crossref's generated caption prefix using its existing four-inline boundary.
fn strip_generated_caption(caption: &mut Value) {
    let text = caption.get(1).map(stringify).unwrap_or_default();
    if (text.starts_with("Figure ") || text.starts_with("Table "))
        && let Some(inlines) = caption
            .get_mut(1)
            .and_then(|blocks| blocks.get_mut(0))
            .and_then(|block| block.get_mut("c"))
            .and_then(Value::as_array_mut)
    {
        inlines.drain(..inlines.len().min(4));
    }
}

/// Render a regular tabular float with the same caption, width, and alignment decisions.
fn table_latex(table: &Value) -> Result<Option<String>> {
    let content = table["c"]
        .as_array()
        .context("Pandoc Table content must be an array")?;
    if content.len() < 6 {
        bail!("Pandoc Table is missing caption or row sections")
    }
    let caption = stringify(&content[1][1]);
    let label = content[0][0].as_str().unwrap_or("");
    let mut alignments: Vec<String> = content[2]
        .as_array()
        .map(|columns| {
            columns
                .iter()
                .map(|column| {
                    match column[0]["t"].as_str() {
                        Some("AlignRight") => "r",
                        Some("AlignCenter") => "c",
                        _ => "l",
                    }
                    .to_string()
                })
                .collect()
        })
        .unwrap_or_default();
    let headers = content[3][1]
        .as_array()
        .and_then(|rows| rows.first())
        .map(row_cells)
        .unwrap_or_default();
    let mut rows = Vec::new();
    if let Some(bodies) = content[4].as_array() {
        for body in bodies {
            if let Some(body_rows) = body.get(3).and_then(Value::as_array) {
                rows.extend(body_rows.iter().map(row_cells));
            }
        }
    }
    if headers.is_empty() && rows.is_empty() {
        super::warn(format_args!("[WARN] Empty table found, skipping"));
        return Ok(None);
    }
    for (column, alignment) in alignments.iter_mut().enumerate() {
        if rows.iter().any(|row| {
            row.get(column)
                .is_some_and(|cell| cell.contains("\\includegraphics"))
        }) {
            *alignment = "m{2.8cm}".to_string();
        }
    }
    if alignments.is_empty() {
        alignments.resize(headers.len(), "l".to_string());
    }
    let mut environment = if headers.len() > 5
        || rows
            .iter()
            .flatten()
            .any(|cell| cell.contains("includegraphics"))
    {
        "table*"
    } else {
        "table"
    };
    if let Some(value) = image_attribute(table, &["twocol"]) {
        environment = if value == "false" { "table" } else { "table*" };
    }
    let mut lines = vec![
        format!("\\begin{{{environment}}}[htbp]"),
        "\\centering".to_string(),
        format!("\\caption{{{caption}}}"),
        format!("\\label{{{label}}}"),
        format!("\\begin{{tabular}}{{{}}}", alignments.join(" ")),
        "\\toprule".to_string(),
    ];
    if !headers.is_empty() {
        lines.push(format!(
            "{} \\\\",
            headers
                .iter()
                .map(|header| format!("\\textbf{{{header}}}"))
                .collect::<Vec<_>>()
                .join(" & ")
        ));
        lines.push("\\midrule".to_string());
    }
    for row in rows {
        lines.push(format!("{} \\\\", row.join(" & ")));
    }
    lines.extend([
        "\\bottomrule".to_string(),
        "\\end{tabular}".to_string(),
        format!("\\end{{{environment}}}"),
    ]);
    Ok(Some(lines.join("\n")))
}

/// Find the first nested figure image used by the previous figure layout filter.
fn first_image_mut(node: &mut Value) -> Option<&mut Value> {
    if node.get("t").and_then(Value::as_str) == Some("Image") {
        return Some(node);
    }
    match node {
        Value::Array(items) => items.iter_mut().find_map(first_image_mut),
        Value::Object(mapping) => mapping.values_mut().find_map(first_image_mut),
        _ => None,
    }
}

/// Locate a native writer for the retained figure-to-LaTeX conversion boundary.
fn figure_writer(resources: &ResourcePaths, options: &FilterOptions) -> PathBuf {
    if let Some(command) = &options.pandoc_command {
        return command.clone();
    }
    let name = if cfg!(windows) {
        "pmt-pandoc-worker.exe"
    } else {
        "pmt-pandoc-worker"
    };
    for directory in [
        resources.root.join("bin"),
        resources.root.join("src/pandoc_manuscript/bin"),
        papper_core::paths::tools_bin_dir(),
    ] {
        let command = directory.join(name);
        if command.is_file() {
            return command;
        }
    }
    PathBuf::from("pandoc")
}

/// Convert one figure with the original native Pandoc writer, without Python subprocesses.
fn figure_latex(
    figure: &Value,
    api_version: &Value,
    resources: &ResourcePaths,
    options: &FilterOptions,
) -> Result<String> {
    let mut command = Command::new(figure_writer(resources, options));
    command
        .args(["--from=json", "--to=latex"])
        .stdin(Stdio::piped())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped());
    #[cfg(windows)]
    {
        use std::os::windows::process::CommandExt;
        command.creation_flags(0x0800_0000);
    }
    let mut process = command
        .spawn()
        .context("Cannot start Pandoc figure writer")?;
    let input = json!({"pandoc-api-version": api_version, "meta": {}, "blocks": [figure]});
    process
        .stdin
        .take()
        .context("Missing Pandoc figure input")?
        .write_all(&serde_json::to_vec(&input)?)?;
    let output = process.wait_with_output()?;
    if !output.status.success() {
        bail!(
            "Pandoc figure writer failed: {}",
            String::from_utf8_lossy(&output.stderr)
        )
    }
    // Panflute normalized native Windows newlines and removed the writer's
    // trailing newline before placing this string inside a RawBlock.
    Ok(String::from_utf8(output.stdout)?
        .lines()
        .collect::<Vec<_>>()
        .join("\n"))
}

/// Convert LaTeX tables and two-column figures after the retained crossref/Lua passes.
pub fn convert_tables(
    document: &mut Value,
    format: &str,
    resources: &ResourcePaths,
    options: &FilterOptions,
) -> Result<()> {
    if format != "latex" {
        return Ok(());
    }
    let api_version = document
        .get("pandoc-api-version")
        .cloned()
        .context("Pandoc document is missing API version")?;
    walk_mut(document, &mut |node| {
        match node.get("t").and_then(Value::as_str) {
            Some("Table") => {
                if let Some(caption) = node.get_mut("c").and_then(|content| content.get_mut(1)) {
                    strip_generated_caption(caption);
                }
                if let Some(latex) = table_latex(node)? {
                    *node = json!({"t": "RawBlock", "c": ["latex", latex]});
                }
            }
            Some("Figure") => {
                if let Some(caption) = node.get_mut("c").and_then(|content| content.get_mut(1)) {
                    strip_generated_caption(caption);
                }
                let wide = first_image_mut(node)
                    .is_some_and(|image| image_attribute(image, &["twocol"]) == Some("true"));
                if wide {
                    let latex = figure_latex(node, &api_version, resources, options)?
                        .replace("{figure}", "{figure*}");
                    *node = json!({"t": "RawBlock", "c": ["latex", latex]});
                } else if let Some(image) = first_image_mut(node) {
                    set_image_attribute(image, "width", "95%");
                }
            }
            _ => (),
        }
        Ok(())
    })
}

#[cfg(test)]
mod tests {
    use super::*;

    /// Preserve cell formatting and replace longtable with a usable tabular float.
    #[test]
    fn table_conversion_retains_formatted_content_and_two_column_hint() -> Result<()> {
        let cell = |text: Value| json!([["", [], []], {"t": "AlignDefault"}, 1, 1, [{"t": "Plain", "c": [text]}]]);
        let table = json!({"t": "Table", "c": [["tbl:test", [], [["twocol", "true"]]], [null, [{"t": "Plain", "c": [{"t": "Str", "c": "Results"}]}]], [[{"t": "AlignLeft"}, {"t": "ColWidthDefault"}]], [["", [], []], [[["", [], []], [cell(json!({"t": "Str", "c": "Header"}))]]]], [[["", [], []], 0, [], [[["", [], []], [cell(json!({"t": "Emph", "c": [{"t": "Str", "c": "Value_1"}]}))]]]]], [["", [], []], []]]});
        let rendered = table_latex(&table)?.unwrap();
        assert!(rendered.starts_with("\\begin{table*}[htbp]"));
        assert!(rendered.contains("\\label{tbl:test}"));
        assert!(rendered.contains("\\textbf{Header}"));
        assert!(rendered.contains("\\textit{Value\\_1}"));
        assert!(!rendered.contains("longtable"));
        Ok(())
    }
}
