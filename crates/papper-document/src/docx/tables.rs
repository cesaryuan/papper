//! Preserve the existing table metadata, subfigure, and display-equation layout rules.

use super::formatting::{get_style_mut, paragraph_style, set_bool, style_index};
use super::xml::{Element, Node};
use anyhow::Result;
use serde_json::Value;

/// Identify structural equation tables using both math and their surrounding label text.
pub(crate) fn is_equation(table: &Element) -> bool {
    if !(table.contains("m:oMath") || table.contains("m:oMathPara")) {
        return false;
    }
    if table.elements().filter(|row| row.name == "w:tr").count() != 1
        || table
            .child("w:tblGrid")
            .map(|grid| {
                grid.elements()
                    .filter(|column| column.name == "w:gridCol")
                    .count()
            })
            .unwrap_or(0)
            < 3
    {
        return false;
    }
    let text = table.text();
    let filtered: String = text
        .lines()
        .filter(|line| !line.starts_with("MTLATEX:"))
        .collect();
    regex::Regex::new(r"^[\s\t\r\n()（）\[\]【】0-9ivxlcdmIVXLCDM.\-–—]*$")
        .expect("valid equation label grammar")
        .is_match(&filtered)
}

/// Resolve the reference document's actual default table style rather than assuming its ID.
fn normal_table_style(styles: &Element) -> String {
    styles
        .elements()
        .find(|style| style.attr("w:type") == Some("table") && style.attr("w:default") == Some("1"))
        .or_else(|| {
            styles.elements().find(|style| {
                style.attr("w:type") == Some("table")
                    && style.child("w:name").and_then(|name| name.attr("w:val"))
                        == Some("Normal Table")
            })
        })
        .and_then(|style| style.attr("w:styleId"))
        .unwrap_or("TableNormal")
        .into()
}

/// Replace table-level margins in the original side order.
fn margins(table: &mut Element, sides: &[(&str, i64)]) {
    let properties = table.word("w:tblPr");
    let margins = properties.ensure("w:tblCellMar");
    for (side, amount) in sides {
        let name = format!("w:{side}");
        margins.remove(&name);
        margins.push(Element::with_attrs(
            &name,
            &[("w:w", &amount.to_string()), ("w:type", "dxa")],
        ));
    }
}

/// Neutralize layout table styling, including nested subfigure tables.
fn format_subfigure(table: &mut Element, style_id: &str) {
    let properties = table.word("w:tblPr");
    properties.remove("w:tblStyle");
    properties.children.insert(
        0,
        Node::Element(Element::with_attrs("w:tblStyle", &[("w:val", style_id)])),
    );
    properties.remove("w:tblLook");
    margins(
        table,
        &[("top", 0), ("left", 0), ("bottom", 0), ("right", 0)],
    );
    for row in table.elements_mut().filter(|row| row.name == "w:tr") {
        for cell in row.elements_mut().filter(|cell| cell.name == "w:tc") {
            for nested in cell.elements_mut().filter(|nested| nested.name == "w:tbl") {
                format_subfigure(nested, style_id);
                autofit(nested, false);
            }
        }
    }
}

/// Neutralize tables immediately before image captions without touching normal tables.
pub(crate) fn clear_subfigures(document: &mut Element, styles: &Element) {
    let style = normal_table_style(styles);
    let caption_id = style_index(styles, "Image Caption")
        .and_then(|index| {
            if let Node::Element(style) = &styles.children[index] {
                style.attr("w:styleId")
            } else {
                None
            }
        })
        .unwrap_or("ImageCaption");
    let Some(body) = document.child_mut("w:body") else {
        return;
    };
    let indices:Vec<_>=body.children.iter().enumerate().filter_map(|(index,node)|if index>0&&matches!(node,Node::Element(paragraph)if paragraph.name=="w:p"&&paragraph_style(paragraph)==Some(caption_id)){Some(index-1)}else{None}).collect();
    for index in indices {
        if let Node::Element(table) = &mut body.children[index]
            && table.name == "w:tbl"
        {
            format_subfigure(table, &style);
        }
    }
}

