//! Project Word ancestry onto shared HTML selectors in cascade order.

use super::css::*;
use super::model::*;
use super::table::*;
use super::word::*;
use std::collections::HashSet;

/// Walk only the selected style's ancestors, stopping missing parents and malformed cycles.
pub(super) fn style_chain<'a>(styles: &'a ReferenceStyles, id: &str) -> Vec<&'a ReferenceStyle> {
    let mut chain = Vec::new();
    let mut visited = HashSet::new();
    let mut current = styles.ids.get(id).and_then(|key| styles.named.get(key));
    while let Some(style) = current {
        if !visited.insert(style.id.as_str()) {
            break;
        }
        chain.push(style);
        current = style
            .parent
            .as_ref()
            .and_then(|parent| styles.ids.get(parent))
            .and_then(|key| styles.named.get(key));
    }
    chain.reverse();
    chain
}

/// Emit shared ancestor selectors followed by property differences on descendant selectors.
pub(super) fn cascade_style_css(styles: &ReferenceStyles, targets: &[StyleTarget]) -> String {
    let mut writer = CssWriter::default();
    let html_defaults = CssSource::html_defaults();
    let document_defaults = CssSource::document_defaults();
    writer.register(&html_defaults);
    writer.register(&document_defaults);
    for target in targets {
        let chain = style_chain(styles, &target.id);
        let Some(root) = chain.first() else { continue };
        let mut previous = if styles.ids[&root.id].0 == StyleKind::Paragraph {
            styles.defaults.clone()
        } else {
            StyleSpec::default()
        };
        if matches!(
            target.mode,
            CssStyleMode::CustomParagraph | CssStyleMode::CaptionParagraph
        ) {
            // Browser/body paragraph rules must not supply spacing or indentation
            // absent from Word, or double the outer caption's paragraph spacing.
            let reset_target = StyleTarget {
                id: target.id.clone(),
                selector: target.selector.clone(),
                mode: CssStyleMode::ParagraphReset,
            };
            writer.style(
                &html_defaults,
                &reset_target,
                &StyleSpec::zero_paragraph_spacing(),
            );
        }
        if let CssStyleMode::Table { custom } = target.mode {
            for rule in table_geometry_rules(
                &target.selector,
                &StyleSpec::default(),
                find_style(styles, "Table Text"),
                custom,
                true,
            ) {
                writer.rule(
                    &html_defaults,
                    rule,
                    &format!("table styleId={}", css_string(&target.id)),
                );
            }
        }
        writer.style(&document_defaults, target, &previous);
        for style in chain {
            let delta = style.effective.difference(&previous);
            let source = CssSource::word(styles, style);
            // Register even an empty parent first: other targets may add its
            // declarations later, and it still must precede every child.
            writer.register(&source);
            writer.style(&source, target, &delta);
            if let CssStyleMode::Table { custom } = target.mode {
                for rule in table_geometry_rules(&target.selector, &delta, None, custom, false) {
                    writer.rule(
                        &source,
                        rule,
                        &format!("table styleId={}", css_string(&target.id)),
                    );
                }
            }
            previous = style.effective.clone();
        }
    }
    writer.finish()
}
