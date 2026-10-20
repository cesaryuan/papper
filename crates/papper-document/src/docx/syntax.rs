//! Warn about residual authoring syntax and attach review comments without rejecting a build.

use super::package::Package;
use super::xml::{Element, Node};
use anyhow::{Context, Result};
use regex::Regex;
use std::collections::BTreeSet;

const WORD_NS: &str = "http://schemas.openxmlformats.org/wordprocessingml/2006/main";
const COMMENTS_REL: &str =
    "http://schemas.openxmlformats.org/officeDocument/2006/relationships/comments";

/// Retain the source paragraph and every warning to explain the annotation in Word.
struct Finding {
    part: String,
    paragraph: usize,
    text: String,
    issues: Vec<&'static str>,
}

impl Finding {
    /// Name the Word editing area in comments without exposing package-internal XML paths.
    fn source(&self) -> &'static str {
        match self.part.as_str() {
            "word/document.xml" => "Body",
            "word/footnotes.xml" => "Footnotes",
            "word/endnotes.xml" => "Endnotes",
            name if name.starts_with("word/header") => "Header",
            _ => "Footer",
        }
    }

    /// Explain the suspected problem without claiming that a heuristic proves invalid content.
    fn message(&self) -> String {
        format!(
            "Papper syntax warning: suspected unrendered syntax or duplicated reference text. Detected: {}. Source: {}, paragraph {}. Text: {}. Check the Markdown source and rendered references; this may be intentional literal text.",
            self.issues.join("; "),
            self.source(),
            self.paragraph + 1,
            self.text
        )
    }
}

/// Report every finding and add paragraph comments while preserving the original document text.
pub(super) fn annotate(package: &mut Package, document: &mut Element) -> Result<()> {
    let rules = SyntaxRules::new()?;
    let mut findings = Vec::new();
    collect(document, "word/document.xml", &rules, &mut findings);
    let parts = Regex::new(r"^word/(header\d+|footer\d+|footnotes|endnotes)\.xml$")?;
    for name in package.entries.keys().filter(|name| parts.is_match(name)) {
        collect(&package.xml(name)?, name, &rules, &mut findings);
    }
    if findings.is_empty() {
        return Ok(());
    }
    for finding in &findings {
        eprintln!(
            "[WARNING] DOCX syntax check ({}, paragraph {}): {}: {}",
            finding.part,
            finding.paragraph + 1,
            finding.issues.join("; "),
            finding.text
        );
    }

    let mut comments = ReviewComments::load(package)?;
    let mut paragraph_index = 0;
    document.visit_mut(&mut |paragraph| {
        if paragraph.name != "w:p" {
            return;
        }
        for finding in findings.iter().filter(|finding| {
            finding.part == "word/document.xml" && finding.paragraph == paragraph_index
        }) {
            comments.attach(paragraph, &finding.message());
        }
        paragraph_index += 1;
    });

    // Word cannot reliably show comments in headers, footers or notes. Anchor these
    // warnings in the body and include the original part and text in each comment.
    let other_stories: Vec<_> = findings
        .iter()
        .filter(|finding| finding.part != "word/document.xml")
        .collect();
    if !other_stories.is_empty() {
        if paragraph_index == 0 {
            let body = document
                .child_mut("w:body")
                .context("DOCX has no document body")?;
            let index = body
                .children
                .iter()
                .position(
                    |node| matches!(node, Node::Element(element) if element.name == "w:sectPr"),
                )
                .unwrap_or(body.children.len());
            body.children
                .insert(index, Node::Element(Element::new("w:p")));
        }
        let mut attached = false;
        document.visit_mut(&mut |paragraph| {
            if paragraph.name == "w:p" && !attached {
                for finding in &other_stories {
                    comments.attach(paragraph, &finding.message());
                }
                attached = true;
            }
        });
    }
    package.set_xml(&comments.part, &comments.root);
    eprintln!(
        "[WARNING] DOCX syntax check found {} affected paragraph(s); review the Papper comments in Word",
        findings.len()
    );
    Ok(())
}