/// Make the shared Table Text style inherit Normal while preserving an existing definition.
pub(crate) fn ensure_table_text_style(styles: &mut Element) {
    let base = style_index(styles, "Normal")
        .or_else(|| style_index(styles, "正文"))
        .and_then(|index| {
            if let Node::Element(style) = &styles.children[index] {
                style.attr("w:styleId")
            } else {
                None
            }
        })
        .unwrap_or("Normal")
        .to_owned();
    if let Some(style) = get_style_mut(styles, "Table Text") {
        style.word("w:basedOn").set("w:val", &base);
        return;
    }
    let mut style = Element::with_attrs(
        "w:style",
        &[
            ("w:type", "paragraph"),
            ("w:customStyle", "1"),
            ("w:styleId", "TableText"),
        ],
    );
    style.word("w:name").set("w:val", "Table Text");
    style.word("w:basedOn").set("w:val", &base);
    let spacing = style.word("w:pPr").word("w:spacing");
    spacing.set("w:before", "57");
    spacing.set("w:after", "57");
    spacing.set("w:line", "240");
    spacing.set("w:lineRule", "auto");
    set_bool(&mut style, "w:semiHidden", false);
    set_bool(&mut style, "w:qFormat", true);
    style.word("w:uiPriority").set("w:val", "1");
    styles.push(style);
}

/// Convert regular table paragraphs while retaining Compact in equation layout tables.
pub(crate) fn convert_table_text_styles(document: &mut Element, styles: &Element) {
    let style_id = |name: &str, default: &str| {
        style_index(styles, name)
            .and_then(|index| {
                if let Node::Element(style) = &styles.children[index] {
                    style.attr("w:styleId")
                } else {
                    None
                }
            })
            .unwrap_or(default)
            .to_owned()
    };
    let compact = style_id("Compact", "Compact");
    let table_text = style_id("Table Text", "TableText");
    let Some(body) = document.child_mut("w:body") else {
        return;
    };
    for table in body.elements_mut().filter(|table| table.name == "w:tbl") {
        if is_equation(table) {
            continue;
        }
        for row in table.elements_mut().filter(|row| row.name == "w:tr") {
            for cell in row.elements_mut().filter(|cell| cell.name == "w:tc") {
                for paragraph in cell
                    .elements_mut()
                    .filter(|paragraph| paragraph.name == "w:p")
                {
                    if paragraph_style(paragraph) == Some(compact.as_str()) {
                        paragraph
                            .word("w:pPr")
                            .word("w:pStyle")
                            .set("w:val", &table_text);
                    }
                }
            }
        }
    }
}

/// Consume revision markers recursively so math nested in layout cells is also colored.
pub(crate) fn equation_metadata(document: &mut Element) {
    let mut pending = None;
    consume_equation_markers(document, &mut pending);
}

/// Pair each hidden marker with the following paragraph and then remove it.
fn consume_equation_markers(element: &mut Element, pending: &mut Option<Value>) {
    let mut index = 0;
    while index < element.children.len() {
        let mut remove = false;
        if let Node::Element(child) = &mut element.children[index] {
            if child.name == "w:p" {
                let text = child.text();
                if let Some(record) = text.strip_prefix("PMT_EQUATION_METADATA:") {
                    *pending = serde_json::from_str(record).ok();
                    remove = true;
                } else if let Some(record) = pending.take()
                    && record.get("revision").is_some_and(|value| {
                        value.as_bool() == Some(true) || value.as_str() == Some("true")
                    })
                    && (child.contains("m:oMath") || child.contains("m:oMathPara"))
                {
                    child.visit_mut(&mut |run| {
                        if run.name == "m:r" {
                            if run.child("m:rPr").is_none() {
                                run.children.insert(0, Node::Element(Element::new("m:rPr")));
                            }
                            run.ensure("m:rPr")
                                .ensure("w:rPr")
                                .ensure("w:color")
                                .set("w:val", "FF0000");
                        }
                    });
                }
            }
            if !remove {
                consume_equation_markers(child, pending);
            }
        }
        if remove {
            element.children.remove(index);
        } else {
            index += 1;
        }
    }
}

