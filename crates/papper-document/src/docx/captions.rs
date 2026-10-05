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