/// Inspect each paragraph independently, including paragraphs nested in tables or text boxes.
fn collect(root: &Element, part: &str, rules: &SyntaxRules, findings: &mut Vec<Finding>) {
    let mut paragraph = 0;
    root.visit(&mut |element| {
        if element.name != "w:p" {
            return;
        }
        let text = validation_text(element)
            .split_whitespace()
            .collect::<Vec<_>>()
            .join(" ");
        let issues = rules.issues(&text);
        if !issues.is_empty() {
            findings.push(Finding {
                part: part.into(),
                paragraph,
                text,
                issues,
            });
        }
        paragraph += 1;
    });
}

/// Append traditional Word comments without replacing authored comments or their relationships.
struct ReviewComments {
    part: String,
    root: Element,
    used_ids: BTreeSet<String>,
    next_id: u64,
}

impl ReviewComments {
    /// Discover the existing comments part, or register a new one with an unused relationship ID.
    fn load(package: &mut Package) -> Result<Self> {
        let rels_path = "word/_rels/document.xml.rels";
        let mut rels = if package.entries.contains_key(rels_path) {
            package.xml(rels_path)?
        } else {
            Element::with_attrs(
                "Relationships",
                &[(
                    "xmlns",
                    "http://schemas.openxmlformats.org/package/2006/relationships",
                )],
            )
        };
        let existing = rels
            .elements()
            .find(|relation| relation.attr("Type") == Some(COMMENTS_REL));
        let part = if let Some(relation) = existing {
            let target = relation
                .attr("Target")
                .context("Comments relationship has no target")?;
            let mut segments = if target.starts_with('/') {
                Vec::new()
            } else {
                vec!["word"]
            };
            for segment in target.split('/') {
                match segment {
                    "" | "." => {}
                    ".." => {
                        segments.pop();
                    }
                    _ => segments.push(segment),
                }
            }
            segments.join("/")
        } else {
            let mut suffix = 0;
            let part = loop {
                let candidate = if suffix == 0 {
                    "word/comments.xml".into()
                } else {
                    format!("word/comments{suffix}.xml")
                };
                if !package.entries.contains_key(&candidate) {
                    break candidate;
                }
                suffix += 1;
            };
            let mut id = 1;
            while rels.elements().any(|relation| {
                relation.attr("Id") == Some(format!("rIdPapperComments{id}").as_str())
            }) {
                id += 1;
            }
            rels.push(Element::with_attrs(
                "Relationship",
                &[
                    ("Id", &format!("rIdPapperComments{id}")),
                    ("Type", COMMENTS_REL),
                    ("Target", &part[5..]),
                ],
            ));
            package.set_xml(rels_path, &rels);
            part
        };
        let mut root = if package.entries.contains_key(&part) {
            package.xml(&part)?
        } else {
            Element::with_attrs("w:comments", &[("xmlns:w", WORD_NS)])
        };
        root.set("xmlns:w", WORD_NS);
        let mut used_ids = BTreeSet::new();
        root.visit(&mut |element| {
            if let Some(id) = element.attr("w:id") {
                used_ids.insert(id.to_owned());
            }
        });
        // Reserve anchors as well as comment bodies so dangling authored IDs are not reused.
        for name in package.entries.keys().filter(|name| {
            name.as_str() == "word/document.xml"
                || name.as_str() == "word/footnotes.xml"
                || name.as_str() == "word/endnotes.xml"
                || (name.starts_with("word/header") || name.starts_with("word/footer"))
                    && name.ends_with(".xml")
        }) {
            package.xml(name)?.visit(&mut |element| {
                if matches!(
                    element.name.as_str(),
                    "w:commentReference" | "w:commentRangeStart" | "w:commentRangeEnd"
                ) && let Some(id) = element.attr("w:id")
                {
                    used_ids.insert(id.to_owned());
                }
            });
        }
        let mut types = package.xml("[Content_Types].xml")?;
        let absolute = format!("/{part}");
        if !types
            .elements()
            .any(|entry| entry.attr("PartName") == Some(absolute.as_str()))
        {
            types.push(Element::with_attrs("Override", &[
                ("PartName", &absolute),
                ("ContentType", "application/vnd.openxmlformats-officedocument.wordprocessingml.comments+xml"),
            ]));
            package.set_xml("[Content_Types].xml", &types);
        }
        Ok(Self {
            part,
            root,
            used_ids,
            next_id: 0,
        })
    }

