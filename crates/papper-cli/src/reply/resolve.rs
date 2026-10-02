//! Resolve manuscript cross-references, complete citation clusters and reply syntax.

use anyhow::{Context, Result};
use fancy_regex::Regex as FancyRegex;
use papper_core::metadata::{EffectiveMetadata, PmtSettings};
use papper_core::paths::pandoc_path;
use papper_engine::PandocCli;
use regex::{Captures, Regex};
use serde_json::Value;
use std::collections::{BTreeMap, BTreeSet};
use std::ffi::OsString;
use std::path::Path;

const LABEL: &str = r"[A-Za-z0-9][A-Za-z0-9_:\-]*(?:\.[A-Za-z0-9_:\-]+)*";
const REF_SENTINEL: &str = "PANDOC_REPLY_REF_PROBE";
const CITE_SENTINEL: &str = "PANDOC_REPLY_CITE_PROBE";
const CLUSTER_SENTINEL: &str = "PANDOC_REPLY_CITE_CLUSTER_PROBE";

/// Share immutable manuscript and engine state across the independent reply probes.
pub struct ReplyResolver<'a> {
    pub engine: &'a PandocCli,
    pub manuscript: &'a Path,
    pub metadata: &'a Path,
    pub work: &'a Path,
    pub effective: &'a EffectiveMetadata,
    pub from_format: &'a str,
    pub environment: &'a BTreeMap<String, Option<String>>,
}

/// Compile a fixed internal regular expression with a meaningful invariant.
fn pattern(raw: &str) -> Regex {
    Regex::new(raw).expect("valid reply syntax regex")
}

/// Collect citation keys while excluding manuscript cross-reference prefixes.
fn citation_keys(text: &str) -> Vec<String> {
    let regex = FancyRegex::new(r"(?<![\w:])@([A-Za-z0-9_][A-Za-z0-9_:.#/$%&+?<>~/-]*)")
        .expect("citation regex");
    let mut seen = BTreeSet::new();
    let mut keys = Vec::new();
    for capture in regex.captures_iter(text).flatten() {
        let key = capture[1].to_owned();
        if !["sec:", "fig:", "tbl:", "eq:"]
            .iter()
            .any(|prefix| key.starts_with(prefix))
            && seen.insert(key.clone())
        {
            keys.push(key);
        }
    }
    keys
}

/// Find complete bracketed bibliography clusters in encounter order.
fn citation_clusters(text: &str) -> Vec<String> {
    let mut clusters = Vec::new();
    for capture in pattern(r"\[([^\]\n]*@[^\]\n]*)\]").captures_iter(text) {
        if !citation_keys(&capture[1]).is_empty()
            && !clusters.iter().any(|existing| existing == &capture[0])
        {
            clusters.push(capture[0].to_owned());
        }
    }
    clusters
}

/// Convert Pandoc JSON display inlines to reply-safe Markdown without style markers.
fn inline_text(value: &Value) -> String {
    let content = &value["c"];
    match value["t"].as_str().unwrap_or("") {
        "Str" => content.as_str().unwrap_or("").into(),
        "Space" | "SoftBreak" | "LineBreak" => " ".into(),
        "Code" | "Math" => content
            .as_array()
            .and_then(|items| items.last())
            .and_then(Value::as_str)
            .unwrap_or("")
            .into(),
        "Superscript" => format!("^{}^", inlines_text(content)),
        "Emph" | "Strong" | "SmallCaps" | "Strikeout" | "Subscript" => inlines_text(content),
        "Span" | "Link" | "Cite" | "Quoted" => inlines_text(&content[1]),
        "RawInline" => content[1].as_str().unwrap_or("").into(),
        _ => String::new(),
    }
}

/// Normalize rendered inline whitespace, including non-breaking citation spaces.
fn inlines_text(value: &Value) -> String {
    let text = value
        .as_array()
        .map(|items| items.iter().map(inline_text).collect::<String>())
        .unwrap_or_default();
    text.split_whitespace().collect::<Vec<_>>().join(" ")
}

