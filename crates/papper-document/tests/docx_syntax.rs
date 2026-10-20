//! Exercise public DOCX postprocessing to verify nonfatal diagnostics and usable review comments.

use anyhow::Result;
use papper_core::metadata::{EffectiveMetadata, PmtSettings};
use papper_document::docx::{DocxPostprocessOptions, postprocess_docx};
use std::collections::{BTreeMap, BTreeSet};
use std::io::{Read, Write};
use std::path::Path;

const W: &str = "http://schemas.openxmlformats.org/wordprocessingml/2006/main";

/// Read exported parts independently of Papper's internal XML and OPC helpers.
fn parts(path: &Path) -> Result<BTreeMap<String, Vec<u8>>> {
    let mut zip = zip::ZipArchive::new(std::fs::File::open(path)?)?;
    let mut parts = BTreeMap::new();
    for index in 0..zip.len() {
        let mut part = zip.by_index(index)?;
        let mut bytes = Vec::new();
        part.read_to_end(&mut bytes)?;
        parts.insert(part.name().to_owned(), bytes);
    }
    Ok(parts)
}

/// Build a real Word package with controlled body text and optional authored review content.
fn fixture(path: &Path, body: &str, existing_comments: bool, other_stories: bool) -> Result<()> {
    let reference = Path::new(env!("CARGO_MANIFEST_DIR"))
        .join("../../pandoc/manuscript-template/reference-doc.docx");
    let mut entries = parts(&reference)?;
    let section = if other_stories {
        "<w:sectPr><w:headerReference w:type=\"default\" r:id=\"rIdTestHeader\"/><w:footerReference w:type=\"default\" r:id=\"rIdTestFooter\"/></w:sectPr>"
    } else {
        "<w:sectPr/>"
    };
    entries.insert("word/document.xml".into(), format!(
        "<w:document xmlns:w=\"{W}\" xmlns:r=\"http://schemas.openxmlformats.org/officeDocument/2006/relationships\"><w:body>{body}{section}</w:body></w:document>"
    ).into_bytes());
    let mut rels = String::from_utf8(entries["word/_rels/document.xml.rels"].clone())?;
    let mut types = String::from_utf8(entries["[Content_Types].xml"].clone())?;
    if existing_comments {
        // A nonstandard part name catches implementations that overwrite comments.xml blindly.
        entries.insert("word/review/feedback.xml".into(), format!(
            "<w:comments xmlns:w=\"{W}\"><w:comment w:id=\"0\" w:author=\"Reviewer\"><w:p><w:r><w:t>Keep the author's comment</w:t></w:r></w:p></w:comment></w:comments>"
        ).into_bytes());
        rels = rels.replace("</Relationships>", "<Relationship Id=\"rIdExistingReview\" Type=\"http://schemas.openxmlformats.org/officeDocument/2006/relationships/comments\" Target=\"review/feedback.xml\"/></Relationships>");
        types = types.replace("</Types>", "<Override PartName=\"/word/review/feedback.xml\" ContentType=\"application/vnd.openxmlformats-officedocument.wordprocessingml.comments+xml\"/></Types>");
    }
    if other_stories {
        for (part, root, content_type, text) in [
            (
                "header1",
                "hdr",
                "header",
                "&lt;div&gt;Header syntax&lt;/div&gt;",
            ),
            ("footer1", "ftr", "footer", "Table Table 7"),
            (
                "footnotes",
                "footnotes",
                "footnotes",
                "Footnote @missing2024",
            ),
            ("endnotes", "endnotes", "endnotes", "Equation (Equation 9)"),
        ] {
            let paragraph = format!("<w:p><w:r><w:t>{text}</w:t></w:r></w:p>");
            let content = match root {
                "footnotes" => format!("<w:footnote w:id=\"1\">{paragraph}</w:footnote>"),
                "endnotes" => format!("<w:endnote w:id=\"1\">{paragraph}</w:endnote>"),
                _ => paragraph,
            };
            entries.insert(
                format!("word/{part}.xml"),
                format!("<w:{root} xmlns:w=\"{W}\">{content}</w:{root}>").into_bytes(),
            );
            if matches!(root, "hdr" | "ftr") {
                let id = if root == "hdr" {
                    "rIdTestHeader"
                } else {
                    "rIdTestFooter"
                };
                rels = rels.replace("</Relationships>", &format!("<Relationship Id=\"{id}\" Type=\"http://schemas.openxmlformats.org/officeDocument/2006/relationships/{content_type}\" Target=\"{part}.xml\"/></Relationships>"));
                types = types.replace("</Types>", &format!("<Override PartName=\"/word/{part}.xml\" ContentType=\"application/vnd.openxmlformats-officedocument.wordprocessingml.{content_type}+xml\"/></Types>"));
            }
        }
    }
    entries.insert("word/_rels/document.xml.rels".into(), rels.into_bytes());
    entries.insert("[Content_Types].xml".into(), types.into_bytes());
    let mut writer = zip::ZipWriter::new(std::fs::File::create(path)?);
    for (name, bytes) in entries {
        writer.start_file(name, zip::write::SimpleFileOptions::default())?;
        writer.write_all(&bytes)?;
    }
    writer.finish()?;
    Ok(())
}