/// Use Word's auto-fit width and optional centering while preserving child cell widths.
fn autofit(table: &mut Element, center: bool) {
    let properties = table.word("w:tblPr");
    properties.word("w:tblLayout").set("w:type", "autofit");
    let width = properties.ensure("w:tblW");
    width.set("w:type", "pct");
    width.set("w:w", "5000");
    if center {
        properties.ensure("w:jc").set("w:val", "center");
    }
}

/// Auto-fit only top-level regular tables, matching the original document traversal.
pub(crate) fn autofit_tables(document: &mut Element) {
    let Some(body) = document.child_mut("w:body") else {
        return;
    };
    for table in body.elements_mut().filter(|table| table.name == "w:tbl") {
        if !is_equation(table) {
            autofit(table, true);
        }
    }
}

/// Color direct cell/caption runs for a table revision without recoloring math or hyperlinks.
fn revision_runs(element: &mut Element) {
    for paragraph in element
        .elements_mut()
        .filter(|element| element.name == "w:p")
    {
        for run in paragraph.elements_mut().filter(|run| run.name == "w:r") {
            run.word("w:rPr").word("w:color").set("w:val", "FF0000");
        }
    }
}

/// Parse positive one-based row or column selection indices.
fn indices(text: &str) -> Result<Vec<usize>> {
    let separator = regex::Regex::new(r"[,，;；\s]+")?;
    let mut values = Vec::new();
    for value in separator
        .split(text.trim())
        .filter(|value| !value.is_empty())
    {
        let number: usize = value.parse()?;
        anyhow::ensure!(
            number > 0,
            "Revision indices are one-based and must be positive"
        );
        values.push(number);
    }
    Ok(values)
}

/// Apply attribute markers to their following table and optional caption in source order.
pub(crate) fn table_metadata(document: &mut Element) -> Result<()> {
    let Some(body) = document.child_mut("w:body") else {
        return Ok(());
    };
    let mut pending = None;
    let mut caption = None;
    let mut pairs = Vec::new();
    let mut markers = Vec::new();
    for (index, node) in body.children.iter().enumerate() {
        let Node::Element(element) = node else {
            continue;
        };
        if element.name == "w:p" {
            if let Some(record) = element.text().strip_prefix("PMT_TABLE_METADATA:") {
                markers.push(index);
                if let Ok(record) = serde_json::from_str::<Value>(record) {
                    pending = Some(record);
                    caption = None;
                }
            } else if pending.is_some()
                && paragraph_style(element).is_some_and(|style| {
                    style.contains("Caption") || style.contains("Table") || style.contains("题注")
                })
            {
                caption = Some(index);
            }
        } else if element.name == "w:tbl"
            && let Some(record) = pending.take()
        {
            pairs.push((index, caption.take(), record));
        }
    }
    for (index, caption, record) in pairs {
        let Some(attributes) = record.get("attributes").and_then(Value::as_object) else {
            continue;
        };
        let mut whole_revision = false;
        let Node::Element(table) = &mut body.children[index] else {
            continue;
        };
        for (key, value) in attributes {
            let key = key.trim().to_lowercase().replace('-', "_");
            let value = value
                .as_str()
                .map(str::to_owned)
                .unwrap_or_else(|| value.to_string());
            let apply = (|| -> Result<()> {
                match key.as_str() {
                    "cell_margin" | "cell_margin_top" | "cell_margin_bottom"
                    | "cell_margin_left" | "cell_margin_right" => {
                        let amount = super::formatting::length_twips_truncated(&Value::String(
                            value.clone(),
                        ))?;
                        if key == "cell_margin" {
                            margins(
                                table,
                                &[
                                    ("top", amount),
                                    ("bottom", amount),
                                    ("left", amount),
                                    ("right", amount),
                                ],
                            );
                        } else {
                            margins(
                                table,
                                &[(key.strip_prefix("cell_margin_").unwrap(), amount)],
                            );
                        }
                    }
                    "row_height" => {
                        let amount =
                            super::formatting::length_twips(&Value::String(value.clone()))?;
                        for row in table.elements_mut().filter(|row| row.name == "w:tr") {
                            row.word("w:trPr").word("w:trHeight").set("w:val", amount);
                        }
                    }
                    "alignment" => {
                        let alignment = if matches!(value.to_lowercase().as_str(), "left" | "right")
                        {
                            value.to_lowercase()
                        } else {
                            "center".into()
                        };
                        table.word("w:tblPr").ensure("w:jc").set("w:val", alignment);
                    }
                    "autofit" => {
                        let width = table.word("w:tblPr").ensure("w:tblW");
                        match value.to_lowercase().as_str() {
                            "window" => {
                                width.set("w:type", "pct");
                                width.set("w:w", "5000");
                            }
                            "content" => {
                                width.set("w:type", "auto");
                                width.set("w:w", "0");
                            }
                            _ => {}
                        }
                    }
                    "revision_rows" | "revision_columns" => {
                        let all = value.trim() == "*";
                        let selected = if all { Vec::new() } else { indices(&value)? };
                        whole_revision |= all;
                        for (row_index, row) in table
                            .elements_mut()
                            .filter(|row| row.name == "w:tr")
                            .enumerate()
                        {
                            let mut column = 1;
                            for cell in row.elements_mut().filter(|cell| cell.name == "w:tc") {
                                let span = cell
                                    .child("w:tcPr")
                                    .and_then(|properties| properties.child("w:gridSpan"))
                                    .and_then(|span| span.attr("w:val"))
                                    .and_then(|value| value.parse::<usize>().ok())
                                    .unwrap_or(1);
                                if all
                                    || key == "revision_rows" && selected.contains(&(row_index + 1))
                                    || key == "revision_columns"
                                        && selected.iter().any(|selected| {
                                            *selected >= column && *selected < column + span
                                        })
                                {
                                    revision_runs(cell);
                                }
                                column += span;
                            }
                        }
                    }
                    _ => {}
                }
                Ok(())
            })();
            if let Err(error) = apply {
                eprintln!("[DOCX] Failed to apply table setting {key}={value}: {error}");
            }
        }
        if whole_revision
            && let Some(caption) = caption
            && let Node::Element(paragraph) = &mut body.children[caption]
        {
            for run in paragraph.elements_mut().filter(|run| run.name == "w:r") {
                run.word("w:rPr").word("w:color").set("w:val", "FF0000");
            }
        }
    }
    for index in markers.into_iter().rev() {
        body.children.remove(index);
    }
    Ok(())
}

