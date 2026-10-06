//! Apply author metadata and table margins while retaining manuscript whitespace.

use anyhow::{Result, bail};
use regex::Regex;
use serde_json::{Map, Value};
use std::collections::{BTreeSet, HashMap};
use std::sync::LazyLock;

use super::styles::{StyleKind, custom_style_css_text};

/// Ordered HTML node representation, retaining source text between elements.
#[derive(Clone)]
enum HtmlNode {
    Element {
        name: String,
        attributes: Vec<(String, String)>,
        children: Vec<HtmlNode>,
    },
    Text(String),
    Comment(String),
}

/// Normalized authors and their ordered, shared institution registry.
struct AuthorInformation {
    authors: Vec<Map<String, Value>>,
    affiliations: Vec<(String, String)>,
}

impl HtmlNode {
    /// Create an HTML element with ordered attributes and children.
    fn element(name: &str, attributes: &[(&str, &str)], children: Vec<Self>) -> Self {
        Self::Element {
            name: name.into(),
            attributes: attributes
                .iter()
                .map(|(key, value)| ((*key).into(), (*value).into()))
                .collect(),
            children,
        }
    }

    /// Return an attribute from an element without inventing a default value.
    fn attribute(&self, key: &str) -> Option<&str> {
        match self {
            Self::Element { attributes, .. } => attributes
                .iter()
                .find(|(name, _)| name == key)
                .map(|(_, value)| value.as_str()),
            _ => None,
        }
    }

    /// Match a whitespace-delimited CSS class token.
    fn has_class(&self, class: &str) -> bool {
        self.attribute("class")
            .is_some_and(|classes| classes.split_whitespace().any(|value| value == class))
    }

    /// Find the first descendant element with the supplied HTML tag name.
    fn find_mut(&mut self, tag: &str) -> Option<&mut Self> {
        if let Self::Element { name, .. } = self
            && name == tag
        {
            return Some(self);
        }
        if let Self::Element { children, .. } = self {
            for child in children {
                if let Some(found) = child.find_mut(tag) {
                    return Some(found);
                }
            }
        }
        None
    }
}

/// Recognize HTML void elements whose closing tags are omitted by libxml2.
fn is_void(name: &str) -> bool {
    matches!(
        name,
        "area"
            | "base"
            | "br"
            | "col"
            | "embed"
            | "hr"
            | "img"
            | "input"
            | "link"
            | "meta"
            | "param"
            | "source"
            | "track"
            | "wbr"
    )
}

/// Recognize raw text elements so JavaScript and CSS remain byte-for-byte text.
fn is_raw(name: &str) -> bool {
    matches!(name, "script" | "style")
}

/// Find a tag's closing angle bracket while tolerating angle brackets in quotes.
fn tag_end(text: &str, start: usize) -> usize {
    let mut quote = None;
    for (offset, byte) in text.as_bytes()[start..].iter().enumerate() {
        if let Some(active) = quote {
            if *byte == active {
                quote = None;
            }
        } else if matches!(*byte, b'\'' | b'"') {
            quote = Some(*byte);
        } else if *byte == b'>' {
            return start + offset;
        }
    }
    text.len().saturating_sub(1)
}

/// Parse ordered, quoted or unquoted HTML attributes and normalize line endings.
fn parse_open_tag(tag: &str) -> (String, Vec<(String, String)>) {
    let bytes = tag.as_bytes();
    let mut position = 0;
    while position < bytes.len()
        && !bytes[position].is_ascii_whitespace()
        && bytes[position] != b'/'
    {
        position += 1;
    }
    let name = tag[..position].to_lowercase();
    let mut attributes = Vec::new();
    while position < bytes.len() {
        while position < bytes.len()
            && (bytes[position].is_ascii_whitespace() || bytes[position] == b'/')
        {
            position += 1;
        }
        let start = position;
        while position < bytes.len()
            && !bytes[position].is_ascii_whitespace()
            && !matches!(bytes[position], b'=' | b'/')
        {
            position += 1;
        }
        if position == start {
            break;
        }
        let key = tag[start..position].to_lowercase();
        while position < bytes.len() && bytes[position].is_ascii_whitespace() {
            position += 1;
        }
        let mut value = String::new();
        if position < bytes.len() && bytes[position] == b'=' {
            position += 1;
            while position < bytes.len() && bytes[position].is_ascii_whitespace() {
                position += 1;
            }
            if position < bytes.len() && matches!(bytes[position], b'\'' | b'"') {
                let quote = bytes[position];
                position += 1;
                let start = position;
                while position < bytes.len() && bytes[position] != quote {
                    position += 1;
                }
                value = tag[start..position].to_owned();
                if position < bytes.len() {
                    position += 1;
                }
            } else {
                let start = position;
                while position < bytes.len() && !bytes[position].is_ascii_whitespace() {
                    position += 1;
                }
                value = tag[start..position].to_owned();
            }
        }
        value = html_escape::decode_html_entities(&value).into_owned();
        if !attributes.iter().any(|(existing, _)| existing == &key) {
            attributes.push((key, value));
        }
    }
    (name, attributes)
}