/// Finalize the public output with ordinary formatting enabled and native equations retained.
fn process(input: &Path, output: &Path) -> Result<()> {
    let effective = EffectiveMetadata {
        pmt_settings: PmtSettings::default(),
        pandoc_metadata: Default::default(),
        has_yaml_header: false,
    };
    postprocess_docx(
        input,
        output,
        &effective,
        &DocxPostprocessOptions::default(),
    )
}

/// Extract visible text while excluding comment anchors and all XML metadata.
fn text(node: roxmltree::Node<'_, '_>) -> String {
    node.descendants()
        .filter(|node| node.has_tag_name((W, "t")))
        .filter_map(|node| node.text())
        .collect()
}

/// Prevent the former build failure and catch missing/mismatched anchors across all scan locations.
#[test]
fn syntax_findings_publish_comments_instead_of_failing() -> Result<()> {
    let project = tempfile::tempdir()?;
    let input = project.path().join("input.docx");
    let output = project.path().join("output.docx");
    let snippets = [
        "::: {#example}",
        "{#fig:demo}",
        "@fig:missing",
        "[@smith2024]",
        "&lt;span&gt;raw&lt;/span&gt;",
        "Figure Figure 1",
        "Figure (Figure 2)",
        "Table 3 Table 3",
    ];
    let body: String = snippets
        .iter()
        .map(|snippet| format!("<w:p><w:r><w:t>{snippet}</w:t></w:r></w:p>"))
        .collect();
    fixture(&input, &body, false, true)?;
    process(&input, &output)?;
    let entries = parts(&output)?;
    let comments = roxmltree::Document::parse(std::str::from_utf8(&entries["word/comments.xml"])?)?;
    let comment_nodes: Vec<_> = comments
        .descendants()
        .filter(|node| node.has_tag_name((W, "comment")))
        .collect();
    assert_eq!(comment_nodes.len(), snippets.len() + 4);
    let ids: BTreeSet<_> = comment_nodes
        .iter()
        .map(|node| node.attribute((W, "id")).unwrap())
        .collect();
    assert_eq!(ids.len(), comment_nodes.len());
    let document = roxmltree::Document::parse(std::str::from_utf8(&entries["word/document.xml"])?)?;
    for kind in ["commentRangeStart", "commentRangeEnd", "commentReference"] {
        let anchors: Vec<_> = document
            .descendants()
            .filter(|node| node.has_tag_name((W, kind)))
            .map(|node| node.attribute((W, "id")).unwrap())
            .collect();
        assert_eq!(anchors.len(), ids.len());
        assert_eq!(anchors.into_iter().collect::<BTreeSet<_>>(), ids);
    }
    let paragraphs: Vec<_> = document
        .descendants()
        .filter(|node| node.has_tag_name((W, "p")))
        .collect();
    for (index, paragraph) in paragraphs.iter().enumerate() {
        let count = paragraph
            .descendants()
            .filter(|node| node.has_tag_name((W, "commentReference")))
            .count();
        assert_eq!(count, if index == 0 { 5 } else { 1 });
    }
    for (source, label) in [
        ("header1", "Header"),
        ("footer1", "Footer"),
        ("footnotes", "Footnotes"),
        ("endnotes", "Endnotes"),
    ] {
        assert!(
            comment_nodes
                .iter()
                .any(|node| text(*node).contains(&format!("Source: {label}")))
        );
        let name = format!("word/{source}.xml");
        let original_entries = parts(&input)?;
        let original = roxmltree::Document::parse(std::str::from_utf8(&original_entries[&name])?)?;
        let result = roxmltree::Document::parse(std::str::from_utf8(&entries[&name])?)?;
        assert_eq!(text(result.root_element()), text(original.root_element()));
        assert!(
            !result
                .descendants()
                .any(|node| node.has_tag_name((W, "commentReference")))
        );
    }
    let rels = std::str::from_utf8(&entries["word/_rels/document.xml.rels"])?;
    assert!(rels.contains("Target=\"comments.xml\""));
    let types = std::str::from_utf8(&entries["[Content_Types].xml"])?;
    assert!(
        types.contains(
            "application/vnd.openxmlformats-officedocument.wordprocessingml.comments+xml"
        )
    );
    Ok(())
}

