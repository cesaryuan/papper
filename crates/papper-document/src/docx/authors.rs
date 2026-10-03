//! Insert keyed/inline author affiliations and corresponding-author footnotes.

use super::formatting::{get_style_mut, paragraph_style, set_bool, style_index};
use super::package::Package;
use super::xml::{Element, Node};
use anyhow::Result;
use serde_json::{Map, Value};
use std::collections::BTreeMap;

/// Retain author fields while resolving affiliation text to reusable keys.
struct Author {
    values: Map<String, Value>,
    affiliations: Vec<String>,
}

/// Normalize both keyed and inline metadata without dropping existing affiliation labels.
fn normalize(metadata: &Map<String, Value>) -> (Vec<Author>, BTreeMap<String, String>) {
    let raw_authors = metadata
        .get("authors")
        .or_else(|| metadata.get("author"))
        .and_then(Value::as_array);
    let raw_affiliations = metadata
        .get("affiliations")
        .or_else(|| metadata.get("affiliation"))
        .and_then(Value::as_object);
    let mut affiliations = BTreeMap::new();
    let mut ordered = Vec::new();
    if let Some(raw) = raw_affiliations {
        for (key, value) in raw {
            let value = value
                .as_str()
                .map(str::to_owned)
                .unwrap_or_else(|| value.to_string());
            if !value.trim().is_empty() {
                affiliations.insert(key.clone(), value.trim().into());
                ordered.push((key.clone(), value.trim().to_owned()));
            }
        }
    }
    let mut authors = Vec::new();
    for raw in raw_authors.into_iter().flatten() {
        let Some(values) = raw.as_object() else {
            continue;
        };
        let raw_keys = values
            .get("affiliations")
            .or_else(|| values.get("affiliation"));
        let keys: Vec<Value> = match raw_keys {
            Some(Value::Array(values)) => values.clone(),
            Some(Value::String(value)) => vec![Value::String(value.clone())],
            _ => Vec::new(),
        };
        let mut resolved = Vec::new();
        for value in keys {
            if value.is_null() {
                continue;
            }
            let value = value
                .as_str()
                .map(str::to_owned)
                .unwrap_or_else(|| value.to_string());
            let value = value.trim();
            if value.is_empty() {
                continue;
            }
            let key = if affiliations.contains_key(value) {
                value.to_owned()
            } else if let Some((key, _)) = ordered.iter().find(|(_, text)| text == value) {
                key.clone()
            } else {
                let key = alpha(affiliations.len() + 1);
                affiliations.insert(key.clone(), value.into());
                ordered.push((key.clone(), value.into()));
                key
            };
            resolved.push(key);
        }
        authors.push(Author {
            values: values.clone(),
            affiliations: resolved,
        });
    }
    (authors, affiliations)
}

/// Generate the existing alphabetic affiliation labels beyond the first 26 entries.
fn alpha(mut index: usize) -> String {
    let mut label = Vec::new();
    while index > 0 {
        index -= 1;
        label.push((b'a' + (index % 26) as u8) as char);
        index /= 26;
    }
    label.into_iter().rev().collect()
}

/// Create author text runs with the same explicit Word font and superscript properties.
fn text_run(text: &str, font: bool, size: Option<i64>, superscript: bool, italic: bool) -> Element {
    let mut run = Element::new("w:r");
    let mut properties = Element::new("w:rPr");
    if font {
        properties.push(Element::with_attrs(
            "w:rFonts",
            &[
                ("w:ascii", "Times New Roman"),
                ("w:hAnsi", "Times New Roman"),
                ("w:eastAsia", "Times New Roman"),
            ],
        ));
    }
    if let Some(size) = size {
        properties.push(Element::with_attrs("w:sz", &[("w:val", &size.to_string())]));
        properties.push(Element::with_attrs(
            "w:szCs",
            &[("w:val", &size.to_string())],
        ));
    }
    if superscript {
        properties.push(Element::with_attrs(
            "w:vertAlign",
            &[("w:val", "superscript")],
        ));
    }
    if italic {
        properties.push(Element::new("w:i"));
    }
    run.push(properties);
    let mut value = Element::with_attrs("w:t", &[("xml:space", "preserve")]);
    value.children.push(Node::Text(text.into()));
    run.push(value);
    run
}