/// Extract the displayed tail of each probe paragraph without rewriting citations.
fn probe_map(
    document: &Value,
    sentinel: &str,
    labels: &[String],
    unresolved: &[&str],
) -> BTreeMap<String, String> {
    let mut resolved = BTreeMap::new();
    for block in document["blocks"].as_array().into_iter().flatten() {
        if block["t"] != "Para" {
            continue;
        }
        let Some(inlines) = block["c"].as_array() else {
            continue;
        };
        if inlines
            .first()
            .is_none_or(|item| item["t"] != "Str" || item["c"] != sentinel)
        {
            continue;
        }
        let mut index = 1;
        while inlines.get(index).is_some_and(|item| {
            matches!(
                item["t"].as_str(),
                Some("Space" | "SoftBreak" | "LineBreak")
            )
        }) {
            index += 1;
        }
        let Some(label) = inlines
            .get(index)
            .filter(|item| item["t"] == "Str")
            .and_then(|item| item["c"].as_str())
        else {
            continue;
        };
        index += 1;
        let display = inlines_text(&Value::Array(inlines[index..].to_vec()));
        if labels.iter().any(|requested| requested == label)
            && !display.is_empty()
            && !unresolved.iter().any(|marker| display.contains(marker))
        {
            resolved.insert(label.into(), display);
        }
    }
    resolved
}

/// Run the same independent Pandoc probes used by the reference implementation.
fn resolve_probe(
    resolver: &ReplyResolver<'_>,
    labels: &[String],
    payloads: &[String],
    sentinel: &str,
    citations: bool,
) -> Result<BTreeMap<String, String>> {
    let ReplyResolver {
        engine,
        manuscript,
        metadata,
        work,
        from_format,
        environment,
        ..
    } = resolver;
    if labels.is_empty() {
        return Ok(BTreeMap::new());
    }
    anyhow::ensure!(
        manuscript.is_file(),
        "Manuscript source not found for reply references: {}",
        manuscript.display()
    );
    let probe = work.join(format!("{sentinel}.md"));
    let lines = labels
        .iter()
        .zip(payloads)
        .map(|(label, payload)| format!("{sentinel} {label} {payload}"))
        .collect::<Vec<_>>()
        .join("\n\n");
    std::fs::write(&probe, format!("{lines}\n"))?;
    let project = std::env::current_dir()?;
    let resource_path = [manuscript.parent().unwrap_or(&project), project.as_path()]
        .iter()
        .map(|path| pandoc_path(path))
        .collect::<Vec<_>>()
        .join(if cfg!(windows) { ";" } else { ":" });
    let mut args: Vec<OsString> = vec![
        "--metadata-file".into(),
        metadata.as_os_str().to_owned(),
        "--resource-path".into(),
        resource_path.into(),
        "-f".into(),
        from_format.into(),
        "-t".into(),
        "json".into(),
        "--filter".into(),
        "pandoc-crossref".into(),
    ];
    if citations {
        args.push("--citeproc".into());
    }
    args.extend([manuscript.as_os_str().to_owned(), probe.into_os_string()]);
    let result = engine.run(&args, &project, environment)?;
    let document: Value =
        serde_json::from_slice(&result.stdout).context("Invalid reply probe output")?;
    let resolved = probe_map(
        &document,
        sentinel,
        labels,
        if citations { &["???"] } else { &["¿"] },
    );
    for missing in labels.iter().filter(|label| !resolved.contains_key(*label)) {
        eprintln!("[WARN] Reply reference was not resolved from manuscript: {missing}");
    }
    Ok(resolved)
}

/// Return a cross-reference's numeric portion when prose supplies its English prefix.
fn number_only(label: &str, display: &str) -> String {
    let prefix = match label.split(':').next().unwrap_or("") {
        "sec" => "Section",
        "fig" => "Figure",
        "tbl" => "Table",
        "eq" => "Equation",
        _ => return display.into(),
    };
    pattern(&format!(r"(?i)^{}\s+", regex::escape(prefix)))
        .replace(display, "")
        .trim()
        .into()
}

