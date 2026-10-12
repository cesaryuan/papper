//! Move Pandoc-rendered Markdown into header/footer parts and finalize dynamic page fields.

use super::formatting::style_index;
use super::package::Package;
use super::xml::{Element, Node};
use anyhow::{Context, Result, bail};
use papper_core::metadata::{DocxFooter, FooterAlignment, PmtSettings};
use serde_json::Value;
use std::collections::{BTreeMap, BTreeSet};

/// Replace configured headers/footers uniformly, retaining legacy footer page visibility.
pub(crate) fn apply(
    package: &mut Package,
    document: &mut Element,
    styles: &Element,
    settings: &PmtSettings,
) -> Result<()> {
    if let Some(config) = &settings.fields().docx_header {
        apply_content(package, document, styles, config, "header")?;
    }
    if let Some(config) = &settings.fields().docx_footer {
        apply_content(package, document, styles, config, "footer")
    } else if settings.was_provided("docxFooter") {
        Ok(())
    } else {
        apply_page_numbers(package, document, styles, settings)
    }
}

/// Share rendered content, resource relocation, and style handling between the two page stories.
fn apply_content(
    package: &mut Package,
    document: &mut Element,
    styles: &Element,
    config: &DocxFooter,
    kind: &str,
) -> Result<()> {
    let default_content = if kind == "footer" {
        "[]{t=\"pageNum\"}"
    } else {
        ""
    };
    let content = config.content.as_deref().unwrap_or(default_content);
    let alignment = config.alignment.unwrap_or(FooterAlignment::Center).as_str();
    let rendered = take_rendered_blocks(document, kind)?;
    if rendered.is_none() && !content.trim().is_empty() {
        bail!(
            "DOCX {kind} content was not rendered; run the docx_footer Pandoc filter before postprocessing"
        )
    }
    let mut blocks = rendered.unwrap_or_default();
    // Localized references may use an ID such as af2 rather than the display name.
    let style_name = if kind == "footer" { "Footer" } else { "Header" };
    let footer_style = style_index(styles, style_name)
        .and_then(|index| match &styles.children[index] {
            Node::Element(style) => style.attr("w:styleId"),
            _ => None,
        })
        .unwrap_or(style_name);
    for block in &mut blocks {
        finalize_page_fields(block, styles)?;
        format_paragraphs(block, alignment, footer_style, false);
    }
    if blocks.is_empty()
        || !matches!(blocks.last(), Some(Node::Element(element)) if element.name == "w:p")
    {
        // Word requires a final paragraph, including after a Markdown table or an empty header/footer.
        let mut paragraph = Element::new("w:p");
        paragraph
            .word("w:pPr")
            .word("w:pStyle")
            .set("w:val", footer_style);
        paragraph.word("w:pPr").word("w:jc").set("w:val", alignment);
        blocks.push(Node::Element(paragraph));
    }
    let mut names: Vec<String> = package
        .entries
        .keys()
        .filter(|name| name.starts_with(&format!("word/{kind}")) && name.ends_with(".xml"))
        .cloned()
        .collect();
    let mut first_section = true;
    let mut has_primary = false;
    document.visit(&mut |element| {
        if element.name == "w:sectPr" && first_section {
            has_primary = element.elements().any(|child| {
                child.name == format!("w:{kind}Reference")
                    && child.attr("w:type") == Some("default")
            });
            first_section = false;
        }
    });
    // First/even-only templates still need a primary header/footer on ordinary pages.
    // Later sections inherit this reference unless they already define their own.
    if !has_primary && !content.is_empty() {
        names.push(create_primary_part(package, document, kind)?);
    }
    for name in names {
        let mut footer = package.xml(&name)?;
        // Relocated nodes inherit document namespaces, which may be absent from a custom header/footer root.
        for (key, value) in &document.attrs {
            if key.starts_with("xmlns:") || key == "mc:Ignorable" {
                footer.set(key, value);
            }
        }
        footer.children = blocks.clone();
        relocate_relationships(package, &name, &mut footer)?;
        relocate_identifiers(package, document, &mut footer)?;
        package.set_xml(&name, &footer);
    }
    Ok(())
}