/// Hide table and cell borders with complete explicit Word border attributes.
fn hidden_borders(properties: &mut Element, tag: &str, sides: &[&str]) {
    let borders = properties.ensure(tag);
    for side in sides {
        let border = borders.ensure(&format!("w:{side}"));
        border.set("w:val", "nil");
        border.set("w:sz", "0");
        border.set("w:space", "0");
        border.set("w:color", "auto");
    }
}

/// Format structural equation grids while preserving the equation's visible contents.
pub(crate) fn format_equations(document: &mut Element) {
    let Some(body) = document.child_mut("w:body") else {
        return;
    };
    for table in body.elements_mut().filter(|table| table.name == "w:tbl") {
        if !is_equation(table) {
            continue;
        }
        format_equation(table);
    }
}

/// Apply the existing width distribution, border removal, and compact paragraph spacing.
fn format_equation(table: &mut Element) {
    let number = table
        .elements()
        .find(|row| row.name == "w:tr")
        .and_then(|row| row.elements().filter(|cell| cell.name == "w:tc").last())
        .map(Element::text)
        .unwrap_or_default();
    let number = regex::Regex::new(r"\d+")
        .expect("valid number grammar")
        .find(&number)
        .and_then(|value| value.as_str().parse::<usize>().ok());
    let edge = if number.is_some_and(|number| number < 10) {
        340
    } else {
        454
    };
    let widths: Vec<i64> = table
        .child("w:tblGrid")
        .map(|grid| {
            grid.elements()
                .filter(|column| column.name == "w:gridCol")
                .map(|column| {
                    column
                        .attr("w:w")
                        .and_then(|value| value.parse().ok())
                        .unwrap_or(0)
                })
                .collect()
        })
        .unwrap_or_default();
    let count = widths.len();
    if count < 3 {
        return;
    }
    let total = widths.iter().sum::<i64>();
    let total = if total > 0 { total } else { 9360 };
    let remaining = (total - edge * 2).max((count - 2) as i64 * 1440);
    let middle_total = widths[1..count - 1]
        .iter()
        .filter(|width| **width > 0)
        .sum::<i64>();
    let mut middle: Vec<i64> = if middle_total > 0 {
        widths[1..count - 1]
            .iter()
            .map(|width| {
                ((remaining as f64 * *width as f64 / middle_total as f64).round_ties_even() as i64)
                    .max(1440)
            })
            .collect()
    } else {
        vec![remaining / (count - 2) as i64; count - 2]
    };
    if middle.iter().sum::<i64>() > remaining {
        middle = vec![remaining / (count - 2) as i64; count - 2];
    }
    let used = middle.iter().sum::<i64>();
    *middle.last_mut().unwrap() += remaining - used;
    let mut new_widths = vec![edge];
    new_widths.extend(middle);
    new_widths.push(edge);
    let properties = table.word("w:tblPr");
    properties.remove("w:tblStyle");
    properties.remove("w:tblLook");
    margins(
        table,
        &[("top", 0), ("left", 0), ("bottom", 0), ("right", 0)],
    );
    hidden_borders(
        table.word("w:tblPr"),
        "w:tblBorders",
        &["top", "left", "bottom", "right", "insideH", "insideV"],
    );
    for row in table.elements_mut().filter(|row| row.name == "w:tr") {
        for cell in row.elements_mut().filter(|cell| cell.name == "w:tc") {
            hidden_borders(
                cell.word("w:tcPr"),
                "w:tcBorders",
                &["top", "left", "bottom", "right"],
            );
        }
    }
    if let Some(number) = table
        .elements_mut()
        .find(|row| row.name == "w:tr")
        .and_then(|row| row.elements_mut().filter(|cell| cell.name == "w:tc").last())
    {
        let right = number.word("w:tcPr").ensure("w:tcMar").ensure("w:right");
        right.set("w:w", "0");
        right.set("w:type", "dxa");
    }
    table
        .word("w:tblPr")
        .ensure("w:tblLayout")
        .set("w:type", "fixed");
    let grid = table.word("w:tblGrid");
    grid.children.clear();
    for width in &new_widths {
        grid.push(Element::with_attrs(
            "w:gridCol",
            &[("w:w", &width.to_string())],
        ));
    }
    for row in table.elements_mut().filter(|row| row.name == "w:tr") {
        for (index, cell) in row
            .elements_mut()
            .filter(|cell| cell.name == "w:tc")
            .enumerate()
        {
            if let Some(width) = new_widths.get(index) {
                let cell_width = cell.word("w:tcPr").ensure("w:tcW");
                cell_width.set("w:w", width);
                cell_width.set("w:type", "dxa");
            }
            for paragraph in cell
                .elements_mut()
                .filter(|paragraph| paragraph.name == "w:p")
            {
                let properties = paragraph.word("w:pPr");
                properties.remove("w:pStyle");
                let spacing = properties.word("w:spacing");
                spacing.set("w:before", "40");
                spacing.set("w:after", "40");
                spacing.set("w:line", "240");
                spacing.set("w:lineRule", "auto");
                if let Some(indentation) = properties.child_mut("w:ind") {
                    for attr in [
                        "w:firstLine",
                        "w:firstLineChars",
                        "w:hanging",
                        "w:hangingChars",
                    ] {
                        indentation.attrs.remove(attr);
                    }
                    if indentation.attrs.is_empty() {
                        properties.remove("w:ind");
                    }
                }
            }
        }
    }
    if let Some(number) = table
        .elements_mut()
        .find(|row| row.name == "w:tr")
        .and_then(|row| row.elements_mut().filter(|cell| cell.name == "w:tc").last())
    {
        number
            .word("w:tcPr")
            .ensure("w:vAlign")
            .set("w:val", "center");
        for paragraph in number
            .elements_mut()
            .filter(|paragraph| paragraph.name == "w:p")
        {
            paragraph.word("w:pPr").word("w:jc").set("w:val", "right");
        }
    }
}
