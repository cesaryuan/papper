//! Render configured footer text and dynamic Word page fields, preserving legacy visibility behavior.

use super::formatting::style_index;
use super::package::Package;
use super::xml::{Element, Node};
use anyhow::Result;
use papper_core::metadata::{FooterAlignment, PmtSettings};

/// Replace configured footers uniformly, or retain the old page-visibility behavior.
pub(crate) fn apply(
    package: &mut Package,
    document: &mut Element,
    styles: &Element,
    settings: &PmtSettings,
) -> Result<()> {
    let Some(config) = &settings.fields().docx_footer else {
        if settings.was_provided("docxFooter") {
            return Ok(());
        }
        return apply_page_numbers(package, document, styles, settings);
    };
    let content = config.content.as_deref().unwrap_or("[]{t=\"pageNum\"}");
    let alignment = config.alignment.unwrap_or(FooterAlignment::Center).as_str();
    let mut names: Vec<String> = package
        .entries
        .keys()
        .filter(|name| name.starts_with("word/footer") && name.ends_with(".xml"))
        .cloned()
        .collect();
    let mut first_section = true;
    let mut has_primary = false;
    document.visit(&mut |element| {
        if element.name == "w:sectPr" && first_section {
            has_primary = element.elements().any(|child| {
                child.name == "w:footerReference" && child.attr("w:type") == Some("default")
            });
            first_section = false;
        }
    });
    // First/even-only templates still need a primary footer on ordinary pages.
    // Later sections inherit this reference unless they already define their own.
    if !has_primary && !content.is_empty() {
        names.push(create_primary_footer(package, document)?);
    }
    for name in names {
        let mut footer = package.xml(&name)?;
        footer.children.clear();
        let mut paragraph = Element::new("w:p");
        paragraph
            .word("w:pPr")
            .word("w:pStyle")
            .set("w:val", "Footer");
        paragraph.word("w:pPr").word("w:jc").set("w:val", alignment);
        append_content(&mut paragraph, content, styles)?;
        footer.push(paragraph);
        package.set_xml(&name, &footer);
    }
    Ok(())
}

/// Split plain footer text around empty page spans, e.g. `- []{t="pageNum"} -`.
fn append_content(paragraph: &mut Element, content: &str, styles: &Element) -> Result<()> {
    static PAGE_SPAN: std::sync::OnceLock<regex::Regex> = std::sync::OnceLock::new();
    let pattern = PAGE_SPAN.get_or_init(|| {
        regex::Regex::new(r#"\[\]\{\s*t\s*=\s*(?:"pageNum"|'pageNum'|pageNum)\s*\}"#)
            .expect("valid footer page span grammar")
    });
    let mut offset = 0;
    for marker in pattern.find_iter(content) {
        append_text(paragraph, &content[offset..marker.start()]);
        append_page_field(paragraph, styles)?;
        offset = marker.end();
    }
    append_text(paragraph, &content[offset..]);
    Ok(())
}

/// Preserve decorative spaces, XML-sensitive text, tabs, and authored line breaks.
fn append_text(paragraph: &mut Element, text: &str) {
    if text.is_empty() {
        return;
    }
    let mut run = Element::new("w:r");
    for (index, line) in text.replace("\r\n", "\n").split('\n').enumerate() {
        if index > 0 {
            run.push(Element::new("w:br"));
        }
        for (index, part) in line.split('\t').enumerate() {
            if index > 0 {
                run.push(Element::new("w:tab"));
            }
            if !part.is_empty() {
                let mut value = Element::with_attrs("w:t", &[("xml:space", "preserve")]);
                value.children.push(Node::Text(part.into()));
                run.push(value);
            }
        }
    }
    paragraph.push(run);
}

/// Insert a dynamic PAGE field using the reference's page-number character style.
fn append_page_field(paragraph: &mut Element, styles: &Element) -> Result<()> {
    let index = style_index(styles, "page number").ok_or_else(|| {
        anyhow::anyhow!("DOCX reference document must define the `page number` character style")
    })?;
    let Node::Element(style) = &styles.children[index] else {
        unreachable!()
    };
    let identifier = style.attr("w:styleId").unwrap_or("page number");
    for (kind, text) in [
        (Some("begin"), None),
        (None, Some(" PAGE ")),
        (Some("separate"), None),
        (None, Some("1")),
        (Some("end"), None),
    ] {
        let mut run = Element::new("w:r");
        run.word("w:rPr").word("w:rStyle").set("w:val", identifier);
        if let Some(kind) = kind {
            run.push(Element::with_attrs("w:fldChar", &[("w:fldCharType", kind)]));
        } else {
            let mut value = Element::new(if text == Some(" PAGE ") {
                "w:instrText"
            } else {
                "w:t"
            });
            if text == Some(" PAGE ") {
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

/// Add a unique primary footer and connect the first section without altering section modes.
fn create_primary_footer(package: &mut Package, document: &mut Element) -> Result<String> {
    let mut index = 1;
    while package
        .entries
        .contains_key(&format!("word/footer{index}.xml"))
    {
        index += 1;
    }
    let name = format!("word/footer{index}.xml");
    let target = format!("footer{index}.xml");
    package.set_xml(&name, &default_footer());
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
                "http://schemas.openxmlformats.org/officeDocument/2006/relationships/footer",
            ),
            ("Target", &target),
        ],
    ));
    package.set_xml("word/_rels/document.xml.rels", &relationships);
    let mut linked = false;
    document.visit_mut(&mut |element| {
        if element.name == "w:sectPr" && !linked {
            let reference = Element::with_attrs(
                "w:footerReference",
                &[("w:type", "default"), ("r:id", &identifier)],
            );
            // Insert before other section properties; existing first/even references stay intact.
            element.children.insert(0, Node::Element(reference));
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
                "application/vnd.openxmlformats-officedocument.wordprocessingml.footer+xml",
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
        names.push(create_primary_footer(package, document)?);
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

/// Create the Word-compatible primary footer with the established Footer style.
fn default_footer() -> Element {
    let mut footer = Element::new("w:ftr");
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
        .set("w:val", "Footer");
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
