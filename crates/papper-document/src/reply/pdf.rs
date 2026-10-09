//! Extract PDF text, metadata and physical lines with the Rust PDF Oxide engine.
//!
//! The isolated `__pdf_extract` command keeps the existing page text/layout JSON
//! contract. Visual words are joined by their actual gaps, keeping inline numbers
//! with the body while emitting left-margin manuscript numbers separately.
//! Rendering, OCR, system fonts and language bindings are unnecessary here.

use anyhow::{Context, Result};
use pdf_oxide::document::PdfDocument;
use pdf_oxide::geometry::Rect;
use pdf_oxide::layout::{TextLine, Word};
use pdf_oxide::optional_content::decode_pdf_text_string;
use serde_json::{Value, json};
use std::path::Path;

/// Decode optional document information, including UTF-16 metadata from Word.
fn metadata(document: &PdfDocument) -> Result<serde_json::Map<String, Value>> {
    let mut metadata = serde_json::Map::new();
    let Some(info) = document
        .trailer()
        .as_dict()
        .and_then(|dict| dict.get("Info"))
    else {
        return Ok(metadata);
    };
    let info = document
        .resolve_object(info)
        .context("Could not read PDF metadata")?;
    if let Some(info) = info.as_dict() {
        for key in [
            "Producer", "Creator", "Title", "Author", "Subject", "Keywords",
        ] {
            if let Some(value) = info.get(key) {
                let value = document.resolve_object(value)?;
                if let Some(bytes) = value.as_string() {
                    metadata.insert(key.into(), decode_pdf_text_string(bytes).into());
                }
            }
        }
    }
    Ok(metadata)
}

/// Emit the rectangle array accepted by the reviewer line matcher.
fn line_json(text: &str, bbox: Rect) -> Value {
    json!({"text": text, "bbox": [bbox.x, bbox.y, bbox.x + bbox.width, bbox.y + bbox.height]})
}

/// Join adjacent visual words without adding spaces around split punctuation.
fn body_line(words: &[&Word]) -> Option<Value> {
    let first = words.first()?;
    let mut text = String::new();
    let mut bbox = first.bbox;
    let mut previous_end = None;
    for word in words {
        // Word references and math runs may be separate PDF spans with no space.
        // The library's TextLine.text inserts one unconditionally, breaking regexes.
        if previous_end.is_some_and(|end| word.bbox.x - end > 0.5)
            && !text.ends_with(char::is_whitespace)
            && !word.text.starts_with(char::is_whitespace)
        {
            text.push(' ');
        }
        text.push_str(&word.text);
        bbox = bbox.union(&word.bbox);
        previous_end = Some(word.bbox.x + word.bbox.width);
    }
    Some(line_json(&text, bbox))
}

/// Separate left-margin numbers from visual rows while retaining inline references.
fn layout(lines: &[TextLine]) -> Value {
    let body_left = lines
        .iter()
        .flat_map(|line| &line.words)
        .filter(|word| word.text.trim().parse::<usize>().is_err())
        .map(|word| word.bbox.x)
        .fold(f32::INFINITY, f32::min);
    let mut output = Vec::new();
    for line in lines {
        let mut words: Vec<_> = line.words.iter().collect();
        words.sort_by(|left, right| left.bbox.x.total_cmp(&right.bbox.x));
        let mut body = Vec::new();
        for word in words {
            // PDF Oxide groups the margin number and body into the same visual row.
            // Splitting only numbers left of the body avoids losing inline citations.
            if word.bbox.x < body_left - 2.0 && word.text.trim().parse::<usize>().is_ok() {
                output.push(line_json(word.text.trim(), word.bbox));
            } else {
                body.push(word);
            }
        }
        if let Some(line) = body_line(&body) {
            output.push(line);
        }
    }
    json!({"lines": output})
}

/// Read page text, physical line boxes and producer information in the isolated helper.
pub fn extract(path: &Path) -> Result<Value> {
    let document = PdfDocument::open(path)
        .with_context(|| format!("Could not open PDF {}", path.display()))?;
    let metadata = metadata(&document)?;
    let mut pages = Vec::new();
    for number in 0..document.page_count()? {
        let lines = document
            .extract_text_lines(number)
            .with_context(|| format!("Could not read PDF page {} layout", number + 1))?;
        let text = document
            .extract_text(number)
            .with_context(|| format!("Could not read PDF page {} text", number + 1))?;
        pages.push(json!({"layout": layout(&lines), "text": text}));
    }
    Ok(json!({"metadata": metadata, "pages": pages}))
}