/// Ensure the shared italic affiliation paragraph style exists before insertion.
fn ensure_affiliation_style(styles: &mut Element) {
    if style_index(styles, "Affiliation").is_some() {
        return;
    }
    let base = styles
        .elements()
        .find(|style| {
            style.attr("w:type") == Some("paragraph") && style.attr("w:default") == Some("1")
        })
        .and_then(|style| style.attr("w:styleId"))
        .unwrap_or("Normal")
        .to_owned();
    let mut style = Element::with_attrs(
        "w:style",
        &[
            ("w:type", "paragraph"),
            ("w:customStyle", "1"),
            ("w:styleId", "Affiliation"),
        ],
    );
    style.word("w:name").set("w:val", "Affiliation");
    style.word("w:basedOn").set("w:val", base);
    let properties = style.word("w:rPr");
    let fonts = properties.word("w:rFonts");
    fonts.set("w:ascii", "Times New Roman");
    fonts.set("w:hAnsi", "Times New Roman");
    properties.word("w:sz").set("w:val", "20");
    set_bool(properties, "w:i", true);
    let properties = style.word("w:pPr");
    properties.word("w:jc").set("w:val", "center");
    let spacing = properties.word("w:spacing");
    spacing.set("w:before", "120");
    spacing.set("w:after", "120");
    set_bool(&mut style, "w:semiHidden", false);
    set_bool(&mut style, "w:qFormat", true);
    style.word("w:uiPriority").set("w:val", "1");
    styles.push(style);
}

/// Build the existing corresponding-author message or accept its explicit custom text.
fn corresponding_text(authors: &[Author], affiliations: &BTreeMap<String, String>) -> String {
    for author in authors {
        let Some(corresponding) = author.values.get("corresponding").filter(|value| {
            value.as_bool() == Some(true) || value.as_str().is_some_and(|raw| !raw.is_empty())
        }) else {
            continue;
        };
        if let Some(text) = corresponding.as_str() {
            return text.trim().into();
        }
        let name = author
            .values
            .get("name")
            .and_then(Value::as_str)
            .unwrap_or_default();
        let mut text = format!("*Correspondence to: Dr. {name}");
        if let Some(title) = author
            .values
            .get("title")
            .and_then(Value::as_str)
            .filter(|value| !value.is_empty())
        {
            text.push_str(&format!(" ({title})"));
        }
        if let Some(affiliation) = author
            .affiliations
            .first()
            .and_then(|key| affiliations.get(key))
            .filter(|value| !value.is_empty())
        {
            text.push_str(&format!(", {affiliation}"));
        }
        if let Some(email) = author
            .values
            .get("email")
            .and_then(Value::as_str)
            .filter(|value| !value.is_empty())
        {
            text.push_str(&format!(". E-mail: {email}."));
        }
        return text;
    }
    String::new()
}