    /// Anchor one warning to a paragraph without rewriting runs, fields, hyperlinks or formatting.
    fn attach(&mut self, paragraph: &mut Element, message: &str) {
        let mut already_present = false;
        paragraph.visit(&mut |element| {
            if element.name == "w:commentReference" {
                already_present |= self.root.elements().any(|comment| {
                    comment.attr("w:id") == element.attr("w:id")
                        && comment.attr("w:author") == Some("Papper")
                        && comment.text() == message
                });
            }
        });
        // Standalone postprocessing may be repeated; do not duplicate an unchanged warning.
        if already_present {
            return;
        }
        while self.used_ids.contains(&self.next_id.to_string()) {
            self.next_id += 1;
        }
        let id = self.next_id.to_string();
        self.used_ids.insert(id.clone());
        self.next_id += 1;
        let mut comment = Element::with_attrs(
            "w:comment",
            &[("w:id", &id), ("w:author", "Papper"), ("w:initials", "P")],
        );
        let mut text = Element::with_attrs("w:t", &[("xml:space", "preserve")]);
        text.children.push(Node::Text(message.into()));
        let mut run = Element::new("w:r");
        run.push(text);
        let mut content = Element::new("w:p");
        content.push(run);
        comment.push(content);
        self.root.push(comment);
        let start = paragraph
            .children
            .iter()
            .position(|node| matches!(node, Node::Element(element) if element.name != "w:pPr"))
            .unwrap_or(paragraph.children.len());
        paragraph.children.insert(
            start,
            Node::Element(Element::with_attrs("w:commentRangeStart", &[("w:id", &id)])),
        );
        paragraph.push(Element::with_attrs("w:commentRangeEnd", &[("w:id", &id)]));
        let mut reference = Element::new("w:r");
        reference.push(Element::with_attrs("w:commentReference", &[("w:id", &id)]));
        paragraph.push(reference);
    }
}

/// Compile the existing final-output heuristics once per document.
struct SyntaxRules {
    fenced: Regex,
    attribute: Regex,
    crossref: Regex,
    citation: Regex,
    citation_brackets: Regex,
    raw_html: Regex,
    duplicate: Regex,
    parenthesized: Regex,
    numbered: Regex,
}

impl SyntaxRules {
    /// Prepare every supported syntax and duplicated-reference check.
    fn new() -> Result<Self> {
        let fenced = regex::Regex::new(r"^\s*:{3,}(?:\s*\{[^{}]*\}|\s*)$")?;
        let attribute = regex::Regex::new(r"\{#[A-Za-z][\w.-]*(?::[\w.-]+)?(?:\s+[^{}]+)?\}")?;
        let crossref = regex::Regex::new(
            r"[@#](?:sec|fig|tbl|eq|app|alg|lem|thm|cor|def|prop|exm|exr):[A-Za-z0-9._:-]+",
        )?;
        let citation = regex::Regex::new(r"-?@[A-Za-z0-9_][A-Za-z0-9_:+-]*")?;
        let citation_brackets = regex::Regex::new(r"\[\s*@[\w:-]+(?:\s*;\s*@[\w:-]+)*\s*\]")?;
        // Match only HTML's standard element names. Scientific notation such as
        // `<k>` or `<degree>` is ordinary visible text, while known elements such
        // as `<div>` and `<span>` still indicate raw HTML that Pandoc failed to render.
        let raw_html = regex::Regex::new(concat!(
            r"(?i)<!--.*?-->|</?(?:",
            r"a|abbr|address|area|article|aside|audio|b|base|bdi|bdo|blockquote|body|br|button|",
            r"canvas|caption|cite|code|col|colgroup|data|datalist|dd|del|details|dfn|dialog|div|dl|dt|",
            r"em|embed|fieldset|figcaption|figure|footer|form|h[1-6]|head|header|hgroup|hr|html|",
            r"i|iframe|img|input|ins|kbd|label|legend|li|link|main|map|mark|menu|meta|meter|",
            r"nav|noscript|object|ol|optgroup|option|output|p|picture|pre|progress|q|rp|rt|ruby|",
            r"s|samp|script|search|section|select|slot|small|source|span|strong|style|sub|summary|sup|",
            r"table|tbody|td|template|textarea|tfoot|th|thead|time|title|tr|track|u|ul|var|video|wbr",
            r")(?:\s+[^<>]*?)?\s*/?>",
        ))?;
        let duplicate = regex::Regex::new(
            r"\b(Section|Figure|Table|Equation)\s+(Section|Figure|Table|Equation)\b",
        )?;
        let parenthesized = regex::Regex::new(
            r"\b(Section|Figure|Table|Equation)\s*\(\s*(Section|Figure|Table|Equation)\s+\d+(?:\.\d+)*\s*\)",
        )?;
        let numbered = regex::Regex::new(
            r"\b(Section|Figure|Table|Equation)\s+(\d+(?:\.\d+)*)\s+(Section|Figure|Table|Equation)\s+(\d+(?:\.\d+)*)\b",
        )?;
        Ok(Self {
            fenced,
            attribute,
            crossref,
            citation,
            citation_brackets,
            raw_html,
            duplicate,
            parenthesized,
            numbered,
        })
    }