/// Substitute the whole label, protecting dotted labels from shorter prefix matches.
fn replace_references(text: &str, references: &BTreeMap<String, String>) -> String {
    let mut resolved = text.to_owned();
    let mut labels: Vec<_> = references.keys().collect();
    labels.sort_by_key(|label| std::cmp::Reverse(label.len()));
    for label in labels {
        let display = &references[label];
        let prefix = match label.split(':').next().unwrap_or("") {
            "sec" => "Section",
            "fig" => "Figure",
            "tbl" => "Table",
            "eq" => "Equation",
            _ => "",
        };
        let escaped = regex::escape(label);
        let boundary = r"(?![A-Za-z0-9_:\-]|\.(?=[A-Za-z0-9_:\-]))";
        if !prefix.is_empty() {
            let regex = FancyRegex::new(&format!(
                r"(?i)\b{prefix}\s+(?:\[@{escaped}\]|@{escaped}{boundary})"
            ))
            .expect("reference prefix regex");
            resolved = regex
                .replace_all(
                    &resolved,
                    format!("{prefix} {}", number_only(label, display)),
                )
                .into_owned();
        }
        resolved = resolved.replace(&format!("[@{label}]"), display);
        let regex = FancyRegex::new(&format!("@{escaped}{boundary}")).expect("reference regex");
        resolved = regex.replace_all(&resolved, display.as_str()).into_owned();
    }
    resolved
}

/// Preserve already numbered/manual captions while adding manuscript figure/table labels.
fn caption_numbered(caption: &str, kind: &str, label: &str, display: &str) -> bool {
    let prefix = pattern(&format!(
        r"(?i)^(?:{}(?:\s|$)|{kind}\s+(?:\[?@{label}:[A-Za-z0-9]|[A-Za-z]*\d))",
        regex::escape(display)
    ));
    prefix.is_match(caption.trim_start())
}

/// Match actual copied figure definitions, including quoted paragraphs.
fn figure_pattern() -> Regex {
    pattern(&format!(
        r"!\[([^\]\r\n]*)\](\([^\r\n]*?\))[ \t]*(\{{[^}}\r\n]*#(fig:{LABEL})[^}}\r\n]*\}})"
    ))
}

/// Match actual copied table captions without treating literal attribute examples as labels.
fn table_pattern() -> Regex {
    pattern(&format!(
        r"(?im)^([ \t]*(?:>[ \t]*)*(?:Table)?:[ \t]+)(.*?)([ \t]*\{{[^}}\r\n]*#(tbl:{LABEL})[^}}\r\n]*\}}[ \t]*)$"
    ))
}

/// Add original manuscript numbers to copied figure and table definitions.
fn add_caption_numbers(text: &str, references: &BTreeMap<String, String>) -> String {
    let resolved = figure_pattern().replace_all(text, |capture: &Captures<'_>| {
        let Some(display) = references.get(&capture[4]) else {
            return capture[0].into();
        };
        let caption = &capture[1];
        let caption = if caption_numbered(caption, "Figure", "fig", display) {
            caption.into()
        } else {
            format!(
                "{display}{}{caption}",
                if caption.is_empty() { "" } else { " " }
            )
        };
        format!("![{caption}]{}{}", &capture[2], &capture[3])
    });
    table_pattern()
        .replace_all(&resolved, |capture: &Captures<'_>| {
            let Some(display) = references.get(&capture[4]) else {
                return capture[0].into();
            };
            if caption_numbered(&capture[2], "Table", "tbl", display) {
                return capture[0].into();
            }
            format!(
                "{}{display}{}{}{}",
                &capture[1],
                if capture[2].is_empty() { "" } else { " " },
                &capture[2],
                &capture[3]
            )
        })
        .into_owned()
}

/// Match labeled display equations without swallowing triple-dollar boundaries.
fn equation_pattern() -> FancyRegex {
    FancyRegex::new(&format!(
        r"(?s)(?<!\$)\$\$(?!\$)(.*?)(?<!\$)\$\$(?!\$)\s*\{{#(eq:{LABEL})([^}}]*)\}}"
    ))
    .expect("equation regex")
}

