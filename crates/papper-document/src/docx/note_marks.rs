//! Apply custom correspondence marks requested by Lua to already generated notes.
//!
//! Pandoc allocates native footnote IDs and package relationships. Its Note AST
//! cannot select a custom mark, so this pass consumes the filter's hidden request
//! and changes only the reference and note label. Ordinary footnotes retain their
//! automatic numbering and all existing note IDs and content remain intact.

use super::package::Package;
use super::xml::{Element, Node};
use anyhow::{Result, ensure};
use std::collections::BTreeSet;

const MARK_REQUEST: &str = "PMT_CORRESPONDENCE_MARK:*";

/// Remove only the hidden request run emitted by the correspondence Lua filter.
fn remove_request(element: &mut Element) {
    element.children.retain(|node| {
        !matches!(node, Node::Element(run) if run.name == "w:r"
            && run.contains("w:vanish") && run.text() == MARK_REQUEST)
    });
    for child in element.elements_mut() {
        remove_request(child);
    }
}

/// Construct the literal custom mark while leaving superscript formatting to its run.
fn mark_text() -> Element {
    let mut text = Element::new("w:t");
    text.children.push(Node::Text("*".into()));
    text
}

/// Replace note labels and references for explicitly marked correspondence notes.
pub(super) fn apply(package: &mut Package, document: &mut Element) -> Result<bool> {
    let Some(bytes) = package.entries.get("word/footnotes.xml") else {
        return Ok(false);
    };
    // Most documents contain no correspondence notes; avoid parsing their note part.
    if !bytes
        .windows(MARK_REQUEST.len())
        .any(|part| part == MARK_REQUEST.as_bytes())
    {
        return Ok(false);
    }
    let mut notes = package.xml("word/footnotes.xml")?;
    let mut ids = BTreeSet::new();
    for note in notes
        .elements_mut()
        .filter(|note| note.name == "w:footnote")
    {
        let mut requested = false;
        note.visit(&mut |run| {
            requested |=
                run.name == "w:r" && run.contains("w:vanish") && run.text() == MARK_REQUEST;
        });
        if !requested {
            continue;
        }
        let id = note
            .attr("w:id")
            .ok_or_else(|| anyhow::anyhow!("Correspondence footnote is missing its ID"))?
            .to_owned();
        remove_request(note);
        let mut labels = 0;
        note.visit_mut(&mut |run| {
            if run.name == "w:r" && run.child("w:footnoteRef").is_some() {
                run.remove("w:footnoteRef");
                run.push(mark_text());
                labels += 1;
            }
        });
        ensure!(
            labels == 1,
            "Correspondence footnote {id} must have one label"
        );
        ids.insert(id);
    }
    let mut referenced = BTreeSet::new();
    document.visit_mut(&mut |run| {
        if run.name != "w:r" {
            return;
        }
        let Some(reference) = run.child_mut("w:footnoteReference") else {
            return;
        };
        let Some(id) = reference
            .attr("w:id")
            .filter(|id| ids.contains(*id))
            .map(str::to_owned)
        else {
            return;
        };
        // customMarkFollows suppresses automatic numbering only for this note.
        reference.set("w:customMarkFollows", "1");
        run.push(mark_text());
        referenced.insert(id);
    });
    ensure!(
        ids == referenced,
        "Correspondence footnote is missing its document reference"
    );
    if ids.is_empty() {
        return Ok(false);
    }
    package.set_xml("word/footnotes.xml", &notes);
    Ok(true)
}
