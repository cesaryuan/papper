//! Finalize retained Lua native-reference markers into real Word heading numbering.

use super::formatting::{paragraph_style, style_index};
use super::package::Package;
use super::xml::{Element, Node};
use anyhow::{Result, bail};
use serde_json::Value;
use std::collections::{BTreeMap, BTreeSet};
use std::time::{SystemTime, UNIX_EPOCH};

/// Consume heading records and preserve formatted title runs in the remaining paragraph.
fn collect(document: &mut Element) -> Result<(Vec<Value>, BTreeMap<usize, Value>)> {
    let mut records = Vec::new();
    let mut levels = BTreeMap::new();
    let mut error = None;
    document.visit_mut(&mut |paragraph| {
        if paragraph.name == "w:p" {
            let mut found = None;
            paragraph.children.retain(|node| {
                let Node::Element(run) = node else {
                    return true;
                };
                if run.name != "w:r" {
                    return true;
                }
                let text = run.text();
                let Some(raw) = text.strip_prefix("PMT_NATIVE_HEADING:") else {
                    return true;
                };
                match serde_json::from_str::<Value>(raw) {
                    Ok(record) => {
                        found = Some(record);
                    }
                    Err(reason) => {
                        error = Some(anyhow::anyhow!("Invalid native heading marker: {reason}"));
                    }
                }
                false
            });
            if let Some(record) = found {
                let level = record.get("level").and_then(Value::as_u64).unwrap_or(0) as usize;
                if !(1..=9).contains(&level) {
                    error = Some(anyhow::anyhow!(
                        "Native DOCX heading level must be between 1 and 9: {level}"
                    ));
                    return;
                }
                if let Some(previous) = levels.get(&level) {
                    let previous: &Value = previous;
                    if previous.get("pattern") != record.get("pattern") {
                        error = Some(anyhow::anyhow!(
                            "Native DOCX heading level {level} has inconsistent numbering templates"
                        ));
                        return;
                    }
                }
                levels.entry(level).or_insert_with(|| record.clone());
                paragraph.set("_papper_native_level", level);
                records.push(record);
            }
        }
    });
    if let Some(error) = error {
        return Err(error);
    }
    Ok((records, levels))
}

/// Append a dedicated outline numbering definition without changing ordinary list IDs.
fn numbering(
    package: &mut Package,
    styles: &mut Element,
    records: &[Value],
    levels: &BTreeMap<usize, Value>,
) -> Result<(i64, BTreeMap<String, usize>)> {
    let mut numbering = package.xml("word/numbering.xml")?;
    let abstract_id = numbering
        .elements()
        .filter(|element| element.name == "w:abstractNum")
        .filter_map(|element| {
            element
                .attr("w:abstractNumId")
                .and_then(|raw| raw.parse::<i64>().ok())
        })
        .max()
        .unwrap_or(-1)
        + 1;
    let number_id = numbering
        .elements()
        .filter(|element| element.name == "w:num")
        .filter_map(|element| {
            element
                .attr("w:numId")
                .and_then(|raw| raw.parse::<i64>().ok())
        })
        .max()
        .unwrap_or(0)
        + 1;
    let depth = records
        .iter()
        .filter_map(|record| record.get("depth").and_then(Value::as_u64))
        .max()
        .unwrap_or(9) as usize;
    let mut outline = Element::with_attrs(
        "w:abstractNum",
        &[("w:abstractNumId", &abstract_id.to_string())],
    );
    outline.push(Element::with_attrs(
        "w:multiLevelType",
        &[("w:val", "multilevel")],
    ));
    outline.push(Element::with_attrs(
        "w:name",
        &[("w:val", "Papper native headings")],
    ));
    let mut style_levels = BTreeMap::new();
    for level in 1..=9 {
        let record = levels.get(&level);
        let index = style_index(styles, &format!("Heading {level}"))
            .ok_or_else(|| anyhow::anyhow!("Reference DOCX is missing Heading {level}"))?;
        let Node::Element(style) = &mut styles.children[index] else {
            unreachable!()
        };
        let identifier = style.attr("w:styleId").unwrap_or_default().to_owned();
        style_levels.insert(identifier.clone(), level);
        let mut definition = Element::with_attrs("w:lvl", &[("w:ilvl", &(level - 1).to_string())]);
        definition.push(Element::with_attrs(
            "w:start",
            &[(
                "w:val",
                &record
                    .and_then(|record| record.get("start"))
                    .map(|value| value.to_string())
                    .unwrap_or_else(|| "1".into()),
            )],
        ));
        definition.push(Element::with_attrs("w:numFmt", &[("w:val", "decimal")]));
        if level <= depth {
            definition.push(Element::with_attrs("w:pStyle", &[("w:val", &identifier)]));
        }
        definition.push(Element::with_attrs(
            "w:suff",
            &[(
                "w:val",
                record
                    .and_then(|record| record.get("suffix"))
                    .and_then(Value::as_str)
                    .unwrap_or("space"),
            )],
        ));
        let default = (1..=level)
            .map(|index| format!("%{index}"))
            .collect::<Vec<_>>()
            .join(".");
        definition.push(Element::with_attrs(
            "w:lvlText",
            &[(
                "w:val",
                record
                    .and_then(|record| record.get("pattern"))
                    .and_then(Value::as_str)
                    .unwrap_or(&default),
            )],
        ));
        definition.push(Element::with_attrs("w:lvlJc", &[("w:val", "left")]));
        outline.push(definition);
        if level <= depth {
            let numbering = style.word("w:pPr").word("w:numPr");
            numbering.word("w:ilvl").set("w:val", level - 1);
            numbering.word("w:numId").set("w:val", number_id);
        }
    }
    let index = numbering
        .children
        .iter()
        .position(|node| matches!(node,Node::Element(element)if element.name=="w:num"))
        .unwrap_or(numbering.children.len());
    numbering.children.insert(index, Node::Element(outline));
    let mut instance = Element::with_attrs("w:num", &[("w:numId", &number_id.to_string())]);
    instance.push(Element::with_attrs(
        "w:abstractNumId",
        &[("w:val", &abstract_id.to_string())],
    ));
    numbering.push(instance);
    package.set_xml("word/numbering.xml", &numbering);
    Ok((number_id, style_levels))
}