/// Extract exact boundary paragraphs and their rendered blocks before body-specific formatting runs.
pub(super) fn take_rendered_blocks(
    document: &mut Element,
    kind: &str,
) -> Result<Option<Vec<Node>>> {
    let body = document
        .child_mut("w:body")
        .context("DOCX is missing its document body")?;
    let begin = format!("PMT_DOCX_{}_BEGIN", kind.to_uppercase());
    let end = format!("PMT_DOCX_{}_END", kind.to_uppercase());
    let boundaries: Vec<_> = body
        .children
        .iter()
        .enumerate()
        .filter_map(|(index, node)| {
            let Node::Element(element) = node else {
                return None;
            };
            if element.name != "w:p" {
                return None;
            }
            match element.text().as_str() {
                value if value == begin => Some((index, true)),
                value if value == end => Some((index, false)),
                _ => None,
            }
        })
        .collect();
    if boundaries.is_empty() {
        return Ok(None);
    }
    let [(start, true), (end, false)] = boundaries.as_slice() else {
        bail!("DOCX contains incomplete or duplicate {kind} boundaries")
    };
    let mut nodes: Vec<_> = body.children.drain(*start..=*end).collect();
    nodes.pop();
    nodes.remove(0);
    Ok(Some(nodes))
}

/// Set outer paragraph alignment while keeping table cells, list numbering, and custom styles intact.
fn format_paragraphs(node: &mut Node, alignment: &str, footer_style: &str, in_table: bool) {
    let Node::Element(element) = node else {
        return;
    };
    let in_table = in_table || element.name == "w:tbl";
    if element.name == "w:p" {
        let properties = element.word("w:pPr");
        let inherited = properties
            .child("w:pStyle")
            .and_then(|style| style.attr("w:val"));
        if inherited.is_none_or(|style| {
            matches!(style, "Normal" | "BodyText" | "FirstParagraph" | "Compact")
        }) {
            properties.word("w:pStyle").set("w:val", footer_style);
        }
        if !in_table {
            properties.word("w:jc").set("w:val", alignment);
        }
    }
    for child in &mut element.children {
        format_paragraphs(child, alignment, footer_style, in_table);
    }
}

/// Replace Pandoc's styled page runs with dynamic fields without parsing Markdown or losing emphasis.
pub(super) fn finalize_page_fields(node: &mut Node, styles: &Element) -> Result<()> {
    let Node::Element(element) = node else {
        return Ok(());
    };
    let mut children = Vec::new();
    for mut child in std::mem::take(&mut element.children) {
        let record = if let Node::Element(run) = &child {
            (run.name == "w:r").then(|| run.text()).and_then(|text| {
                text.strip_prefix("PMT_FOOTER_PAGE:")
                    .map(|record| (true, record.to_owned()))
                    .or_else(|| {
                        text.strip_prefix("PMT_DOCX_FIELD:")
                            .map(|record| (false, record.to_owned()))
                    })
            })
        } else {
            None
        };
        if let Some((page, record)) = record {
            let record: Value =
                serde_json::from_str(&record).context("Invalid rendered footer page marker")?;
            let result = record["result"]
                .as_str()
                .context("Missing footer page result")?;
            let instruction = if page {
                let switch = record["switch"].as_str().context("Missing page format")?;
                if switch.is_empty() {
                    " PAGE ".into()
                } else {
                    format!(" PAGE \\* {switch} ")
                }
            } else {
                record["instruction"]
                    .as_str()
                    .context("Missing Word field instruction")?
                    .to_owned()
            };
            let Node::Element(run) = child else {
                unreachable!()
            };
            let properties = run.child("w:rPr").cloned();
            let mut paragraph = Element::new("w:p");
            append_formatted_field(
                &mut paragraph,
                styles,
                &instruction,
                result,
                properties.as_ref(),
                page,
            )?;
            children.extend(paragraph.children);
        } else {
            finalize_page_fields(&mut child, styles)?;
            children.push(child);
        }
    }
    element.children = children;
    Ok(())
}

