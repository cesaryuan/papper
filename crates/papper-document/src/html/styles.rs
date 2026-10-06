//! Public HTML style generation: map semantic/custom targets, then emit their cascade.

mod cascade;
mod css;
mod model;
mod overrides;
mod table;
mod word;

use anyhow::{Context, Result};
use cascade::*;
use css::*;
pub(super) use model::StyleKind;
use model::*;
pub use overrides::normalized_override_css;
use overrides::*;
use serde_json::{Map, Value};
use std::path::Path;
use table::*;
use word::*;

// Custom-style Divs wrap body paragraphs; keep this fallback at the same
// specificity as direct paragraphs so dedicated custom-style rules can win.
const BODY_PARAGRAPH_SELECTOR: &str =
    ".pmt-page > p, .pmt-page > :where(div[data-custom-style]) > p";
// Pandoc's section headings share one page parent. First-of-type only finds
// one paragraph for the entire page, rather than the opening of each section.
// :where keeps authored Body Text/First Paragraph override order effective.
const FIRST_PARAGRAPH_SELECTOR: &str = ".pmt-page > :where(h1:not(.title), h2, h3, h4, h5, h6) + p";

/// Generate typed rules only for custom styles observed in the current document.
pub(super) fn custom_style_css_text(
    xml: &str,
    used: &std::collections::BTreeSet<(StyleKind, String)>,
    records: &[Map<String, Value>],
) -> Result<String> {
    let styles = read_reference_styles_with_overrides(xml, records)?;
    let mut targets = Vec::new();
    for (kind, name) in used {
        let Some(style) = find_style_definition(&styles, *kind, name) else {
            eprintln!("[WARN] Unknown {kind:?} custom style: {name}");
            continue;
        };
        let attribute = format!("[data-custom-style={}]", css_string(name));
        match kind {
            StyleKind::Table => {
                // Table Text remains the cell typography source, including its
                // configured overrides; custom table styles supply geometry.
                targets.push(StyleTarget {
                    id: style.id.clone(),
                    selector: format!("table{attribute}"),
                    mode: CssStyleMode::Table { custom: true },
                });
            }
            StyleKind::Paragraph => {
                let selector = format!(
                    ":is(.pmt-page, body) div{attribute} > p, :is(.pmt-page, body) p{attribute}"
                );
                targets.push(StyleTarget {
                    id: style.id.clone(),
                    selector,
                    mode: CssStyleMode::CustomParagraph,
                });
            }
            StyleKind::Character => {
                targets.push(StyleTarget { id: style.id.clone(), selector: format!(":is(.pmt-page, body) :not(div, p, table){attribute}, :is(.pmt-page, body) :is(td, th){attribute} > p"), mode: CssStyleMode::Character });
            }
        }
    }
    Ok(cascade_style_css(&styles, &targets))
}
/// Generate CSS from reference styles and raw docxStyle metadata.
pub fn build_reference_style_css(styles_path: &Path, docx_style: Option<&Value>) -> Result<String> {
    let xml = std::fs::read_to_string(styles_path)
        .with_context(|| format!("Cannot read {}", styles_path.display()))?;
    build_reference_style_css_text(&xml, docx_style)
}

/// Generate CSS in memory; callers may cache XML bytes after dependency validation.
pub fn build_reference_style_css_text(xml: &str, docx_style: Option<&Value>) -> Result<String> {
    let records = docx_style
        .filter(|value| !value.is_null())
        .map(normalize_docx_styles)
        .transpose()?
        .unwrap_or_default();
    reference_style_css_text(xml, &records)
}

/// Generate product CSS directly from validated configuration, preserving authored override order.
pub fn build_reference_style_css_with_settings(
    styles_path: &Path,
    settings: &papper_core::metadata::PmtSettings,
) -> Result<String> {
    let xml = std::fs::read_to_string(styles_path)
        .with_context(|| format!("Cannot read {}", styles_path.display()))?;
    let records =
        papper_core::style_values::normalize_docx_style_settings(settings)?.unwrap_or_default();
    reference_style_css_text(&xml, &records)
}