/// Parse children until their closing tag while keeping all Pandoc whitespace.
fn parse_children(text: &str, position: &mut usize, parent: &str) -> Vec<HtmlNode> {
    let mut children = Vec::new();
    while *position < text.len() {
        let remaining = &text[*position..];
        if is_raw(parent) {
            let marker = format!("</{parent}");
            let lower = remaining.to_ascii_lowercase();
            let count = lower.find(&marker).unwrap_or(remaining.len());
            if count > 0 {
                children.push(HtmlNode::Text(remaining[..count].to_owned()));
                *position += count;
            }
            if count == remaining.len() {
                break;
            }
        }
        let remaining = &text[*position..];
        if remaining.starts_with("<!--") {
            let count = remaining
                .find("-->")
                .map(|end| end + 3)
                .unwrap_or(remaining.len());
            children.push(HtmlNode::Comment(remaining[..count].to_owned()));
            *position += count;
        } else if remaining.starts_with("</") {
            let end = tag_end(text, *position + 2);
            let name = text[*position + 2..end].trim().to_lowercase();
            if name == parent {
                *position = end + 1;
                break;
            }
            // Optional HTML end tags terminate an open cell, row, or paragraph.
            if !parent.is_empty() {
                break;
            }
            *position = end + 1;
        } else if remaining.starts_with("<!") || remaining.starts_with("<?") {
            *position = tag_end(text, *position + 2) + 1;
        } else if remaining.starts_with('<')
            && remaining
                .as_bytes()
                .get(1)
                .is_some_and(u8::is_ascii_alphabetic)
        {
            let end = tag_end(text, *position + 1);
            let (name, attributes) = parse_open_tag(&text[*position + 1..end]);
            if matches!(parent, "p" | "li" | "tr" | "td" | "th")
                && (name == parent
                    || (matches!(parent, "td" | "th") && matches!(name.as_str(), "td" | "th")))
            {
                break;
            }
            *position = end + 1;
            let nested = if is_void(&name) {
                Vec::new()
            } else {
                parse_children(text, position, &name)
            };
            children.push(HtmlNode::Element {
                name,
                attributes,
                children: nested,
            });
        } else {
            let count = if remaining.starts_with('<') {
                1
            } else {
                remaining.find('<').unwrap_or(remaining.len())
            };
            let raw = &remaining[..count];
            children.push(HtmlNode::Text(
                html_escape::decode_html_entities(raw).into_owned(),
            ));
            *position += count;
        }
    }
    children
}

/// Parse a complete document or wrap a fragment in the same HTML/body structure.
fn parse_document(text: &str) -> Result<HtmlNode> {
    if text.is_empty() {
        bail!("Cannot post-process an empty HTML document")
    }
    let mut position = 0;
    let mut children = parse_children(text, &mut position, "");
    if let Some(index) = children
        .iter()
        .position(|node| matches!(node, HtmlNode::Element {name, ..} if name == "html"))
    {
        return Ok(children.remove(index));
    }
    children.retain(|node| !matches!(node, HtmlNode::Text(text) if text.trim().is_empty()));
    Ok(HtmlNode::element(
        "html",
        &[],
        vec![HtmlNode::element("body", &[], children)],
    ))
}

/// Escape HTML text while retaining Unicode characters decoded by the parser.
fn escape_text(text: &str) -> String {
    text.replace('&', "&amp;")
        .replace('<', "&lt;")
        .replace('>', "&gt;")
}

/// Serialize an HTML attribute using libxml2's quote choice and entity escaping.
fn render_attribute(name: &str, value: &str) -> String {
    if matches!(
        name,
        "checked"
            | "compact"
            | "declare"
            | "defer"
            | "disabled"
            | "ismap"
            | "multiple"
            | "nohref"
            | "noresize"
            | "noshade"
            | "nowrap"
            | "readonly"
            | "selected"
    ) {
        return format!(" {name}");
    }
    let quote = if value.contains('"') && !value.contains('\'') {
        '\''
    } else {
        '"'
    };
    let value = escape_text(value).replace(quote, if quote == '"' { "&quot;" } else { "&#39;" });
    format!(" {name}={quote}{value}{quote}")
}

/// Serialize the existing whitespace; generated author blocks supply explicit breaks.
fn render_node(node: &HtmlNode, raw: bool, output: &mut String) {
    match node {
        HtmlNode::Text(text) => {
            output.push_str(&if raw { text.clone() } else { escape_text(text) })
        }
        HtmlNode::Comment(text) => output.push_str(text),
        HtmlNode::Element {
            name,
            attributes,
            children,
        } => {
            output.push('<');
            output.push_str(name);
            for (key, value) in attributes {
                output.push_str(&render_attribute(key, value));
            }
            output.push('>');
            let mixed_children = children.len() > 1;
            let block_parent = matches!(
                name.as_str(),
                "html"
                    | "head"
                    | "body"
                    | "div"
                    | "blockquote"
                    | "table"
                    | "thead"
                    | "tbody"
                    | "tfoot"
                    | "tr"
                    | "td"
                    | "th"
                    | "ul"
                    | "ol"
                    | "dl"
                    | "li"
                    | "dt"
                    | "dd"
                    | "form"
                    | "fieldset"
            );
            if block_parent
                && mixed_children
                && matches!(children.first(), Some(HtmlNode::Element { .. }))
            {
                output.push('\n');
            }
            for (index, child) in children.iter().enumerate() {
                render_node(child, is_raw(name), output);
                if let HtmlNode::Element {
                    name: child_name, ..
                } = child
                {
                    let block_child = matches!(
                        child_name.as_str(),
                        "html"
                            | "head"
                            | "body"
                            | "div"
                            | "p"
                            | "h1"
                            | "h2"
                            | "h3"
                            | "h4"
                            | "h5"
                            | "h6"
                            | "blockquote"
                            | "table"
                            | "thead"
                            | "tbody"
                            | "tfoot"
                            | "tr"
                            | "ul"
                            | "ol"
                            | "dl"
                            | "li"
                            | "dt"
                            | "dd"
                            | "form"
                            | "fieldset"
                    );
                    let next_element =
                        matches!(children.get(index + 1), Some(HtmlNode::Element { .. }));
                    let last = index + 1 == children.len();
                    // libxml2 also breaks after an inline child at the end of
                    // a mixed table cell or div, even when prose preceded it.
                    if (block_child && next_element) || (last && block_parent && mixed_children) {
                        output.push('\n');
                    }
                }
            }
            if !is_void(name) {
                output.push_str("</");
                output.push_str(name);
                output.push('>');
            }
        }
    }
}

