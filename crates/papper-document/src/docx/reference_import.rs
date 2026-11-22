//! Transfer matching Word styles onto Papper's reference package.
//!
//! The exported package retains Papper's style names and IDs so that the
//! writer, captions, and native cross-references can still find them.

use super::package::Package;
use super::xml::Element;
use anyhow::{Context, Result};
use std::collections::{BTreeMap, BTreeSet};
use std::io::Read;
use std::path::Path;

/// Export a Papper reference with the source's matching typography and report the matches.
pub fn export_reference_styles(
    source: &Path,
    template: &Path,
    output: &Path,
) -> Result<Vec<(String, String)>> {
    let mut archive = zip::ZipArchive::new(std::fs::File::open(source)?)?;
    let source_styles = read_source_xml(&mut archive, "word/styles.xml")?;
    let document = read_source_xml(&mut archive, "word/document.xml")?;
    let stories = active_stories(&mut archive, &document)?;
    let mut reference = Package::open(template)?;
    let mut styles = reference.xml("word/styles.xml")?;
    for (name, uri) in &source_styles.attrs {
        if name.starts_with("xmlns:") {
            styles
                .attrs
                .entry(name.clone())
                .or_insert_with(|| uri.clone());
        }
    }
    let source = SourceStyles::new(&source_styles, &document, &stories);
    let mut matches = Vec::new();
    for style in styles
        .elements_mut()
        .filter(|element| element.name == "w:style")
    {
        if let Some(matched) = source.find(style) {
            source.copy_formatting(matched, style);
            matches.push((style_name(style).into(), style_name(matched).into()));
        }
    }
    // Standard theme fonts and colors are referenced by name in the style
    // properties. Use the source theme with the same part name as the template.
    let theme = "word/theme/theme1.xml";
    if !matches.is_empty()
        && let Ok(mut part) = archive.by_name(theme)
    {
        let mut bytes = Vec::new();
        part.read_to_end(&mut bytes)?;
        reference.entries.insert(theme.into(), bytes);
    }
    reference.set_xml("word/styles.xml", &styles);
    reference.save(output)?;
    Ok(matches)
}

/// Load only the Word parts needed to match styles, without inflating manuscript media.
fn read_source_xml(archive: &mut zip::ZipArchive<std::fs::File>, name: &str) -> Result<Element> {
    let mut bytes = Vec::new();
    archive.by_name(name)?.read_to_end(&mut bytes)?;
    super::xml::parse(&bytes).with_context(|| format!("Cannot parse source DOCX part {name}"))
}

/// Read only referenced page stories and note parts; ignore stored, inactive variants.
fn active_stories(
    archive: &mut zip::ZipArchive<std::fs::File>,
    document: &Element,
) -> Result<Vec<Element>> {
    let settings = if archive.index_for_name("word/settings.xml").is_some() {
        Some(read_source_xml(archive, "word/settings.xml")?)
    } else {
        None
    };
    let even = word_flag(
        settings
            .as_ref()
            .and_then(|s| s.child("w:evenAndOddHeaders")),
    );
    let mut active = BTreeSet::new();
    let mut inherited = BTreeMap::new();
    let mut note_ids = BTreeMap::<_, BTreeSet<_>>::new();
    visit_live(document, &mut |node| match node.name.as_str() {
        "w:footnoteReference" | "w:endnoteReference" => {
            if let Some(id) = node.attr("w:id") {
                note_ids
                    .entry(node.name.replace("Reference", ""))
                    .or_default()
                    .insert(id.to_owned());
            }
        }
        "w:sectPr" => {
            let first = word_flag(node.child("w:titlePg"));
            for reference in node.elements().filter(|child| {
                matches!(
                    child.name.as_str(),
                    "w:headerReference" | "w:footerReference"
                )
            }) {
                if let Some(id) = reference.attr("r:id") {
                    inherited.insert(
                        (
                            reference.name.clone(),
                            reference.attr("w:type").unwrap_or("default").to_owned(),
                        ),
                        id.to_owned(),
                    );
                }
            }
            for ((_, variant), id) in &inherited {
                if match variant.as_str() {
                    "default" => true,
                    "first" => first,
                    "even" => even,
                    _ => false,
                } {
                    active.insert(id.clone());
                }
            }
        }
        _ => {}
    });
    if active.is_empty() && note_ids.is_empty() {
        return Ok(Vec::new());
    }
    let rels = read_source_xml(archive, "word/_rels/document.xml.rels")?;
    let mut stories = Vec::new();
    for rel in rels
        .elements()
        .filter(|rel| rel.attr("TargetMode") != Some("External"))
    {
        let notes = match rel.attr("Type").and_then(|kind| kind.rsplit('/').next()) {
            Some("footnotes") => note_ids.get("w:footnote"),
            Some("endnotes") => note_ids.get("w:endnote"),
            _ => None,
        };
        let page_story = rel.attr("Id").is_some_and(|id| active.contains(id));
        if !page_story && notes.is_none() {
            continue;
        }
        if let Some(target) = rel.attr("Target") {
            let part = super::image_crops::resolve_part("word/document.xml", target)?;
            let mut story = read_source_xml(archive, &part)?;
            if let Some(ids) = notes {
                story.children.retain(|child| matches!(child,
                    super::xml::Node::Element(note) if note.attr("w:id").is_some_and(|id| ids.contains(id))
                ));
            }
            stories.push(story);
        }
    }
    Ok(stories)
}