/// Bind marked headings while explicitly disabling inherited numbering on unmarked headings.
fn bind(document: &mut Element, number_id: i64, styles: &BTreeMap<String, usize>) {
    document.visit_mut(&mut |paragraph| {
        if paragraph.name == "w:p" {
            let level = paragraph
                .attrs
                .remove("_papper_native_level")
                .and_then(|value| value.parse::<usize>().ok());
            if level.is_none()
                && !paragraph_style(paragraph).is_some_and(|style| styles.contains_key(style))
            {
                return;
            }
            let properties = paragraph.word("w:pPr");
            properties
                .word("w:numPr")
                .word("w:numId")
                .set("w:val", if level.is_some() { number_id } else { 0 });
            if let Some(level) = level {
                properties
                    .word("w:numPr")
                    .word("w:ilvl")
                    .set("w:val", level - 1);
                properties.ensure("w:outlineLvl").set("w:val", level - 1);
            }
        }
    });
}

/// Allocate per-build IDs outside the old set and pair every bookmark start/end in a part.
fn randomize(root: &mut Element, used: &mut BTreeSet<String>, state: &mut u64) {
    let mut replacements = BTreeMap::new();
    root.visit(&mut |element| {
        if element.name == "w:bookmarkStart"
            && let Some(old) = element.attr("w:id")
            && !replacements.contains_key(old)
        {
            loop {
                *state ^= *state << 13;
                *state ^= *state >> 7;
                *state ^= *state << 17;
                let candidate = ((*state % 2_147_483_647) + 1).to_string();
                if used.insert(candidate.clone()) {
                    replacements.insert(old.to_owned(), candidate);
                    break;
                }
            }
        }
    });
    root.visit_mut(&mut |element| {
        if (element.name == "w:bookmarkStart" || element.name == "w:bookmarkEnd")
            && let Some(replacement) = element
                .attr("w:id")
                .and_then(|old| replacements.get(old))
                .cloned()
        {
            element.set("w:id", replacement);
        }
    });
}

/// Preserve native field/bookmark targets and finalize all XML parts' paired IDs.
pub(crate) fn finalize(
    package: &mut Package,
    document: &mut Element,
    styles: &mut Element,
) -> Result<()> {
    let (records, levels) = collect(document)?;
    if !records.is_empty() {
        let (number_id, style_levels) = numbering(package, styles, &records, &levels)?;
        bind(document, number_id, &style_levels);
    } else if !levels.is_empty() {
        bail!("Native heading records were lost during finalization");
    }
    let mut used = BTreeSet::new();
    document.visit(&mut |element| {
        if (element.name == "w:bookmarkStart" || element.name == "w:bookmarkEnd")
            && let Some(value) = element.attr("w:id")
        {
            used.insert(value.to_owned());
        }
    });
    let names: Vec<_> = package
        .entries
        .keys()
        .filter(|name| {
            name.starts_with("word/")
                && name.ends_with(".xml")
                && name.as_str() != "word/document.xml"
                && name.as_str() != "word/styles.xml"
        })
        .cloned()
        .collect();
    let mut parts = Vec::new();
    for name in names {
        let root = package.xml(&name)?;
        if root.contains("w:bookmarkStart") || root.contains("w:bookmarkEnd") {
            root.visit(&mut |element| {
                if (element.name == "w:bookmarkStart" || element.name == "w:bookmarkEnd")
                    && let Some(value) = element.attr("w:id")
                {
                    used.insert(value.to_owned());
                }
            });
            parts.push((name, root));
        }
    }
    let mut state = SystemTime::now().duration_since(UNIX_EPOCH)?.as_nanos() as u64
        ^ u64::from(std::process::id());
    state = state.max(1);
    randomize(document, &mut used, &mut state);
    for (name, mut root) in parts {
        randomize(&mut root, &mut used, &mut state);
        package.set_xml(&name, &root);
    }
    Ok(())
}