/// Reproduce scalar Python str values used by historical author metadata.
fn metadata_text(value: &Value) -> String {
    match value {
        Value::String(text) => text.clone(),
        Value::Null => "None".into(),
        Value::Bool(true) => "True".into(),
        Value::Bool(false) => "False".into(),
        _ => value.to_string(),
    }
}

/// Apply Python-style truthiness for metadata fields rather than string parsing.
fn truthy(value: Option<&Value>) -> bool {
    match value {
        None | Some(Value::Null) => false,
        Some(Value::Bool(value)) => *value,
        Some(Value::String(value)) => !value.is_empty(),
        Some(Value::Array(value)) => !value.is_empty(),
        Some(Value::Object(value)) => !value.is_empty(),
        Some(Value::Number(value)) => value.as_f64() != Some(0.0),
    }
}

/// Convert a one-based index into Excel-like lowercase affiliation labels.
fn alpha_label(mut index: usize) -> String {
    let mut label = Vec::new();
    while index > 0 {
        index -= 1;
        label.push((b'a' + (index % 26) as u8) as char);
        index /= 26;
    }
    label.into_iter().rev().collect()
}

/// Register an inline institution while preserving existing keyed institutions.
fn register_affiliation(
    text: &str,
    affiliations: &mut Vec<(String, String)>,
    keys: &mut HashMap<String, String>,
) -> String {
    if let Some(key) = keys.get(text) {
        return key.clone();
    }
    let key = alpha_label(affiliations.len() + 1);
    if let Some((_, existing)) = affiliations
        .iter_mut()
        .find(|(existing, _)| existing == &key)
    {
        *existing = text.into();
    } else {
        affiliations.push((key.clone(), text.into()));
    }
    keys.insert(text.into(), key.clone());
    key
}

/// Normalize inline or keyed author institutions to the current stable labels.
fn normalize_author_metadata(metadata: &Value) -> AuthorInformation {
    let mut affiliations = Vec::new();
    let mut keys_by_text = HashMap::new();
    let raw_affiliations = metadata
        .get("affiliations")
        .or_else(|| metadata.get("affiliation"));
    if let Some(Value::Object(raw)) = raw_affiliations {
        for (key, value) in raw {
            let text = metadata_text(value).trim().to_owned();
            if !text.is_empty() {
                affiliations.push((key.clone(), text.clone()));
                keys_by_text.insert(text, key.clone());
            }
        }
    }
    let mut authors = Vec::new();
    if let Some(raw) = metadata
        .get("authors")
        .or_else(|| metadata.get("author"))
        .and_then(Value::as_array)
    {
        for raw_author in raw {
            let Some(mut author) = raw_author.as_object().cloned() else {
                continue;
            };
            let raw_keys = author
                .get("affiliations")
                .or_else(|| author.get("affiliation"));
            let values = match raw_keys {
                Some(Value::String(text)) => vec![Value::String(text.clone())],
                Some(Value::Array(values)) => values.clone(),
                _ => Vec::new(),
            };
            let mut keys = Vec::new();
            for value in values {
                let text = metadata_text(&value).trim().to_owned();
                if text.is_empty() {
                    continue;
                }
                let key = if affiliations.iter().any(|(key, _)| key == &text) {
                    text
                } else {
                    register_affiliation(&text, &mut affiliations, &mut keys_by_text)
                };
                keys.push(Value::String(key));
            }
            author.insert("affiliations".into(), Value::Array(keys));
            authors.push(author);
        }
    }
    AuthorInformation {
        authors,
        affiliations,
    }
}

/// Build the first corresponding author's custom or generated contact sentence.
fn author_footnote(author: &Map<String, Value>, affiliations: &[(String, String)]) -> String {
    if let Some(Value::String(text)) = author.get("corresponding") {
        return text.trim().into();
    }
    if !truthy(author.get("corresponding")) {
        return String::new();
    }
    let name = author.get("name").map(metadata_text).unwrap_or_default();
    let mut text = format!("Correspondence to: Dr. {name}").trim().to_owned();
    if truthy(author.get("title")) {
        text.push_str(&format!(" ({})", metadata_text(&author["title"])));
    }
    if let Some(key) = author
        .get("affiliations")
        .and_then(Value::as_array)
        .and_then(|keys| keys.first())
        .and_then(Value::as_str)
        && let Some((_, institution)) = affiliations.iter().find(|(candidate, _)| candidate == key)
    {
        text.push_str(&format!(", {institution}"));
    }
    if truthy(author.get("email")) {
        text.push_str(&format!(". E-mail: {}.", metadata_text(&author["email"])));
    }
    text
}