/// Format numbered reply equations with the retained Word-tab layout.
fn replace_equations(
    text: &str,
    references: &BTreeMap<String, String>,
    settings: &PmtSettings,
) -> Result<String> {
    let (center, right) = if settings
        .get("docxPageMargins")
        .is_some_and(|value| !value.is_null())
    {
        papper_document::docx::equation_tab_stops(settings)?
    } else {
        (4888, 9746)
    };
    let prefix = format!(
        "<w:pPr><w:tabs><w:tab w:val=\"center\" w:leader=\"none\" w:pos=\"{center}\" /><w:tab w:val=\"right\" w:leader=\"none\" w:pos=\"{right}\" /></w:tabs></w:pPr><w:r><w:tab /></w:r>"
    );
    Ok(equation_pattern()
        .replace_all(text, |capture: &fancy_regex::Captures<'_>| {
            let Some(display) = references.get(&capture[2]) else {
                return capture[0].into();
            };
            let short = number_only(&capture[2], display);
            let number = if short.starts_with('(') && short.ends_with(')') {
                short
            } else {
                format!("({short})")
            };
            let math = pattern(r"[ \t]*\r?\n[ \t]*").replace_all(capture[1].trim(), " ");
            format!("`{prefix}`{{=openxml}}${math}$`<w:r><w:tab /></w:r>`{{=openxml}}{number}")
        })
        .into_owned())
}

/// Replace complete resolved clusters first and protect every unresolved cluster.
fn replace_citations(
    text: &str,
    singles: &BTreeMap<String, String>,
    clusters: &BTreeMap<String, String>,
) -> String {
    let mut protected = Vec::new();
    let mut resolved = pattern(r"\[([^\]\n]*@[^\]\n]*)\]")
        .replace_all(text, |capture: &Captures<'_>| {
            if let Some(display) = clusters.get(&capture[0]) {
                return display.clone();
            }
            let placeholder = format!("@@PMT_CITE_CLUSTER_{}@@", protected.len());
            protected.push(capture[0].to_owned());
            placeholder
        })
        .into_owned();
    let mut keys: Vec<_> = singles.keys().collect();
    keys.sort_by_key(|key| std::cmp::Reverse(key.len()));
    for key in keys {
        let display = &singles[key];
        resolved = resolved.replace(&format!("[@{key}]"), display);
        let regex = FancyRegex::new(&format!(r"(?<![\w:])@{}\b", regex::escape(key)))
            .expect("citation replace regex");
        resolved = regex.replace_all(&resolved, display.as_str()).into_owned();
    }
    for (index, original) in protected.iter().enumerate() {
        resolved = resolved.replace(&format!("@@PMT_CITE_CLUSTER_{index}@@"), original);
    }
    resolved
}

/// Resolve original manuscript numbering without running crossref against the reply itself.
pub fn resolve_reply_markdown(
    text: &str,
    resolver: &ReplyResolver<'_>,
    format_equations: bool,
) -> Result<String> {
    let mut labels: BTreeSet<String> = pattern(&format!(r"@((?:sec|fig|tbl|eq):{LABEL})"))
        .captures_iter(text)
        .map(|capture| capture[1].to_owned())
        .collect();
    for pattern in [figure_pattern(), table_pattern()] {
        for capture in pattern.captures_iter(text) {
            labels.insert(capture[4].into());
        }
    }
    if format_equations {
        for capture in equation_pattern().captures_iter(text).flatten() {
            labels.insert(capture[2].into());
        }
    }
    let labels: Vec<_> = labels.into_iter().collect();
    let ref_payloads = labels
        .iter()
        .map(|label| format!("@{label}"))
        .collect::<Vec<_>>();
    let references = resolve_probe(resolver, &labels, &ref_payloads, REF_SENTINEL, false)?;
    let citations: Vec<_> = citation_keys(text)
        .into_iter()
        .collect::<BTreeSet<_>>()
        .into_iter()
        .collect();
    let cite_payloads = citations
        .iter()
        .map(|key| format!("[@{key}]"))
        .collect::<Vec<_>>();
    let singles = resolve_probe(resolver, &citations, &cite_payloads, CITE_SENTINEL, true)?;
    let clusters = citation_clusters(text);
    let cluster_ids = clusters
        .iter()
        .enumerate()
        .map(|(index, _)| index.to_string())
        .collect::<Vec<_>>();
    let cluster_values = resolve_probe(resolver, &cluster_ids, &clusters, CLUSTER_SENTINEL, true)?;
    let cluster_map = clusters
        .iter()
        .zip(cluster_ids)
        .filter_map(|(cluster, id)| {
            cluster_values
                .get(&id)
                .map(|display| (cluster.clone(), display.clone()))
        })
        .collect();
    let resolved = if format_equations {
        replace_equations(text, &references, &resolver.effective.pmt_settings)?
    } else {
        text.into()
    };
    let resolved = add_caption_numbers(&resolved, &references);
    let resolved = replace_references(&resolved, &references);
    Ok(replace_citations(&resolved, &singles, &cluster_map))
}

