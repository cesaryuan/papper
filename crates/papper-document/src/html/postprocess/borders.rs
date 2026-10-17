//! Draw effective Word paragraph borders and join adjacent matching paragraph sets.

use super::HtmlNode;
use crate::html::styles::ParagraphBorderStyles;
use anyhow::Result;
use papper_core::paragraph_borders::{OUTER_SIDES, ParagraphBorders};

/// Locate a paragraph after flattening transparent Divs, retaining hard layout boundaries.
struct Paragraph {
    path: Vec<usize>,
    borders: ParagraphBorders,
    metrics: [f64; 4],
}

/// Append generated declarations after authored/local paragraph formatting.
fn append_css(node: &mut HtmlNode, css: &str) {
    if let HtmlNode::Element { attributes, .. } = node {
        if let Some((_, style)) = attributes.iter_mut().find(|(key, _)| key == "style") {
            style.push_str("; ");
            style.push_str(css);
        } else {
            attributes.push(("style".into(), css.into()));
        }
    }
}

/// Record prose in rendered order; lists, table cells and figures own independent border groups.
fn collect(
    node: &mut HtmlNode,
    styles: Option<&ParagraphBorderStyles>,
    scope: &str,
    path: &mut Vec<usize>,
    paragraphs: &mut Vec<Option<Paragraph>>,
) -> Result<()> {
    let own = node.attribute("data-custom-style").map(str::to_owned);
    let is_title = node.has_class("title");
    let is_subtitle = node.has_class("subtitle");
    let footnotes = node.attribute("id") == Some("footnotes");
    let Some(name) = (match node {
        HtmlNode::Element { name, .. } => Some(name.clone()),
        _ => None,
    }) else {
        return Ok(());
    };
    if super::is_raw(&name) || name == "head" {
        return Ok(());
    }
    let boundary = matches!(
        name.as_str(),
        "table" | "td" | "th" | "li" | "blockquote" | "figure" | "pre" | "hr"
    ) || footnotes;
    if boundary {
        paragraphs.push(None);
    }
    let base = if footnotes {
        "Footnote Text".to_owned()
    } else if name == "div" && own.is_some() {
        own.clone().unwrap()
    } else {
        match name.as_str() {
            "td" | "th" => "Table Text".into(),
            "blockquote" => "Block Text".into(),
            "li" if scope != "Footnote Text" => "Compact".into(),
            "figcaption" => "Image Caption".into(),
            "caption" => "Table Caption".into(),
            _ => scope.to_owned(),
        }
    };
    let has_inner_paragraph = matches!(node, HtmlNode::Element { children, .. } if children.iter().any(|child| matches!(child, HtmlNode::Element { name, .. } if name == "p")));
    let paragraph = name == "p"
        || matches!(name.as_str(), "h1" | "h2" | "h3" | "h4" | "h5" | "h6")
        || (matches!(name.as_str(), "caption" | "figcaption") && !has_inner_paragraph);
    if paragraph {
        let semantic = if is_title {
            "Title".into()
        } else if is_subtitle {
            "Subtitle".into()
        } else if let Some(level) = name.strip_prefix('h').filter(|level| level.len() == 1) {
            format!("Heading {level}")
        } else {
            base.clone()
        };
        let selected = own.as_deref().unwrap_or(&semantic);
        let mut borders = styles
            .map(|styles| styles.get(selected))
            .unwrap_or_default();
        if let Some(raw) = node.attribute("data-papper-local-borders") {
            borders.overlay(&serde_json::from_str(raw)?);
        }
        if let HtmlNode::Element { attributes, .. } = node {
            attributes.retain(|(name, _)| name != "data-papper-local-borders");
        }
        let mut metrics = styles
            .map(|styles| styles.metrics(selected))
            .unwrap_or([0.0; 4]);
        for declaration in node.attribute("style").unwrap_or_default().split(';') {
            if let Some((property, value)) = declaration.split_once(':')
                && let Some(index) = ["margin-left", "margin-right", "margin-top", "margin-bottom"]
                    .iter()
                    .position(|key| *key == property.trim())
                && let Some(value) = value
                    .trim()
                    .strip_suffix("pt")
                    .and_then(|value| value.parse().ok())
            {
                metrics[index] = value;
            }
        }
        paragraphs.push(Some(Paragraph {
            path: path.clone(),
            borders,
            metrics,
        }));
    } else if let HtmlNode::Element { children, .. } = node {
        let mut after_heading = false;
        for (index, child) in children.iter_mut().enumerate() {
            let first_paragraph = after_heading
                && base == "Body Text"
                && matches!(child, HtmlNode::Element { name, .. } if name == "p");
            path.push(index);
            collect(
                child,
                styles,
                if first_paragraph {
                    "First Paragraph"
                } else {
                    &base
                },
                path,
                paragraphs,
            )?;
            path.pop();
            if let HtmlNode::Element { name, .. } = child {
                // Match the existing HTML semantic selector for the first paragraph
                // after a section heading, including an explicit border reset there.
                after_heading = matches!(name.as_str(), "h1" | "h2" | "h3" | "h4" | "h5" | "h6")
                    && !child.has_class("title");
            }
        }
    }
    if boundary {
        paragraphs.push(None);
    }
    Ok(())
}