/// Recreate used body relationships in each header/footer part while sharing the same media bytes.
fn relocate_relationships(package: &mut Package, name: &str, footer: &mut Element) -> Result<()> {
    let source = package.xml("word/_rels/document.xml.rels")?;
    let mut used = BTreeSet::new();
    footer.visit(&mut |element| {
        for key in ["r:id", "r:embed", "r:link"] {
            if let Some(value) = element.attr(key) {
                used.insert(value.to_owned());
            }
        }
    });
    let path = format!(
        "word/_rels/{}.rels",
        name.strip_prefix("word/").unwrap_or(name)
    );
    if used.is_empty() {
        package.entries.remove(&path);
        return Ok(());
    }
    let mut relationships = Element::with_attrs(
        "Relationships",
        &[(
            "xmlns",
            "http://schemas.openxmlformats.org/package/2006/relationships",
        )],
    );
    let mut identifiers = BTreeMap::new();
    for (index, identifier) in used.iter().enumerate() {
        let mut relation = source
            .elements()
            .find(|relation| relation.attr("Id") == Some(identifier.as_str()))
            .with_context(|| format!("Missing footer resource relationship: {identifier}"))?
            .clone();
        let target = format!("rId{}", index + 1);
        relation.set("Id", &target);
        relationships.push(relation);
        identifiers.insert(identifier.clone(), target);
    }
    footer.visit_mut(&mut |element| {
        for key in ["r:id", "r:embed", "r:link"] {
            if let Some(value) = element.attr(key).and_then(|value| identifiers.get(value)) {
                element.set(key, value);
            }
        }
    });
    package.set_xml(&path, &relationships);
    Ok(())
}

/// Give copied pictures/bookmarks unique identities and retain links to footer-local anchors.
fn relocate_identifiers(package: &Package, document: &Element, footer: &mut Element) -> Result<()> {
    let mut maximum = 0_u64;
    let mut bookmark_maximum = 0_u64;
    let mut bookmark_names = BTreeSet::new();
    let mut inspect = |element: &Element| {
        if element.name == "wp:docPr" {
            maximum = maximum.max(
                element
                    .attr("id")
                    .and_then(|id| id.parse().ok())
                    .unwrap_or(0),
            );
        }
        if element.name == "w:bookmarkStart" {
            bookmark_maximum = bookmark_maximum.max(
                element
                    .attr("w:id")
                    .and_then(|id| id.parse().ok())
                    .unwrap_or(0),
            );
            if let Some(name) = element.attr("w:name") {
                bookmark_names.insert(name.to_owned());
            }
        }
    };
    document.visit(&mut inspect);
    for name in package.entries.keys().filter(|name| {
        (name.starts_with("word/header") || name.starts_with("word/footer"))
            && name.ends_with(".xml")
    }) {
        package.xml(name)?.visit(&mut inspect);
    }
    let mut names = BTreeMap::new();
    let mut identifiers = BTreeMap::new();
    footer.visit_mut(&mut |element| {
        if element.name == "wp:docPr" {
            maximum += 1;
            element.set("id", maximum);
        }
        if element.name == "w:bookmarkStart" {
            bookmark_maximum += 1;
            if let Some(id) = element.attr("w:id") {
                identifiers.insert(id.to_owned(), bookmark_maximum.to_string());
            }
            if let Some(old) = element.attr("w:name") {
                let mut name = format!("PapperFooter{bookmark_maximum}");
                while bookmark_names.contains(&name) {
                    name.push('_');
                }
                names.insert(old.to_owned(), name.clone());
                bookmark_names.insert(name.clone());
                element.set("w:name", name);
            }
        }
    });
    footer.visit_mut(&mut |element| {
        if matches!(element.name.as_str(), "w:bookmarkStart" | "w:bookmarkEnd")
            && let Some(value) = element.attr("w:id").and_then(|id| identifiers.get(id))
        {
            element.set("w:id", value);
        }
        if element.name == "w:hyperlink"
            && let Some(value) = element.attr("w:anchor").and_then(|name| names.get(name))
        {
            element.set("w:anchor", value);
        }
    });
    Ok(())
}

/// Insert a dynamic PAGE field using the reference's page-number character style.
fn append_page_field(paragraph: &mut Element, styles: &Element) -> Result<()> {
    append_formatted_page_field(paragraph, styles, " PAGE ", "1", None)
}