/// Keep Markdown semantics while removing DOCX-only wrappers, labels and image data.
pub fn render_reply_txt_markdown(markdown: &str) -> String {
    let opening =
        pattern(r#"^\s*:::\s*\{[^}\n]*custom-style\s*=\s*['"]Reply to Reviewers['"][^}\n]*\}\s*$"#);
    let mut depth = 0;
    let mut text = String::new();
    for line in markdown.split_inclusive('\n') {
        if opening.is_match(line.trim()) {
            depth += 1;
        } else if depth > 0 && line.trim() == ":::" {
            depth -= 1;
        } else {
            text.push_str(line);
        }
    }
    text = pattern(r"(?i)<br\s*/?>")
        .replace_all(&text, "")
        .into_owned();
    // Only list markers followed by whitespace are escaped numbers; prose such
    // as `1\\.example` must remain literal, matching the submission TXT contract.
    text = FancyRegex::new(r"(?m)^(\s*\d+)\\\.(?=\s)")
        .expect("escaped ordered-list marker regex")
        .replace_all(&text, "$1.")
        .into_owned();
    let ordered = pattern(r"^\s*\d+\.\s");
    let mut lines: Vec<String> = Vec::new();
    for line in text.lines() {
        if ordered.is_match(line)
            && lines
                .last()
                .is_some_and(|previous| !previous.trim().is_empty() && !ordered.is_match(previous))
        {
            lines.push(String::new());
        }
        lines.push(line.into());
    }
    text = lines.join("\n");
    text = pattern(r"!\[([^\]]*)\]\(([^)]*)\)(?:\s*\{[^}]*\})?")
        .replace_all(&text, |capture: &Captures<'_>| {
            let alt = capture[1].trim();
            let target = capture[2].split_whitespace().next().unwrap_or("");
            format!(
                "[Image: {}]",
                if !alt.is_empty() {
                    alt
                } else if !target.is_empty() {
                    target
                } else {
                    "image"
                }
            )
        })
        .into_owned();
    text = equation_pattern()
        .replace_all(&text, |capture: &fancy_regex::Captures<'_>| {
            format!("$${}$$", &capture[1])
        })
        .into_owned();
    text = pattern(
        r"(?m)^(\s*(?:Table|Figure)?:\s+.*?)[ \t]*\{#(?:tbl|fig):[A-Za-z0-9][^}\r\n]*\}[ \t]*$",
    )
    .replace_all(&text, "$1")
    .into_owned();
    text = pattern(r"(?m)^[ \t]*\{#(?:eq|fig|tbl):[A-Za-z0-9][^}]*\}[ \t]*\r?\n?")
        .replace_all(&text, "")
        .into_owned();
    text = pattern(r"(?:[ \t]*\r?\n){3,}")
        .replace_all(&text, "\n\n")
        .trim_matches(['\r', '\n'])
        .into();
    if !text.is_empty() {
        text.push('\n');
    }
    text
}