/// Interpret optional Word on/off elements, including flags with no value.
fn word_flag(node: Option<&Element>) -> bool {
    node.is_some_and(|node| !matches!(node.attr("w:val"), Some("0" | "false" | "off")))
}

/// Ignore deleted content and history-only property snapshots when collecting usage.
fn is_history(node: &Element) -> bool {
    matches!(node.name.as_str(), "w:del" | "w:moveFrom") || node.name.ends_with("PrChange")
}

/// Visit the current content tree without descending into tracked historical revisions.
fn visit_live(node: &Element, action: &mut impl FnMut(&Element)) {
    if !is_history(node) {
        action(node);
        for child in node.elements() {
            visit_live(child, action);
        }
    }
}

/// Return the Word-facing name, falling back to the ID for unnamed styles.
fn style_name(style: &Element) -> &str {
    property(style, "w:name").unwrap_or_else(|| style.attr("w:styleId").unwrap_or_default())
}

/// Read the value of a style property such as basedOn or outlineLvl.
fn property<'a>(element: &'a Element, name: &str) -> Option<&'a str> {
    element.child(name)?.attr("w:val")
}

/// Index style definitions and how often each is actually assigned in the document.
struct SourceStyles<'a> {
    defaults: Option<&'a Element>,
    by_id: BTreeMap<&'a str, &'a Element>,
    uses: BTreeMap<String, usize>,
    body_uses: BTreeMap<String, usize>,
}

impl<'a> SourceStyles<'a> {
    /// Index styles and count live assignments separately from body text candidates.
    fn new(styles: &'a Element, document: &Element, stories: &[Element]) -> Self {
        let by_id: BTreeMap<_, _> = styles
            .elements()
            .filter(|element| element.name == "w:style")
            .filter_map(|style| Some((style.attr("w:styleId")?, style)))
            .collect();
        let default_paragraph = by_id.iter().find_map(|(&id, style)| {
            (style.attr("w:type") == Some("paragraph")
                && matches!(style.attr("w:default"), Some("1" | "true" | "on")))
            .then_some(id)
        });
        let mut source = Self {
            defaults: styles.child("w:docDefaults"),
            by_id,
            uses: BTreeMap::new(),
            body_uses: BTreeMap::new(),
        };
        source.count(document, true, default_paragraph);
        for story in stories {
            source.count(story, false, default_paragraph);
        }
        source
    }

    /// Count applied styles, excluding tracked snapshots and empty default paragraphs.
    fn count(&mut self, node: &Element, main_text: bool, default_paragraph: Option<&str>) {
        if is_history(node) {
            return;
        }
        let style = match node.name.as_str() {
            "w:p" => node
                .child("w:pPr")
                .and_then(|p| property(p, "w:pStyle"))
                .or_else(|| {
                    // Word omits pStyle for ordinary paragraphs using the default.
                    // Do not count spacer paragraphs or section-break containers as prose.
                    default_paragraph.filter(|_| !node.text().trim().is_empty())
                }),
            "w:r" => node.child("w:rPr").and_then(|r| property(r, "w:rStyle")),
            "w:tbl" => node
                .child("w:tblPr")
                .and_then(|t| property(t, "w:tblStyle")),
            _ => None,
        };
        if let Some(id) = style {
            *self.uses.entry(id.to_owned()).or_default() += 1;
            if main_text && node.name == "w:p" && !node.text().trim().is_empty() {
                *self.body_uses.entry(id.to_owned()).or_default() += 1;
            }
        }
        for child in node.elements() {
            self.count(child, main_text && node.name != "w:tbl", default_paragraph);
        }
    }