/// Write the complex Word PAGE field with the selected format and preserved Pandoc run properties.
fn append_formatted_page_field(
    paragraph: &mut Element,
    styles: &Element,
    instruction: &str,
    result: &str,
    properties: Option<&Element>,
) -> Result<()> {
    append_formatted_field(paragraph, styles, instruction, result, properties, true)
}

/// Write a PAGE or DATE field while retaining the authored inline typography.
fn append_formatted_field(
    paragraph: &mut Element,
    styles: &Element,
    instruction: &str,
    result: &str,
    properties: Option<&Element>,
    page: bool,
) -> Result<()> {
    let identifier = if page {
        let index = style_index(styles, "page number").ok_or_else(|| {
            anyhow::anyhow!("DOCX reference document must define the `page number` character style")
        })?;
        let Node::Element(style) = &styles.children[index] else {
            unreachable!()
        };
        Some(style.attr("w:styleId").unwrap_or("page number"))
    } else {
        None
    };
    for (kind, text) in [
        (Some("begin"), None),
        (None, Some(instruction)),
        (Some("separate"), None),
        (None, Some(result)),
        (Some("end"), None),
    ] {
        let mut run = Element::new("w:r");
        if let Some(properties) = properties {
            run.push(properties.clone());
        }
        if let Some(identifier) = identifier {
            run.word("w:rPr").word("w:rStyle").set("w:val", identifier);
        }
        if let Some(kind) = kind {
            run.push(Element::with_attrs("w:fldChar", &[("w:fldCharType", kind)]));
        } else {
            let mut value = Element::new(if text == Some(instruction) {
                "w:instrText"
            } else {
                "w:t"
            });
            if text == Some(instruction) {
                value.set("xml:space", "preserve");
            }
            value
                .children
                .push(Node::Text(text.unwrap_or_default().into()));
            run.push(value);
        }
        paragraph.push(run);
    }
    Ok(())
}

/// Render an independent section story and return references for all page variants.
pub(super) fn section_story(
    package: &mut Package,
    document: &Element,
    styles: &Element,
    mut blocks: Vec<Node>,
    kind: &str,
    alignment: &str,
) -> Result<Vec<Element>> {
    let mut placeholder = Element::new("w:document");
    placeholder.push(Element::new("w:sectPr"));
    let name = create_primary_part(package, &mut placeholder, kind)?;
    let mut root = default_part(kind);
    for (key, value) in &document.attrs {
        if key.starts_with("xmlns:") || key == "mc:Ignorable" {
            root.set(key, value);
        }
    }
    let style_name = if kind == "footer" { "Footer" } else { "Header" };
    let identifier = style_index(styles, style_name)
        .and_then(|index| match &styles.children[index] {
            Node::Element(style) => style.attr("w:styleId"),
            _ => None,
        })
        .unwrap_or(style_name);
    for block in &mut blocks {
        finalize_page_fields(block, styles)?;
        format_paragraphs(block, alignment, identifier, false);
    }
    if !matches!(blocks.last(), Some(Node::Element(element)) if element.name == "w:p") {
        let mut paragraph = Element::new("w:p");
        paragraph
            .word("w:pPr")
            .word("w:pStyle")
            .set("w:val", identifier);
        paragraph.word("w:pPr").word("w:jc").set("w:val", alignment);
        blocks.push(Node::Element(paragraph));
    }
    root.children = blocks;
    relocate_relationships(package, &name, &mut root)?;
    relocate_identifiers(package, document, &mut root)?;
    package.set_xml(&name, &root);
    let reference = placeholder
        .child("w:sectPr")
        .and_then(|section| section.child(&format!("w:{kind}Reference")))
        .context("Missing generated section story reference")?;
    Ok(["default", "first", "even"]
        .into_iter()
        .map(|variant| {
            let mut reference = reference.clone();
            reference.set("w:type", variant);
            reference
        })
        .collect())
}

