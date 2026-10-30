//! Keep bilingual caption paragraphs adjacent to their image or table in Word output.

use super::formatting::{paragraph_style, set_bool, style_index};
use super::xml::{Element, Node};

/// Keep the two captions adjacent to each other and their image or table opening.
pub(crate) fn keep_groups(document: &mut Element, styles: &Element) {
    let groups: Vec<_> = ["Image Caption", "Table Caption"]
        .into_iter()
        .filter_map(|primary| {
            let index = style_index(styles, primary)?;
            let Node::Element(style) = &styles.children[index] else {
                return None;
            };
            Some((
                style.attr("w:styleId")?.to_owned(),
                primary == "Table Caption",
            ))
        })
        .collect();
    if groups.is_empty() {
        return;
    }
    document.visit_mut(&mut |parent| {
        for index in 1..parent.children.len() {
            let Node::Element(primary) = &parent.children[index - 1] else {
                continue;
            };
            let primary_style = paragraph_style(primary).map(str::to_owned);
            if primary.name != "w:p" {
                continue;
            }
            let Node::Element(english) = &mut parent.children[index] else {
                continue;
            };
            if english.name != "w:p" {
                continue;
            }
            let Some((_, table)) = groups.iter().find(|(id, _)| {
                paragraph_style(english) == Some(id.as_str())
                    && primary_style.as_deref() == Some(id.as_str())
            }) else {
                continue;
            };
            // Shared styles also occur on single captions; require a translated adjacent line.
            let mut translated = false;
            english.visit(&mut |element| {
                translated |= element.name == "w:lang" && element.attr("w:val") == Some("en");
            });
            if !translated {
                continue;
            }
            set_bool(english.word("w:pPr"), "w:keepLines", true);
            // Tables may span pages; keep only the caption group with the table opening.
            set_bool(english.word("w:pPr"), "w:keepNext", *table);
            if let Node::Element(primary) = &mut parent.children[index - 1]
                && primary.name == "w:p"
            {
                set_bool(primary.word("w:pPr"), "w:keepNext", true);
                set_bool(primary.word("w:pPr"), "w:keepLines", true);
            }
            if !table
                && index >= 2
                && let Node::Element(image) = &mut parent.children[index - 2]
                && image.name == "w:p"
                && image.contains("w:drawing")
            {
                set_bool(image.word("w:pPr"), "w:keepNext", true);
            }
        }
    });
}

/// Clear paired main space-after and the inherited leading gap on English table captions.
pub(crate) fn tighten_english_spacing(document: &mut Element, styles: &Element) -> bool {
    let pairs: Vec<_> = ["Image Caption", "Table Caption"]
        .into_iter()
        .filter_map(|name| {
            let primary_index = style_index(styles, name)?;
            let english_index = style_index(styles, &format!("{name} EN"))?;
            let (Node::Element(primary), Node::Element(english)) = (
                &styles.children[primary_index],
                &styles.children[english_index],
            ) else {
                return None;
            };
            Some((
                primary.attr("w:styleId")?.to_owned(),
                english.attr("w:styleId")?.to_owned(),
                name == "Table Caption",
            ))
        })
        .collect();
    if pairs.is_empty() {
        return false;
    }
    let mut changed = false;
    document.visit_mut(&mut |parent| {
        for index in 1..parent.children.len() {
            let Node::Element(english) = &parent.children[index] else {
                continue;
            };
            if english.name != "w:p" {
                continue;
            }
            let Some((primary_id, _, table)) = pairs
                .iter()
                .find(|(_, english_id, _)| paragraph_style(english) == Some(english_id.as_str()))
            else {
                continue;
            };
            let Node::Element(primary) = &parent.children[index - 1] else {
                continue;
            };
            if primary.name != "w:p" || paragraph_style(primary) != Some(primary_id.as_str()) {
                continue;
            }
            // Both kinds clear main space-after. Table EN styles also inherit
            // the main caption's leading gap, so clear English space-before too.
            for (target, side) in [(index - 1, "after"), (index, "before")] {
                if target == index && !table {
                    continue;
                }
                let Node::Element(paragraph) = &mut parent.children[target] else {
                    continue;
                };
                // Line-based and automatic spacing override point spacing in Word.
                let spacing = paragraph.word("w:pPr").word("w:spacing");
                for suffix in ["", "Lines", "Autospacing"] {
                    spacing.set(&format!("w:{side}{suffix}"), "0");
                }
            }
            changed = true;
        }
    });
    changed
}