/// Construct author elements and let libxml2-compatible sibling rules add spacing.
fn author_block(authors: &[Map<String, Value>], affiliations: &[(String, String)]) -> HtmlNode {
    let mut line = Vec::new();
    for (index, author) in authors.iter().enumerate() {
        if index > 0 {
            line.push(HtmlNode::Text(", ".into()));
        }
        line.push(HtmlNode::element(
            "span",
            &[("class", "author-name")],
            vec![HtmlNode::Text(
                author.get("name").map(metadata_text).unwrap_or_default(),
            )],
        ));
        if let Some(keys) = author
            .get("affiliations")
            .and_then(Value::as_array)
            .filter(|keys| !keys.is_empty())
        {
            line.push(HtmlNode::element(
                "sup",
                &[],
                vec![HtmlNode::Text(
                    keys.iter().map(metadata_text).collect::<Vec<_>>().join(","),
                )],
            ));
        }
        if truthy(author.get("corresponding")) {
            line.push(HtmlNode::element(
                "sup",
                &[],
                vec![HtmlNode::Text("*".into())],
            ));
        }
    }
    // A single author paragraph stays compact in lxml; explicit breaks here
    // previously changed raw CLI/server parity when authors had no affiliation.
    let mut children = vec![HtmlNode::element("p", &[("class", "author")], line)];
    let mut sorted = affiliations.to_vec();
    sorted.sort_by(|(left, _), (right, _)| {
        let numeric = |value: &str| !value.is_empty() && value.chars().all(char::is_numeric);
        (!numeric(left), left).cmp(&(!numeric(right), right))
    });
    for (key, text) in sorted {
        children.push(HtmlNode::element(
            "p",
            &[("class", "affiliation")],
            vec![
                HtmlNode::element("sup", &[], vec![HtmlNode::Text(key)]),
                HtmlNode::Text(format!(" {text}")),
            ],
        ));
    }
    if let Some(footnote) = authors
        .iter()
        .map(|author| author_footnote(author, affiliations))
        .find(|text| !text.is_empty())
    {
        children.push(HtmlNode::element(
            "p",
            &[("class", "corresponding-author")],
            vec![
                HtmlNode::element("sup", &[], vec![HtmlNode::Text("*".into())]),
                HtmlNode::Text(footnote),
            ],
        ));
    }
    HtmlNode::element("div", &[("class", "papper-authors")], children)
}

/// Remove prior generated blocks and their element tails before reinsertion.
fn remove_author_blocks(node: &mut HtmlNode) {
    if let HtmlNode::Element { children, .. } = node {
        let mut index = 0;
        while index < children.len() {
            if children[index].has_class("papper-authors") {
                children.remove(index);
                if matches!(children.get(index), Some(HtmlNode::Text(_))) {
                    children.remove(index);
                }
            } else {
                remove_author_blocks(&mut children[index]);
                index += 1;
            }
        }
    }
}

/// Insert immediately after the title, retaining the title's existing tail first.
fn insert_after_title(node: &mut HtmlNode, block: &HtmlNode) -> bool {
    if let HtmlNode::Element { children, .. } = node {
        for index in 0..children.len() {
            if matches!(&children[index], HtmlNode::Element {name, ..} if name == "h1")
                && children[index].has_class("title")
            {
                let insertion = if matches!(children.get(index + 1), Some(HtmlNode::Text(_))) {
                    index + 2
                } else {
                    index + 1
                };
                children.insert(insertion, block.clone());
                return true;
            }
            if insert_after_title(&mut children[index], block) {
                return true;
            }
        }
    }
    false
}

/// Insert the normalized author and affiliation block into the document body.
fn insert_author_info(document: &mut HtmlNode, metadata: &Value) {
    let AuthorInformation {
        authors,
        affiliations,
    } = normalize_author_metadata(metadata);
    if authors.is_empty() {
        return;
    }
    let Some(body) = document.find_mut("body") else {
        return;
    };
    remove_author_blocks(body);
    let block = author_block(&authors, &affiliations);
    if !insert_after_title(body, &block)
        && let HtmlNode::Element { children, .. } = body
    {
        let insertion = if matches!(children.first(), Some(HtmlNode::Text(_))) {
            1
        } else {
            0
        };
        children.insert(insertion, block);
    }
}

/// Read underscore/hyphen and data-prefixed spellings of a table attribute.
fn table_attribute<'a>(table: &'a HtmlNode, name: &str) -> Option<&'a str> {
    let hyphen = name.replace('_', "-");
    [
        format!("data-{name}"),
        format!("data-{hyphen}"),
        name.into(),
        hyphen,
    ]
    .iter()
    .find_map(|key| table.attribute(key))
}

/// Convert supported non-negative table dimensions to CSS points.
fn css_points(raw: &str) -> Result<f64> {
    static DIMENSION: LazyLock<Regex> = LazyLock::new(|| {
        Regex::new(r"(?i)^\s*(\d+(?:\.\d+)?)\s*(cm|mm|in|pt)?\s*$").expect("dimension regex")
    });
    let Some(captures) = DIMENSION.captures(raw) else {
        bail!("expected a non-negative value with cm, mm, in, or pt")
    };
    let number = captures[1].parse::<f64>()?;
    let unit = captures
        .get(2)
        .map(|item| item.as_str().to_lowercase())
        .unwrap_or_else(|| "pt".into());
    Ok(number
        * match unit.as_str() {
            "cm" => 72.0 / 2.54,
            "mm" => 72.0 / 25.4,
            "in" => 72.0,
            _ => 1.0,
        })
}