    /// Return all problem categories in a paragraph, rather than stopping at the first one.
    fn issues(&self, text: &str) -> Vec<&'static str> {
        let Self {
            fenced,
            attribute,
            crossref,
            citation,
            citation_brackets,
            raw_html,
            duplicate,
            parenthesized,
            numbered,
        } = self;
        let mut issues = Vec::new();
        if fenced.is_match(text) {
            issues.push("Pandoc fenced_divs marker");
        }
        if attribute.is_match(text) {
            issues.push("Pandoc attribute block");
        }
        if crossref.find_iter(text).any(|found| {
            text[..found.start()]
                .chars()
                .next_back()
                .is_none_or(|previous| {
                    !previous.is_alphanumeric() && !matches!(previous, '_' | '/')
                })
        }) {
            issues.push("Pandoc cross-reference label");
        }
        if citation_brackets.is_match(text)
            || citation.find_iter(text).any(|found| {
                // A cross-reference is also shaped like a citation; avoid reporting it twice.
                !crossref.is_match(found.as_str())
                    && text[..found.start()]
                        .chars()
                        .next_back()
                        .is_none_or(|previous| {
                            !previous.is_alphanumeric()
                                && !matches!(previous, '_' | '.' | '/' | '-')
                        })
            })
        {
            issues.push("Pandoc citation syntax");
        }
        if raw_html.is_match(text) {
            issues.push("raw HTML tag");
        }
        if duplicate.captures_iter(text).any(|c| c[1] == c[2]) {
            issues.push("duplicated reference label word");
        }
        if parenthesized.captures_iter(text).any(|c| c[1] == c[2]) {
            issues.push("duplicated parenthesized reference label");
        }
        if numbered
            .captures_iter(text)
            .any(|c| c[1] == c[3] && c[2] == c[4])
        {
            issues.push("duplicated numbered reference label");
        }
        issues
    }
}

/// Exclude temporary hidden TeX markers that are removed after MathType conversion.
fn validation_text(paragraph: &Element) -> String {
    if paragraph.name == "w:r"
        && paragraph.text().starts_with("MTLATEX:")
        && paragraph
            .child("w:rPr")
            .is_some_and(|properties| properties.contains("w:vanish"))
    {
        // A formula's source TeX can contain reference-like text. Checking the
        // intermediate marker would reject valid math before it becomes OLE.
        return String::new();
    }
    if paragraph.name == "w:t" {
        return paragraph.direct_text();
    }
    paragraph
        .elements()
        .filter(|child| child.name != "w:p")
        .map(validation_text)
        .collect()
}