    /// Find an exact or known equivalent style of the same Word type.
    fn find(&self, target: &Element) -> Option<&'a Element> {
        let name = style_name(target).to_lowercase();
        let aliases: &[&str] = match name.as_str() {
            "normal" => &["常规", "普通", "标准"],
            "body text" => &[
                "正文文本",
                "正文内容",
                "正文",
                "正文样式",
                "body text first indent",
            ],
            "title" => &["标题"],
            "subtitle" => &["副标题"],
            "header" => &["页眉"],
            "footer" => &["页脚"],
            "caption" => &["题注"],
            "image caption" => &["图题", "图注", "figure caption", "题注", "caption"],
            "table caption" => &["表题", "表注", "题注", "caption"],
            "table text" => &["表格文字", "表格字体", "表格正文"],
            "footnote text" => &["脚注文本"],
            "endnote text" => &["尾注文本"],
            "bibliography" => &["参考文献", "endnote bibliography"],
            "para equation" => &["公式", "equation"],
            _ => &[],
        };
        let heading = name
            .strip_prefix("heading ")
            .and_then(|value| value.parse::<u8>().ok());
        let localized_heading = heading.map(|level| format!("标题 {level}"));
        let candidates = || {
            self.by_id.values().copied().filter(|style| {
                style.attr("w:type") == target.attr("w:type") && self.usages(style) > 0
            })
        };
        let known = |style: &Element| {
            let candidate = style_name(style).to_lowercase();
            candidate == name
                || aliases.contains(&candidate.as_str())
                || localized_heading.as_deref() == Some(candidate.as_str())
        };
        let matched = candidates()
            .filter(|style| known(style))
            .filter(|style| name != "body text" || self.body_usages(style) > 0)
            .max_by_key(|style| {
                let exact = style_name(style).eq_ignore_ascii_case(&name);
                // Choose the main prose style over a built-in used only in a few paragraphs.
                if name == "body text" {
                    (true, self.body_usages(style), exact)
                } else {
                    (exact, self.usages(style), false)
                }
            });
        if let Some(level) = heading.filter(|_| matched.is_none()) {
            // Direct outline levels on custom styles can identify a heading
            // when the built-in style is dormant. Skip ambiguous alternatives.
            let wanted = (level - 1).to_string();
            let mut used_outline = candidates().filter(|style| {
                self.body_usages(style) > 0
                    && style
                        .child("w:pPr")
                        .and_then(|props| property(props, "w:outlineLvl"))
                        == Some(wanted.as_str())
            });
            if let Some(style) = used_outline.next()
                && used_outline.next().is_none()
            {
                return Some(style);
            }
        }
        matched.or_else(|| {
            matches!(name.as_str(), "normal" | "body text")
                .then(|| {
                    candidates().find(|style| {
                        matches!(style.attr("w:default"), Some("1" | "true" | "on"))
                            && (name != "body text" || self.body_usages(style) > 0)
                    })
                })
                .flatten()
        })
    }

    /// Count source assignments across the retained document stories.
    fn usages(&self, style: &Element) -> usize {
        self.uses
            .get(style.attr("w:styleId").unwrap_or_default())
            .copied()
            .unwrap_or_default()
    }

    /// Count prose assignments separately from table cells and running headers.
    fn body_usages(&self, style: &Element) -> usize {
        self.body_uses
            .get(style.attr("w:styleId").unwrap_or_default())
            .copied()
            .unwrap_or_default()
    }

    /// Flatten inherited formatting while keeping the target's canonical Word identity.
    fn copy_formatting(&self, source: &Element, target: &mut Element) {
        let mut ancestors = vec![source];
        let mut seen: BTreeSet<&str> = source.attr("w:styleId").into_iter().collect();
        let mut next = property(source, "w:basedOn");
        while let Some(id) = next.filter(|id| seen.insert(id)) {
            let Some(style) = self.by_id.get(id) else {
                break;
            };
            ancestors.push(style);
            next = property(style, "w:basedOn");
        }
        target.remove("w:basedOn");
        for name in [
            "w:pPr",
            "w:rPr",
            "w:tblPr",
            "w:trPr",
            "w:tcPr",
            "w:tblStylePr",
        ] {
            // A table's conditional formatting uses multiple elements with the same name.
            if name == "w:tblStylePr" {
                target.remove(name);
                let mut conditions = BTreeMap::new();
                for style in ancestors.iter().rev() {
                    for condition in style.elements().filter(|child| child.name == name) {
                        conditions.insert(condition.attr("w:type"), condition.clone());
                    }
                }
                for mut condition in conditions.into_values() {
                    remove_source_structure(&mut condition);
                    target.push(condition);
                }
                continue;
            }
            let mut formatting = Element::new(name);
            if target.attr("w:type") == Some("paragraph")
                && let Some(defaults) = self.defaults
                && let Some(properties) = defaults
                    .child(&format!("{name}Default"))
                    .and_then(|d| d.child(name))
            {
                overlay(&mut formatting, properties);
            }
            for style in ancestors.iter().rev() {
                if let Some(properties) = style.child(name) {
                    overlay(&mut formatting, properties);
                }
            }
            if name == "w:pPr" {
                if target.attr("w:type") == Some("paragraph") {
                    preserve_missing_paragraph_spacing(&mut formatting);
                }
                // Keep the heading role for a source style matched by name.
                if formatting.child("w:outlineLvl").is_none() {
                    if let Some(level) =
                        target.child(name).and_then(|old| old.child("w:outlineLvl"))
                    {
                        formatting.push(level.clone());
                    }
                }
            }
            remove_source_structure(&mut formatting);
            target.remove(name);
            if formatting.elements().next().is_some() {
                target.push(formatting);
            }
        }
    }
}