/// Catch overwritten review comments, lost formatting, skipped later categories and duplicate reprocessing.
#[test]
fn annotations_preserve_authored_comments_and_inline_content() -> Result<()> {
    let project = tempfile::tempdir()?;
    let input = project.path().join("input.docx");
    let output = project.path().join("output.docx");
    let repeated = project.path().join("repeated.docx");
    fixture(
        &input,
        r#"<w:p><w:pPr><w:keepNext/></w:pPr><w:commentRangeStart w:id="0"/><w:r><w:rPr><w:b/></w:rPr><w:t>Figure </w:t></w:r><w:hyperlink r:id="rId8"><w:r><w:t>Figure 1 &lt;div&gt;</w:t></w:r></w:hyperlink><w:fldSimple w:instr="PAGE"><w:r><w:t>1</w:t></w:r></w:fldSimple><w:commentRangeEnd w:id="0"/><w:r><w:commentReference w:id="0"/></w:r></w:p>"#,
        true,
        false,
    )?;
    process(&input, &output)?;
    process(&output, &repeated)?;
    let entries = parts(&output)?;
    let repeated_entries = parts(&repeated)?;
    let comments_part = "word/review/feedback.xml";
    assert!(!entries.contains_key("word/comments.xml"));
    assert_eq!(entries[comments_part], repeated_entries[comments_part]);
    let comments = roxmltree::Document::parse(std::str::from_utf8(&entries[comments_part])?)?;
    let nodes: Vec<_> = comments
        .descendants()
        .filter(|node| node.has_tag_name((W, "comment")))
        .collect();
    assert_eq!(nodes.len(), 2);
    assert_eq!(text(nodes[0]), "Keep the author's comment");
    let warning = text(nodes[1]);
    assert!(warning.contains("raw HTML") && warning.contains("duplicated reference"));
    let document = roxmltree::Document::parse(std::str::from_utf8(&entries["word/document.xml"])?)?;
    assert_eq!(text(document.root_element()), "Figure Figure 1 <div>1");
    assert!(
        document
            .descendants()
            .any(|node| node.has_tag_name((W, "b")))
    );
    assert!(
        document
            .descendants()
            .any(|node| node.has_tag_name((W, "hyperlink")))
    );
    assert!(
        document
            .descendants()
            .any(|node| node.has_tag_name((W, "fldSimple"))
                && node.attribute((W, "instr")) == Some("PAGE"))
    );
    assert_eq!(
        entries["word/document.xml"],
        repeated_entries["word/document.xml"]
    );
    Ok(())
}

/// Keep ordinary scientific text, email addresses, paths and hidden MathType sources unannotated.
#[test]
fn clean_text_does_not_create_review_comments() -> Result<()> {
    let project = tempfile::tempdir()?;
    let input = project.path().join("input.docx");
    let output = project.path().join("output.docx");
    fixture(
        &input,
        r#"<w:p><w:r><w:t>Use &lt;k&gt; and &lt;degree&gt;, name@example.com, /@fig:demo, Figure 1 Figure 2</w:t></w:r><w:r><w:rPr><w:vanish/></w:rPr><w:t>MTLATEX: @fig:hidden &lt;div&gt;</w:t></w:r></w:p>"#,
        false,
        false,
    )?;
    process(&input, &output)?;
    let entries = parts(&output)?;
    assert!(!entries.contains_key("word/comments.xml"));
    assert!(!std::str::from_utf8(&entries["word/document.xml"])?.contains("commentReference"));
    Ok(())
}