/// Insert new metadata paragraphs after the existing title while preserving all later blocks.
pub(crate) fn insert(
    package: &mut Package,
    document: &mut Element,
    styles: &mut Element,
    metadata: &Map<String, Value>,
) -> Result<()> {
    let (authors, affiliations) = normalize(metadata);
    if authors.is_empty() {
        return Ok(());
    }
    ensure_affiliation_style(styles);
    let mut paragraph = Element::new("w:p");
    paragraph.word("w:pPr").word("w:jc").set("w:val", "center");
    let mut footnote_index = None;
    for (index, author) in authors.iter().enumerate() {
        let name = author
            .values
            .get("name")
            .and_then(Value::as_str)
            .unwrap_or_default();
        paragraph.push(text_run(name, true, Some(24), false, false));
        if !author.affiliations.is_empty() {
            paragraph.push(text_run(
                &author.affiliations.join(","),
                true,
                Some(24),
                true,
                false,
            ));
            if author.values.get("corresponding").is_some_and(|value| {
                value.as_bool() == Some(true) || value.as_str().is_some_and(|raw| !raw.is_empty())
            }) {
                let mut run = text_run("", true, None, true, false);
                run.remove("w:t");
                footnote_index = Some(paragraph.children.len());
                paragraph.push(run);
            }
        }
        if index + 1 < authors.len() {
            paragraph.push(text_run(", ", true, Some(24), false, false));
        }
    }
    let message = corresponding_text(&authors, &affiliations);
    if let Some(index) = footnote_index.filter(|_| !message.is_empty()) {
        add_footnote(package, &message)?;
        if let Node::Element(run) = &mut paragraph.children[index] {
            let reference_properties = text_run("", true, None, true, false)
                .child("w:rPr")
                .unwrap()
                .clone();
            run.children.insert(0, Node::Element(reference_properties));
            run.push(Element::with_attrs(
                "w:footnoteReference",
                &[("w:customMarkFollows", "1"), ("w:id", "1")],
            ));
            let mut mark = Element::with_attrs("w:t", &[("xml:space", "preserve")]);
            mark.children.push(Node::Text("*".into()));
            run.push(mark);
        }
    }
    let mut inserted = vec![Node::Element(paragraph)];
    let mut keys: Vec<_> = affiliations.keys().collect();
    keys.sort_by(|a, b| match (a.parse::<i64>(), b.parse::<i64>()) {
        (Ok(a), Ok(b)) => a.cmp(&b),
        (Ok(_), Err(_)) => std::cmp::Ordering::Less,
        (Err(_), Ok(_)) => std::cmp::Ordering::Greater,
        _ => a.cmp(b),
    });
    for key in keys {
        let mut paragraph = Element::new("w:p");
        let properties = paragraph.word("w:pPr");
        properties.word("w:pStyle").set("w:val", "Affiliation");
        properties.word("w:jc").set("w:val", "center");
        paragraph.push(text_run(key, false, None, true, false));
        paragraph.push(text_run(
            &format!(" {}", affiliations[key]),
            false,
            None,
            false,
            false,
        ));
        inserted.push(Node::Element(paragraph));
    }
    let body = document
        .child_mut("w:body")
        .ok_or_else(|| anyhow::anyhow!("DOCX document is missing its body"))?;
    let title_id = get_style_mut(styles, "Title")
        .and_then(|style| style.attr("w:styleId"))
        .unwrap_or("Title")
        .to_owned();
    let title=body.children.iter().position(|node|matches!(node,Node::Element(paragraph)if paragraph.name=="w:p"&&paragraph_style(paragraph)==Some(title_id.as_str())))
        .or_else(||body.children.iter().position(|node|matches!(node,Node::Element(paragraph)if paragraph.name=="w:p"))).unwrap_or(0);
    body.children.splice(title + 1..title + 1, inserted);
    Ok(())
}

/// Append the custom-mark author footnote and preserve any existing footnotes package part.
fn add_footnote(package: &mut Package, message: &str) -> Result<()> {
    if !package.entries.contains_key("word/footnotes.xml") {
        let template=b"<w:footnotes xmlns:w=\"http://schemas.openxmlformats.org/wordprocessingml/2006/main\" xmlns:r=\"http://schemas.openxmlformats.org/officeDocument/2006/relationships\">\n    <w:footnote w:type=\"separator\" w:id=\"-1\">\n        <w:p><w:r><w:separator/></w:r></w:p>\n    </w:footnote>\n    <w:footnote w:type=\"continuationSeparator\" w:id=\"0\">\n        <w:p><w:r><w:continuationSeparator/></w:r></w:p>\n    </w:footnote>\n</w:footnotes>";
        package
            .entries
            .insert("word/footnotes.xml".into(), template.to_vec());
        let mut relations = package.xml("word/_rels/document.xml.rels")?;
        let mut index = 1;
        while relations
            .elements()
            .any(|relation| relation.attr("Id") == Some(format!("rId{index}").as_str()))
        {
            index += 1;
        }
        relations.push(Element::with_attrs(
            "Relationship",
            &[
                ("Id", &format!("rId{index}")),
                ("Target", "footnotes.xml"),
                (
                    "Type",
                    "http://schemas.openxmlformats.org/officeDocument/2006/relationships/footnotes",
                ),
            ],
        ));
        package.set_xml("word/_rels/document.xml.rels", &relations);
        let mut types = package.xml("[Content_Types].xml")?;
        types.push(Element::with_attrs(
            "Override",
            &[
                ("PartName", "/word/footnotes.xml"),
                (
                    "ContentType",
                    "application/vnd.openxmlformats-officedocument.wordprocessingml.footnotes+xml",
                ),
            ],
        ));
        package.set_xml("[Content_Types].xml", &types);
    }
    let mut footnotes = package.xml("word/footnotes.xml")?;
    let mut footnote = Element::with_attrs("w:footnote", &[("w:id", "1")]);
    let mut paragraph = Element::new("w:p");
    paragraph
        .word("w:pPr")
        .word("w:pStyle")
        .set("w:val", "FootnoteText");
    paragraph.push(text_run(message, true, Some(20), false, true));
    footnote.push(paragraph);
    footnotes.push(footnote);
    package.set_xml("word/footnotes.xml", &footnotes);
    Ok(())
}