/// Format a point value to four decimal places without redundant trailing zeroes.
fn format_css_points(value: f64) -> String {
    format!(
        "{}pt",
        format!("{value:.4}")
            .trim_end_matches('0')
            .trim_end_matches('.')
    )
}

/// Resolve all and directional margins before mutating a table's style.
fn table_margin_values(table: &HtmlNode) -> Result<Vec<(&'static str, f64)>> {
    let all = table_attribute(table, "cell_margin")
        .map(css_points)
        .transpose()?;
    let mut values = Vec::new();
    for side in ["top", "right", "bottom", "left"] {
        let configured = table_attribute(table, &format!("cell_margin_{side}"))
            .map(css_points)
            .transpose()?;
        if let Some(value) = configured.or(all) {
            values.push((side, value));
        }
    }
    Ok(values)
}

/// Add margin variables while retaining unrelated inline styles on each table.
fn apply_table_cell_margins(node: &mut HtmlNode) {
    if matches!(node, HtmlNode::Element {name, ..} if name == "table") {
        match table_margin_values(node) {
            Ok(values) if !values.is_empty() => {
                let existing = node
                    .attribute("style")
                    .unwrap_or("")
                    .trim()
                    .trim_end_matches(';');
                // Reprocessing must replace generated variables rather than
                // append duplicates; this keeps the postprocessor idempotent.
                let mut declarations = vec![existing.to_owned()];
                for (side, _) in &values {
                    let variable = format!("--pmt-table-cell-margin-{side}");
                    let pattern = Regex::new(&format!(
                        r"(?m)(^|;)\s*{}\s*:[^;]*(?:;|$)",
                        regex::escape(&variable)
                    ))
                    .expect("Generated table margin pattern is valid");
                    declarations[0] = pattern.replace_all(&declarations[0], "$1").into_owned();
                }
                declarations[0] = declarations[0].trim().trim_end_matches(';').to_owned();
                declarations.retain(|value| !value.is_empty());
                declarations.extend(values.into_iter().map(|(side, value)| {
                    format!(
                        "--pmt-table-cell-margin-{side}: {}",
                        format_css_points(value)
                    )
                }));
                let style = format!("{};", declarations.join("; "));
                if let HtmlNode::Element { attributes, .. } = node {
                    if let Some((_, value)) = attributes.iter_mut().find(|(key, _)| key == "style")
                    {
                        *value = style;
                    } else {
                        attributes.push(("style".into(), style));
                    }
                }
            }
            Err(error) => eprintln!(
                "[WARN] Ignoring invalid cell margin on {}: {error}",
                node.attribute("id").unwrap_or("unnamed table")
            ),
            _ => {}
        }
    }
    if let HtmlNode::Element { children, .. } = node {
        for child in children {
            apply_table_cell_margins(child);
        }
    }
}

/// Apply author information and table margins to standalone HTML in memory.
pub fn postprocess_html_text(
    html: &str,
    metadata: &Value,
    skip_author_info: bool,
) -> Result<String> {
    postprocess_html(html, metadata, skip_author_info, None, None)
}

/// Apply used custom Word styles alongside the shared HTML postprocessing steps.
pub fn postprocess_html_text_with_styles(
    html: &str,
    metadata: &Value,
    skip_author_info: bool,
    reference_xml: &str,
) -> Result<String> {
    postprocess_html(html, metadata, skip_author_info, Some(reference_xml), None)
}

/// Resolve custom-style ancestry using the effective settings applied to named Word styles.
pub fn postprocess_html_text_with_style_settings(
    html: &str,
    metadata: &Value,
    skip_author_info: bool,
    reference_xml: &str,
    settings: &papper_core::metadata::PmtSettings,
) -> Result<String> {
    postprocess_html(
        html,
        metadata,
        skip_author_info,
        Some(reference_xml),
        Some(settings),
    )
}

/// Collect actual body attributes, ignoring style/script text and HTML comments.
fn collect_custom_styles(
    node: &mut HtmlNode,
    used: &mut BTreeSet<(StyleKind, String)>,
    paragraph_scope: Option<&str>,
    direct_wrapper: bool,
) {
    // Pandoc's styled Div can contain lists/quotes or another styled Div.
    // Materialize the nearest scope on nested paragraphs so CSS rule ordering
    // cannot make an outer paragraph style override a nested custom style.
    if matches!(node, HtmlNode::Element { name, .. } if name == "p")
        && node.attribute("data-custom-style").is_none()
        && !direct_wrapper
        && let Some(scope) = paragraph_scope
        && let HtmlNode::Element { attributes, .. } = node
    {
        attributes.push(("data-custom-style".into(), scope.to_owned()));
    }
    let own_style = node.attribute("data-custom-style").map(str::to_owned);
    if let HtmlNode::Element { name, children, .. } = node {
        if is_raw(name) {
            return;
        }
        if let Some(style) = &own_style {
            let kind = match name.as_str() {
                "table" => StyleKind::Table,
                "div" | "p" => StyleKind::Paragraph,
                _ => StyleKind::Character,
            };
            used.insert((kind, style.clone()));
        }
        let direct_wrapper = name == "div" && own_style.is_some();
        let scope = if matches!(name.as_str(), "figcaption" | "caption") {
            // A styled body wrapper must not replace the dedicated caption
            // paragraph style inferred for nested figures and tables.
            None
        } else if direct_wrapper {
            own_style.as_deref()
        } else {
            paragraph_scope
        };
        for child in children {
            collect_custom_styles(child, used, scope, direct_wrapper);
        }
    }
}