/// Clone a previous story part for unlinking while retaining its media relationships.
pub(super) fn unlink_story(
    package: &mut Package,
    document: &Element,
    kind: &str,
    reference: Option<&Element>,
    variant: &str,
) -> Result<Element> {
    let source = if let Some(reference) = reference {
        let relationships = package.xml("word/_rels/document.xml.rels")?;
        let relation = relationships
            .elements()
            .find(|relation| relation.attr("Id") == reference.attr("r:id"))
            .context("Missing inherited header/footer relationship")?;
        let target = relation
            .attr("Target")
            .context("Missing inherited header/footer target")?;
        Some(if target.starts_with('/') {
            target.trim_start_matches('/').to_owned()
        } else {
            format!("word/{target}")
        })
    } else {
        None
    };
    let mut placeholder = Element::new("w:document");
    placeholder.push(Element::new("w:sectPr"));
    let name = create_primary_part(package, &mut placeholder, kind)?;
    if let Some(source) = source {
        let mut root = package.xml(&source)?;
        relocate_identifiers(package, document, &mut root)?;
        package.set_xml(&name, &root);
        let source_rels = format!("word/_rels/{}.rels", source.trim_start_matches("word/"));
        let target_rels = format!("word/_rels/{}.rels", name.trim_start_matches("word/"));
        if let Some(bytes) = package.entries.get(&source_rels).cloned() {
            package.entries.insert(target_rels, bytes);
        }
    }
    let mut reference = placeholder
        .child("w:sectPr")
        .and_then(|section| section.child(&format!("w:{kind}Reference")))
        .context("Missing unlinked story reference")?
        .clone();
    reference.set("w:type", variant);
    Ok(reference)
}

/// Add a unique primary header/footer and connect the first section without altering section modes.
fn create_primary_part(
    package: &mut Package,
    document: &mut Element,
    kind: &str,
) -> Result<String> {
    let mut index = 1;
    while package
        .entries
        .contains_key(&format!("word/{kind}{index}.xml"))
    {
        index += 1;
    }
    let name = format!("word/{kind}{index}.xml");
    let target = format!("{kind}{index}.xml");
    package.set_xml(&name, &default_part(kind));
    let mut relationships = package.xml("word/_rels/document.xml.rels")?;
    let mut index = 1;
    while relationships
        .elements()
        .any(|relation| relation.attr("Id") == Some(format!("rId{index}").as_str()))
    {
        index += 1;
    }
    let identifier = format!("rId{index}");
    relationships.push(Element::with_attrs(
        "Relationship",
        &[
            ("Id", &identifier),
            (
                "Type",
                &format!(
                    "http://schemas.openxmlformats.org/officeDocument/2006/relationships/{kind}"
                ),
            ),
            ("Target", &target),
        ],
    ));
    package.set_xml("word/_rels/document.xml.rels", &relationships);
    let mut linked = false;
    document.visit_mut(&mut |element| {
        if element.name == "w:sectPr" && !linked {
            let reference = Element::with_attrs(
                &format!("w:{kind}Reference"),
                &[("w:type", "default"), ("r:id", &identifier)],
            );
            // Word requires header references before footer references and other section properties.
            let position = if kind == "footer" {
                element.children.iter().take_while(|node| {
                    matches!(node, Node::Element(child) if child.name == "w:headerReference")
                }).count()
            } else {
                0
            };
            element.children.insert(position, Node::Element(reference));
            linked = true;
        }
    });
    let mut types = package.xml("[Content_Types].xml")?;
    types.push(Element::with_attrs(
        "Override",
        &[
            ("PartName", &format!("/{name}")),
            (
                "ContentType",
                &format!(
                    "application/vnd.openxmlformats-officedocument.wordprocessingml.{kind}+xml"
                ),
            ),
        ],
    ));
    package.set_xml("[Content_Types].xml", &types);
    Ok(name)
}

/// Apply explicit footer page visibility without disturbing other footer text.
fn apply_page_numbers(
    package: &mut Package,
    document: &mut Element,
    styles: &Element,
    settings: &PmtSettings,
) -> Result<()> {
    let Some(visible) = settings.fields().docx_show_page_numbers else {
        return Ok(());
    };
    let mut names: Vec<String> = package
        .entries
        .keys()
        .filter(|name| name.starts_with("word/footer") && name.ends_with(".xml"))
        .cloned()
        .collect();
    if visible && names.is_empty() {
        names.push(create_primary_part(package, document, "footer")?);
    }
    for name in names {
        let mut footer = package.xml(&name)?;
        if visible {
            let mut existing = false;
            footer.visit(&mut |element| {
                if element.name == "w:instrText" && page_instruction(&element.direct_text())
                    || element.name == "w:fldSimple"
                        && element.attr("w:instr").is_some_and(page_instruction)
                {
                    existing = true;
                }
            });
            if !existing {
                let paragraph = footer.ensure("w:p");
                paragraph.word("w:pPr").word("w:jc").set("w:val", "center");
                append_page_field(paragraph, styles)?;
            }
        } else {
            footer.visit_mut(&mut |element| {
                if element.name == "w:p" {
                    remove_page_fields(element);
                }
            });
        }
        package.set_xml(&name, &footer);
    }
    Ok(())
}