/// Find an existing DOM element without wrapping or moving its authored contents.
fn at_path<'a>(mut node: &'a mut HtmlNode, path: &[usize]) -> &'a mut HtmlNode {
    for &index in path {
        let HtmlNode::Element { children, .. } = node else {
            unreachable!("collected element path");
        };
        node = &mut children[index];
    }
    node
}

/// Compare full border sets, rather than merging unrelated paragraphs with one equal edge.
fn matches(left: Option<&Paragraph>, right: Option<&Paragraph>) -> bool {
    left.zip(right).is_some_and(|(left, right)| {
        left.borders == right.borders && left.metrics[..2] == right.metrics[..2]
    })
}

/// Render connected outer edges and one between border, keeping per-paragraph scope overrides.
pub(super) fn apply_paragraph_borders(
    document: &mut HtmlNode,
    styles: Option<&ParagraphBorderStyles>,
) -> Result<()> {
    let mut paragraphs = Vec::new();
    collect(
        document,
        styles,
        "Body Text",
        &mut Vec::new(),
        &mut paragraphs,
    )?;
    for (index, paragraph) in paragraphs.iter().enumerate() {
        let Some(paragraph) = paragraph else {
            continue;
        };
        if paragraph.borders.lines.is_empty() && paragraph.borders.spaces.is_empty() {
            continue;
        }
        let previous = index
            .checked_sub(1)
            .and_then(|index| paragraphs[index].as_ref());
        let next = paragraphs.get(index + 1).and_then(Option::as_ref);
        let before = matches(previous, Some(paragraph)) && paragraph.borders.visible();
        let after = matches(Some(paragraph), next) && paragraph.borders.visible();
        let mut css = String::new();
        for side in OUTER_SIDES {
            let line = if side == "top" && before {
                None
            } else if side == "bottom" && after {
                paragraph.borders.lines.get("between")
            } else {
                paragraph.borders.lines.get(side)
            };
            let suppressed =
                (side == "top" && before) || (side == "bottom" && after && line.is_none());
            if suppressed {
                css.push_str(&format!("border-{side}: none; padding-{side}: 0pt; "));
            } else if let Some(line) = line {
                css.push_str(&format!("border-{side}: {}; ", line.css()));
                let distance = if matches!(line.style.as_str(), "none" | "nil")
                    || (side == "bottom" && after)
                {
                    0
                } else {
                    paragraph.borders.spaces.get(side).copied().unwrap_or(0)
                };
                css.push_str(&format!("padding-{side}: {distance}pt; "));
            }
        }
        // Connected vertical edges cannot span a CSS paragraph margin gap.
        if before {
            css.push_str("margin-top: 0pt; ");
        }
        if after {
            css.push_str("margin-bottom: 0pt; ");
            // Move the collapsed paragraph gap inside the connected side rules.
            let gap = paragraph.metrics[3].max(next.map(|next| next.metrics[2]).unwrap_or(0.0));
            css.push_str(&format!("padding-bottom: {gap}pt; "));
        }
        append_css(at_path(document, &paragraph.path), &css);
    }
    Ok(())
}