/// Replace generated custom CSS after defaults but before authored header includes.
fn apply_custom_styles(
    document: &mut HtmlNode,
    xml: &str,
    metadata: &Value,
    settings: Option<&papper_core::metadata::PmtSettings>,
) -> Result<()> {
    let mut used = BTreeSet::new();
    if let Some(body) = document.find_mut("body") {
        collect_custom_styles(body, &mut used, None, false);
    }
    let css = if used.is_empty() {
        String::new()
    } else {
        let records = if let Some(settings) = settings {
            papper_core::style_values::normalize_docx_style_settings(settings)?.unwrap_or_default()
        } else {
            Vec::new()
        };
        custom_style_css_text(xml, &used, &records)?
    };
    // Preview mode may return a body fragment without Pandoc's head. Create a
    // head only when a used custom style needs generated CSS.
    if document.find_mut("head").is_none()
        && !css.is_empty()
        && let HtmlNode::Element { name, children, .. } = document
        && name == "html"
    {
        // The prepared reference include carries effective Table Text overrides
        // even when the worker omits standalone headers in preview mode.
        let mut styles = Vec::new();
        if let Some(includes) = metadata.get("header-includes").and_then(Value::as_array) {
            for include in includes.iter().filter_map(Value::as_str) {
                let mut position = 0;
                styles.extend(
                    parse_children(include, &mut position, "")
                        .into_iter()
                        .filter(|node| node.attribute("id") == Some("pmt-reference-styles")),
                );
            }
        }
        children.insert(0, HtmlNode::element("head", &[], styles));
    }
    if let Some(HtmlNode::Element { children, .. }) = document.find_mut("head") {
        children.retain(|node| node.attribute("id") != Some("pmt-custom-styles"));
        if !css.is_empty() {
            let position = children.iter().position(|node| node.attribute("id") == Some("pmt-reference-styles"))
                .map(|index| index + 1)
                // Older/custom templates may lack the generated reference block.
                // Inserting after their first stylesheet keeps later user CSS effective.
                .or_else(|| children.iter().position(|node| matches!(node, HtmlNode::Element { name, .. } if name == "style")).map(|index| index + 1))
                .unwrap_or(0);
            children.insert(
                position,
                HtmlNode::element(
                    "style",
                    &[("id", "pmt-custom-styles")],
                    vec![HtmlNode::Text(format!("\n{css}\n"))],
                ),
            );
        }
    }
    Ok(())
}