/// Combine inherited reference typography with already normalized project overrides.
fn reference_style_css_text(xml: &str, records: &[Map<String, Value>]) -> Result<String> {
    let styles = read_reference_styles(xml)?;
    let mut targets = Vec::new();
    add_reference_target(
        &styles,
        &mut targets,
        "Body Text",
        ".pmt-page",
        CssStyleMode::Typography,
    );
    let names = ["Title", "Subtitle", "Body Text", "First Paragraph"]
        .into_iter()
        .map(str::to_owned)
        .chain((1..=6).map(|level| format!("Heading {level}")));
    for name in names {
        let selector = override_selector(&name).expect("built-in style has a semantic selector");
        add_reference_target(
            &styles,
            &mut targets,
            &name,
            &selector,
            CssStyleMode::Paragraph,
        );
    }
    for (selector, name) in [
        ("table caption", "Table Caption"),
        ("figure figcaption", "Image Caption"),
    ] {
        // A semantic caption selects its leaf style once. Caption is only the
        // fallback if that leaf is missing, or an ancestor when basedOn says so.
        let name = if find_style(&styles, name).is_some() {
            name
        } else {
            "Caption"
        };
        add_reference_target(
            &styles,
            &mut targets,
            name,
            selector,
            CssStyleMode::Paragraph,
        );
        add_reference_target(
            &styles,
            &mut targets,
            name,
            &format!("{selector} p"),
            CssStyleMode::CaptionParagraph,
        );
    }
    add_reference_target(
        &styles,
        &mut targets,
        "Table",
        "table",
        CssStyleMode::Table { custom: false },
    );
    add_reference_target(
        &styles,
        &mut targets,
        "Table Text",
        "table td, table th",
        CssStyleMode::Typography,
    );
    add_reference_target(
        &styles,
        &mut targets,
        "Table Text",
        "table td > p, table th > p",
        CssStyleMode::Paragraph,
    );
    let mut rules = vec![cascade_style_css(&styles, &targets)];
    if let Some(style) = find_style(&styles, "Table Text") {
        rules.push(table_padding_css(
            find_style(&styles, "Table"),
            style,
            "Table Text",
        ));
    }
    rules.extend(override_css(records, find_style(&styles, "Table"))?);
    Ok(rules.join("\n\n"))
}

/// Register a built-in selector using localized lookup while retaining its style id.
fn add_reference_target(
    styles: &ReferenceStyles,
    targets: &mut Vec<StyleTarget>,
    name: &str,
    selector: &str,
    mode: CssStyleMode,
) {
    let kind = if style_key(name) == "table" {
        StyleKind::Table
    } else {
        StyleKind::Paragraph
    };
    if let Some(style) = find_style_definition(styles, kind, name) {
        targets.push(StyleTarget {
            id: style.id.clone(),
            selector: selector.into(),
            mode,
        });
    }
}
#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    /// Keep inherited fonts, explicit false/zero, localized names, and override priority.
    #[test]
    fn localized_style_overrides_follow_reference_inheritance() -> Result<()> {
        let xml = r#"<w:styles xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
          <w:docDefaults><w:pPrDefault><w:pPr><w:spacing w:after="120"/></w:pPr></w:pPrDefault></w:docDefaults>
          <w:style w:type="paragraph" w:styleId="Body"><w:name w:val="正文文本"/><w:rPr><w:rFonts w:ascii="Times New Roman" w:eastAsia="宋体"/><w:sz w:val="24"/><w:b/></w:rPr></w:style>
          <w:style w:type="paragraph" w:styleId="Heading"><w:name w:val="标题 1"/><w:basedOn w:val="Body"/><w:rPr><w:rFonts w:eastAsia="KaiTi"/><w:sz w:val="30"/><w:b w:val="false"/></w:rPr></w:style>
          <w:style w:type="paragraph" w:styleId="ImageCaption"><w:name w:val="Image Caption"/><w:basedOn w:val="Body"/></w:style>
          <w:style w:type="table" w:styleId="Table"><w:name w:val="Table"/></w:style>
        </w:styles>"#;
        let css = build_reference_style_css_text(
            xml,
            Some(&json!({
                "标题 1": {"fontSize": "四号", "paragraphSpacing": {"after": 0}},
                "Image Caption": {"fontSize": "10pt"},
            })),
        )?;
        // Formatting is the contract; explanatory CSS comments may evolve.
        let css = regex::Regex::new(r"(?m)^  /\*[\s\S]*?\*/\n")?.replace_all(&css, "");
        let inherited = "h1 {\n  font-size: 15pt;\n  font-family: \"Times New Roman\", \"KaiTi\", SimSun, serif;\n  font-weight: normal;\n}";
        let override_rule = "h1 {\n  font-size: 14pt;\n  margin-bottom: 0pt;\n}";
        assert!(css.contains(inherited));
        assert!(css.contains(override_rule));
        assert!(css.find(inherited).unwrap() < css.find(override_rule).unwrap());
        assert_eq!(css.matches("margin-bottom: 6pt;").count(), 1);
        assert!(css.contains("figure figcaption,\nfigure figcaption p {\n  font-size: 10pt;\n}"));
        Ok(())
    }
}