/// Create a Word-compatible header/footer root with its established paragraph style.
fn default_part(kind: &str) -> Element {
    let mut footer = Element::new(if kind == "footer" { "w:ftr" } else { "w:hdr" });
    for (prefix, namespace) in [
        (
            "m",
            "http://schemas.openxmlformats.org/officeDocument/2006/math",
        ),
        (
            "mc",
            "http://schemas.openxmlformats.org/markup-compatibility/2006",
        ),
        (
            "mo",
            "http://schemas.microsoft.com/office/mac/office/2008/main",
        ),
        ("mv", "urn:schemas-microsoft-com:mac:vml"),
        ("o", "urn:schemas-microsoft-com:office:office"),
        (
            "r",
            "http://schemas.openxmlformats.org/officeDocument/2006/relationships",
        ),
        ("v", "urn:schemas-microsoft-com:vml"),
        (
            "w",
            "http://schemas.openxmlformats.org/wordprocessingml/2006/main",
        ),
        ("w10", "urn:schemas-microsoft-com:office:word"),
        (
            "w14",
            "http://schemas.microsoft.com/office/word/2010/wordml",
        ),
        (
            "wne",
            "http://schemas.microsoft.com/office/word/2006/wordml",
        ),
        (
            "wp",
            "http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing",
        ),
        (
            "wp14",
            "http://schemas.microsoft.com/office/word/2010/wordprocessingDrawing",
        ),
        (
            "wpc",
            "http://schemas.microsoft.com/office/word/2010/wordprocessingCanvas",
        ),
        (
            "wpg",
            "http://schemas.microsoft.com/office/word/2010/wordprocessingGroup",
        ),
        (
            "wpi",
            "http://schemas.microsoft.com/office/word/2010/wordprocessingInk",
        ),
        (
            "wps",
            "http://schemas.microsoft.com/office/word/2010/wordprocessingShape",
        ),
    ] {
        footer.set(&format!("xmlns:{prefix}"), namespace);
    }
    footer.set("mc:Ignorable", "w14 wp14");
    let mut paragraph = Element::new("w:p");
    paragraph
        .word("w:pPr")
        .word("w:pStyle")
        .set("w:val", if kind == "footer" { "Footer" } else { "Header" });
    footer.push(paragraph);
    footer
}

/// Recognize the PAGE instruction without matching NUMPAGES or unrelated text.
fn page_instruction(text: &str) -> bool {
    regex::Regex::new(r"(?i)\bPAGE\b")
        .expect("valid PAGE field grammar")
        .is_match(text)
}

/// Remove simple/complex PAGE ranges while retaining unrelated footer runs.
fn remove_page_fields(paragraph: &mut Element) {
    paragraph.children.retain(|node|!matches!(node,Node::Element(element)if element.name=="w:fldSimple"&&element.attr("w:instr").is_some_and(page_instruction)));
    let mut open: Vec<(usize, String)> = Vec::new();
    let mut ranges = Vec::new();
    for (index, node) in paragraph.children.iter().enumerate() {
        if let Node::Element(element) = node {
            element.visit(&mut |child| {
                if child.name == "w:fldChar" {
                    match child.attr("w:fldCharType") {
                        Some("begin") => open.push((index, String::new())),
                        Some("end") => {
                            if let Some((start, instruction)) = open.pop()
                                && page_instruction(&instruction)
                            {
                                ranges.push((start, index));
                            }
                        }
                        _ => {}
                    }
                }
                if child.name == "w:instrText" {
                    for (_, text) in &mut open {
                        text.push_str(&child.direct_text());
                    }
                }
            });
        }
    }
    for (start, end) in ranges.into_iter().rev() {
        paragraph.children.drain(start..=end);
    }
}