/// Parse once, apply optional reference styling, and retain manuscript whitespace.
fn postprocess_html(
    html: &str,
    metadata: &Value,
    skip_author_info: bool,
    reference_xml: Option<&str>,
    settings: Option<&papper_core::metadata::PmtSettings>,
) -> Result<String> {
    let normalized = html.replace("\r\n", "\n").replace('\r', "\n");
    let mut document = parse_document(&normalized)?;
    if !skip_author_info {
        insert_author_info(&mut document, metadata);
    }
    apply_table_cell_margins(&mut document);
    if let Some(xml) = reference_xml {
        apply_custom_styles(&mut document, xml, metadata, settings)?;
    }
    let mut output = "<!DOCTYPE html>\n".to_owned();
    render_node(&document, false, &mut output);
    output.push('\n');
    Ok(output)
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    /// Configured body indentation must reach descendants while explicit child zero wins.
    #[test]
    fn custom_paragraphs_inherit_configured_parent_spacing() -> Result<()> {
        let xml = r#"<w:styles xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
          <w:style w:type="paragraph" w:styleId="Body"><w:name w:val="Body Text"/></w:style>
          <w:style w:type="paragraph" w:styleId="After"><w:name w:val="Para After Table"/><w:basedOn w:val="Body"/><w:pPr><w:spacing w:before="120"/></w:pPr></w:style>
          <w:style w:type="paragraph" w:styleId="Where"><w:name w:val="Para Where"/><w:basedOn w:val="Body"/><w:pPr><w:ind w:firstLineChars="0"/></w:pPr></w:style>
        </w:styles>"#;
        let source = r#"<html><head></head><body><main class="pmt-page"><div data-custom-style="Para After Table"><p>After</p></div><div data-custom-style="Para Where"><p>Where</p></div></main></body></html>"#;
        let settings = papper_core::metadata::PmtSettings::from_mapping(
            json!({"docxStyle": {"正文文本": {"firstLineIndentChars": 2, "paragraphSpacing": {"before": "0pt", "after": "0pt"}}}}).as_object().unwrap()
        )?;
        let output =
            postprocess_html_text_with_style_settings(source, &json!({}), true, xml, &settings)?;
        let formatting = regex::Regex::new(r"(?m)^  /\*[\s\S]*?\*/\n")?.replace_all(&output, "");
        assert!(formatting.contains(":is(.pmt-page, body) div[data-custom-style=\"Para After Table\"] > p,\n:is(.pmt-page, body) p[data-custom-style=\"Para After Table\"],\n:is(.pmt-page, body) div[data-custom-style=\"Para Where\"] > p,\n:is(.pmt-page, body) p[data-custom-style=\"Para Where\"] {\n  margin-top: 0pt;\n  margin-bottom: 0pt;\n  text-indent: 2em;\n}"));
        assert!(formatting.contains(":is(.pmt-page, body) p[data-custom-style=\"Para After Table\"] {\n  margin-top: 6pt;\n}"));
        assert!(formatting.contains(
            ":is(.pmt-page, body) p[data-custom-style=\"Para Where\"] {\n  text-indent: 0em;\n}"
        ));
        assert_eq!(
            postprocess_html_text_with_style_settings(&output, &json!({}), true, xml, &settings)?,
            output
        );
        Ok(())
    }

    /// Verify used styles, inheritance, scoping, border resets, and editable output together.
    #[test]
    fn custom_word_styles_follow_document_usage_and_reference_edits() -> Result<()> {
        let xml = r#"<w:styles xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
          <w:style w:type="paragraph" w:styleId="Body"><w:name w:val="Body Text"/><w:pPr><w:spacing w:before="100" w:after="120" w:line="360"/><w:ind w:firstLineChars="200"/></w:pPr><w:rPr><w:rFonts w:ascii="Times New Roman"/><w:sz w:val="24"/></w:rPr></w:style>
          <w:style w:type="paragraph" w:styleId="Note"><w:name w:val="Note &amp; Details"/><w:basedOn w:val="Body"/><w:pPr><w:spacing w:after="0"/></w:pPr><w:rPr><w:color w:val="123456"/></w:rPr></w:style>
          <w:style w:type="character" w:styleId="NoteChar"><w:name w:val="Note &amp; Details"/><w:rPr><w:rFonts w:ascii="Arial"/><w:sz w:val="18"/><w:color w:val="ABCDEF"/></w:rPr></w:style>
          <w:style w:type="paragraph" w:styleId="Unused"><w:name w:val="Unused style"/><w:rPr><w:color w:val="654321"/></w:rPr></w:style>
          <w:style w:type="table" w:styleId="Grid"><w:name w:val="Grid"/><w:tblPr><w:tblBorders><w:top w:val="single" w:sz="16"/><w:insideH w:val="dashed" w:sz="4"/></w:tblBorders><w:tblCellMar><w:left w:w="100"/><w:top w:w="80"/></w:tblCellMar></w:tblPr><w:tblStylePr w:type="firstRow"><w:tcPr><w:tcBorders><w:top w:val="double" w:sz="24" w:color="123456"/><w:bottom w:val="single" w:sz="8"/></w:tcBorders></w:tcPr></w:tblStylePr></w:style>
          <w:style w:type="table" w:styleId="Child"><w:name w:val="Child Grid"/><w:basedOn w:val="Grid"/><w:tblPr><w:tblBorders><w:top w:val="nil"/></w:tblBorders><w:tblCellMar><w:left w:type="nil"/></w:tblCellMar></w:tblPr><w:tblStylePr w:type="firstRow"><w:tcPr><w:tcBorders><w:bottom w:val="nil"/></w:tcBorders></w:tcPr></w:tblStylePr></w:style>
          <w:style w:type="table" w:styleId="NoBorder"><w:name w:val="No Border"/></w:style>
          <w:style w:type="table" w:styleId="Hairline"><w:name w:val="Hairline"/><w:basedOn w:val="Grid"/><w:tblStylePr w:type="firstRow"><w:tcPr><w:tcBorders><w:bottom w:val="single" w:sz="0"/></w:tcBorders></w:tcPr></w:tblStylePr></w:style>
        </w:styles>"#;
        let source = r#"<html><head><style id="pmt-reference-styles">table { --pmt-table-text-before: 7pt; }</style><style id="user">p { color: green; }</style></head><body><main class="pmt-page">
          <!-- <div data-custom-style="Unused style">not an element</div> -->
          <div data-custom-style="Note &amp; Details"><p>A <span data-custom-style="Note &amp; Details">note</span></p><blockquote><p>Nested note</p></blockquote><figure><figcaption><p>Caption stays independent</p></figcaption></figure></div>
          <table data-custom-style="Child Grid" data-cell_margin_left="9pt"><thead><tr><th>Head</th></tr></thead><tbody><tr><td>One</td></tr><tr><td>Two</td></tr></tbody></table>
          <table data-custom-style="No Border"><tbody><tr><td>Three</td></tr></tbody></table>
          <table data-custom-style="Hairline"><thead><tr><th>Head</th></tr></thead><tbody><tr><td>Four</td></tr></tbody></table>
        </main></body></html>"#;
        let output = postprocess_html_text_with_styles(source, &json!({}), true, xml)?;
        let document = parse_document(&output)?;
        let mut document = document;
        let head = document.find_mut("head").unwrap();
        let HtmlNode::Element { children, .. } = head else {
            unreachable!()
        };
        let generated = children
            .iter()
            .position(|node| node.attribute("id") == Some("pmt-custom-styles"))
            .unwrap();
        let user = children
            .iter()
            .position(|node| node.attribute("id") == Some("user"))
            .unwrap();
        assert!(generated < user);
        assert!(!output.contains("Word style: \"Unused style\""));
        assert!(output.contains("font-family: \"Times New Roman\", serif;"));
        assert!(output.contains("line-height: 1.5;"));
        assert!(output.contains("margin-top: 5pt;\n  margin-bottom: 6pt;"));
        // Assert actual declarations without coupling formatting to source comments.
        let formatting = regex::Regex::new(r"(?m)^  /\*[\s\S]*?\*/\n")?.replace_all(&output, "");
        assert!(formatting.contains(":is(.pmt-page, body) div[data-custom-style=\"Note & Details\"] > p,\n:is(.pmt-page, body) p[data-custom-style=\"Note & Details\"] {\n  margin-bottom: 0pt;\n}"));
        assert!(output.contains("text-indent: 2em;"));
        assert!(
            output.contains("font-size: 9pt;\n  font-family: \"Arial\", serif;\n  color: #ABCDEF;")
        );
        assert!(output.contains("<p data-custom-style=\"Note &amp; Details\">Nested note</p>"));
        assert!(output.contains("<figcaption><p>Caption stays independent</p></figcaption>"));
        assert!(output.contains("--pmt-table-cell-margin-top: 4pt;"));
        assert!(output.contains("--pmt-table-cell-margin-left: 0pt;"));
        assert!(
            output.contains("border-top: 0.5pt dashed var(--pmt-table-border-color, #1a1a1a);")
        );
        assert_eq!(
            output.matches("  border-top: 3pt double #123456;").count(),
            2
        );
        assert!(formatting.contains("table[data-custom-style=\"Child Grid\"] > thead > tr:first-child > :is(td, th) {\n  border-bottom: none;\n}"));
        assert!(formatting.contains("table[data-custom-style=\"Hairline\"] > thead > tr:first-child > :is(td, th) {\n  border-bottom: 0.5pt solid var(--pmt-table-border-color, #1a1a1a);\n}"));
        assert!(output.contains("table[data-custom-style=\"No Border\"]"));
        assert!(output.contains("style=\"--pmt-table-cell-margin-left: 9pt;\""));
        // Custom tables inherit effective Table Text spacing from the defaults
        // block; regenerating it here would erase configured spacing overrides.
        assert_eq!(output.matches("--pmt-table-text-before:").count(), 1);
        assert_eq!(
            postprocess_html_text_with_styles(&output, &json!({}), true, xml)?,
            output
        );
        let edited = xml.replace("ABCDEF", "FEDCBA");
        let regenerated = postprocess_html_text_with_styles(&output, &json!({}), true, &edited)?;
        assert_eq!(regenerated.matches("id=\"pmt-custom-styles\"").count(), 1);
        assert!(regenerated.contains("color: #FEDCBA;"));
        assert!(!regenerated.contains("color: #ABCDEF;"));
        Ok(())
    }

    /// Preserve commas, quotes, and comment markers without splitting names or injecting HTML.
    #[test]
    fn custom_style_names_are_escaped_in_generated_css() -> Result<()> {
        let xml = r#"<w:styles xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:style w:type="character" w:styleId="Char"><w:name w:val="A, (B) &quot;quote&quot; &lt;/style&gt; */"/><w:rPr><w:color w:val="123456"/></w:rPr></w:style></w:styles>"#;
        let source = r#"<html><head></head><body><span data-custom-style='A, (B) "quote" &lt;/style&gt; */'>Text</span></body></html>"#;
        let output = postprocess_html_text_with_styles(source, &json!({}), true, xml)?;
        assert!(output.contains(r#"[data-custom-style="A, (B) \"quote\" \3c /style> */"]"#));
        assert!(output.contains("color: #123456;"));
        assert_eq!(output.matches("</style>").count(), 1);
        Ok(())
    }

    /// Keep shared institutions, author ordering, escaped text, and custom contact.
    #[test]
    fn author_information_is_escaped_and_replaced_on_reprocessing() -> Result<()> {
        let source = "<!DOCTYPE html>\n<html>\n<body>\n<header>\n<h1 class=\"title\">Paper</h1>\n</header>\n<p>Main text</p>\n</body>\n</html>\n";
        let metadata = json!({"authors": [
            {"name": "Alice & Bob", "affiliations": ["Lab A", "Lab B"], "corresponding": "Ask <Alice>"},
            {"name": "Carol", "affiliation": "Lab A"}
        ]});
        let output = postprocess_html_text(source, &metadata, false)?;
        assert!(output.contains("<span class=\"author-name\">Alice &amp; Bob</span><sup>a,b</sup><sup>*</sup>, <span class=\"author-name\">Carol</span><sup>a</sup>"));
        assert!(output.contains("<p class=\"affiliation\"><sup>a</sup> Lab A</p>"));
        assert!(output.contains("<p class=\"affiliation\"><sup>b</sup> Lab B</p>"));
        assert!(
            output.contains("<p class=\"corresponding-author\"><sup>*</sup>Ask &lt;Alice&gt;</p>")
        );
        assert_eq!(postprocess_html_text(&output, &metadata, false)?, output);
        Ok(())
    }

    /// Preserve unrelated inline CSS and discard the entire invalid margin update.
    #[test]
    fn directional_table_margins_preserve_css_and_invalid_values_do_not_partially_apply()
    -> Result<()> {
        let source = "<html>\n<body>\n<table data-cell_margin=\"1mm\" data-cell-margin-right=\"2pt\" style=\"color: red;\"><tr><td>x</td></tr></table>\n<table data-cell_margin=\"3pt\" data-cell_margin_bottom=\"-1pt\" style=\"color: blue;\"><tr><td>y</td></tr></table>\n</body>\n</html>";
        let output = postprocess_html_text(source, &json!({}), true)?;
        assert!(output.contains("style=\"color: red; --pmt-table-cell-margin-top: 2.8346pt; --pmt-table-cell-margin-right: 2pt; --pmt-table-cell-margin-bottom: 2.8346pt; --pmt-table-cell-margin-left: 2.8346pt;\""));
        assert!(output.contains("data-cell_margin_bottom=\"-1pt\" style=\"color: blue;\""));
        assert_eq!(output.matches("--pmt-table-cell-margin-top").count(), 1);
        Ok(())
    }
}