/// Keep implicit zero spacing from inheriting the reference's nonzero document defaults.
fn preserve_missing_paragraph_spacing(properties: &mut Element) {
    let spacing = properties.word("w:spacing");
    for (points, lines, automatic) in [
        ("w:before", "w:beforeLines", "w:beforeAutospacing"),
        ("w:after", "w:afterLines", "w:afterAutospacing"),
    ] {
        if spacing.attr(points).is_none()
            && spacing.attr(lines).is_none()
            && !matches!(spacing.attr(automatic), Some("1" | "true" | "on"))
        {
            spacing.set(points, "0");
        }
    }
}

/// Merge the inheritable formatting groups while keeping the Word property order.
fn overlay(target: &mut Element, source: &Element) {
    for property in source.elements() {
        let child = target.word(&property.name);
        match property.name.as_str() {
            "w:spacing" | "w:ind" | "w:lang" => child.attrs.extend(property.attrs.clone()),
            "w:rFonts" => {
                for (fixed, theme) in [
                    ("w:ascii", "w:asciiTheme"),
                    ("w:hAnsi", "w:hAnsiTheme"),
                    ("w:eastAsia", "w:eastAsiaTheme"),
                    ("w:cs", "w:cstheme"),
                ] {
                    if property.attrs.contains_key(fixed) {
                        child.attrs.remove(theme);
                    }
                    if property.attrs.contains_key(theme) {
                        child.attrs.remove(fixed);
                    }
                }
                child.attrs.extend(property.attrs.clone());
            }
            "w:pBdr" | "w:tblBorders" | "w:tcBorders" | "w:tblCellMar" | "w:tcMar" => {
                overlay(child, property);
            }
            _ => *child = property.clone(),
        }
    }
}

/// Exclude numbering IDs, section data and page breaks that Convert handles separately.
fn remove_source_structure(formatting: &mut Element) {
    formatting.visit_mut(&mut |node| {
        for name in [
            "w:numPr",
            "w:sectPr",
            "w:pStyle",
            "w:rStyle",
            "w:tblStyle",
            "w:pageBreakBefore",
        ] {
            node.remove(name);
        }
    });
}
