//! Convert CSS declarations and render shared rule groups.

use super::model::*;

/// Quote CSS strings and prevent XML names from terminating the enclosing style element.
pub(super) fn css_string(value: &str) -> String {
    let mut escaped = String::from("\"");
    for character in value.chars() {
        match character {
            '\\' | '"' => {
                escaped.push('\\');
                escaped.push(character);
            }
            '<' | '\n' | '\r' | '\u{c}' | '\0' => {
                escaped.push_str(&format!("\\{:x} ", character as u32))
            }
            _ => escaped.push(character),
        }
    }
    escaped.push('"');
    escaped
}

/// Build a font family with the same East Asian and serif fallbacks as Python.
pub(super) fn font_css(style: &StyleSpec) -> Option<String> {
    let mut families = Vec::new();
    for family in [&style.western_font, &style.chinese_font]
        .into_iter()
        .flatten()
    {
        if !families.contains(family) {
            families.push(family.clone());
        }
    }
    if families.is_empty() {
        return None;
    }
    let mut values: Vec<String> = families.iter().map(|name| css_string(name)).collect();
    if style.chinese_font.is_some() {
        values.push("SimSun".into());
    }
    values.push("serif".into());
    Some(values.join(", "))
}

/// Translate style values before grouping shared typography and paragraph layout.
pub(super) fn style_declarations(
    style: &StyleSpec,
    paragraph_metrics: bool,
) -> Vec<(&'static str, String)> {
    let points = |value: Option<f64>| value.map(|item| format!("{}pt", general(item)));
    let alignment = style.alignment.as_ref().map(|raw| {
        if matches!(raw.as_str(), "both" | "distribute") {
            "justify".into()
        } else {
            raw.clone()
        }
    });
    let values = [
        ("font-size", points(style.font_size)),
        ("font-family", font_css(style)),
        (
            "font-weight",
            style
                .bold
                .map(|item| if item { "bold" } else { "normal" }.into()),
        ),
        (
            "font-style",
            style
                .italic
                .map(|item| if item { "italic" } else { "normal" }.into()),
        ),
        (
            "color",
            style
                .color
                .map(|item| format!("#{:02X}{:02X}{:02X}", item[0], item[1], item[2])),
        ),
        ("line-height", style.line_height.clone()),
        (
            "margin-top",
            if paragraph_metrics {
                points(style.before)
            } else {
                None
            },
        ),
        (
            "margin-bottom",
            if paragraph_metrics {
                points(style.after)
            } else {
                None
            },
        ),
        ("text-align", alignment),
        (
            "text-indent",
            if paragraph_metrics {
                style.first_line.clone()
            } else {
                None
            },
        ),
        (
            "margin-left",
            if paragraph_metrics {
                points(style.left)
            } else {
                None
            },
        ),
        (
            "margin-right",
            if paragraph_metrics {
                points(style.right)
            } else {
                None
            },
        ),
    ];
    values
        .into_iter()
        .filter_map(|(key, value)| value.map(|value| (key, value)))
        .collect()
}

/// One declaration group may serve several selectors without repeating its body.
pub(super) struct CssRule {
    pub(super) selectors: Vec<String>,
    pub(super) declarations: Vec<String>,
    scopes: Vec<String>,
    purpose: Option<&'static str>,
}

impl CssRule {
    /// Construct a rule without comments, allowing equal declaration groups to merge.
    pub(super) fn new(selector: String, declarations: Vec<String>) -> Self {
        Self {
            selectors: vec![selector],
            declarations,
            scopes: Vec::new(),
            purpose: None,
        }
    }

    /// Explain table edge or spacing adaptations that cannot be inferred from XML alone.
    pub(super) fn explain(mut self, purpose: &'static str) -> Self {
        self.purpose = Some(purpose);
        self
    }
}
/// Describe how the same Word style maps to one semantic HTML element.
#[derive(Clone, Copy)]
pub(super) enum CssStyleMode {
    Typography,
    Paragraph,
    CustomParagraph,
    CaptionParagraph,
    ParagraphReset,
    Character,
    Table { custom: bool },
}

impl CssStyleMode {
    /// Describe why a target receives typography, paragraph layout, or table geometry.
    fn description(self) -> &'static str {
        match self {
            Self::Typography => "inherited typography",
            Self::Paragraph => "paragraph or caption container",
            Self::CustomParagraph => "custom paragraph",
            Self::CaptionParagraph => "caption inner paragraph; spacing belongs to its container",
            Self::ParagraphReset => "paragraph spacing and indentation reset",
            Self::Character => "character formatting; paragraph layout is excluded",
            Self::Table { .. } => "table geometry and typography",
        }
    }
}

/// Associate a selector with a style id rather than flattening its basedOn values.
pub(super) struct StyleTarget {
    pub(super) id: String,
    pub(super) selector: String,
    pub(super) mode: CssStyleMode,
}

/// Record the actual source and inheritance independently of generated declarations.
#[derive(Clone)]
pub(super) struct CssSource {
    key: String,
    details: Vec<String>,
}

impl CssSource {
    /// Identify the HTML adaptations that precede Word formatting.
    pub(super) fn html_defaults() -> Self {
        Self {
            key: "html-defaults".into(),
            details: vec![
                "Source: papper HTML adaptation rules".into(),
                "Order: before Word document defaults and named styles".into(),
            ],
        }
    }

    /// Identify Word defaults shared by the dependent paragraph styles.
    pub(super) fn document_defaults() -> Self {
        Self {
            key: "doc-defaults".into(),
            details: vec![
                "Source: reference-doc/word/styles.xml > w:docDefaults".into(),
                "Inheritance: paragraph defaults shared by the targets below".into(),
                "Order: before named Word styles".into(),
            ],
        }
    }

    /// Explain one Word style's identity, parent, and role in the generated cascade.
    pub(super) fn word(styles: &ReferenceStyles, style: &ReferenceStyle) -> Self {
        let (kind, name) = &styles.ids[&style.id];
        let parent = style
            .parent
            .as_ref()
            .map(|id| match styles.ids.get(id) {
                Some((_, name)) => format!("{} (styleId={})", css_string(name), css_string(id)),
                None => format!("missing parent styleId={}", css_string(id)),
            })
            .unwrap_or_else(|| {
                if *kind == StyleKind::Paragraph {
                    "w:docDefaults".into()
                } else {
                    "none; paragraph defaults do not apply".into()
                }
            });
        let mut source = Self { key: format!("style:{}", style.id), details: vec![
            format!("Source: reference-doc/word/styles.xml > w:style[@w:styleId={}]", css_string(&style.id)),
            format!("Word style: {} ({kind:?})", css_string(name)),
            format!("Based on: {parent}"),
            "Properties: only differences from the effective parent; shared by dependent targets".into(),
            "Order: after the parent, before descendant styles and configured overrides".into(),
        ] };
        if let Some(name) = &style.configured_by {
            source.details.insert(1, format!("Configuration: effective docxStyle override for {}; applied before descendants inherit", css_string(name)));
        }
        source
    }

    /// Identify effective configuration values without attributing them to styles.xml.
    pub(super) fn configured(name: &str) -> Self {
        Self {
            key: format!("override:{name}"),
            details: vec![
                format!(
                    "Source: effective docxStyle configuration for {}",
                    css_string(name)
                ),
                "Order: after reference Word styles; authored header CSS follows generated CSS"
                    .into(),
            ],
        }
    }

    /// Explain the two sources combined to compute table cell padding.
    pub(super) fn table_spacing(label: &str) -> Self {
        let mut source = if label.starts_with("docxStyle.") {
            Self::configured("Table Text")
        } else {
            Self { key: "table-spacing".into(), details: vec!["Source: reference-doc/word/styles.xml > Table cell margins and Table Text paragraph spacing".into()] }
        };
        source.details.push(
            "Calculation: top/bottom padding = cell margin + Table Text before/after spacing"
                .into(),
        );
        source.details.push(
            "Override: per-table cell_margin attributes replace the cell margin component".into(),
        );
        source
    }
}

/// Keep shared rules together while registering parents before their descendants.
struct CssLayer {
    source: CssSource,
    rules: Vec<CssRule>,
}

/// Own declaration grouping and all CSS rendering for reference, custom, and override styles.
#[derive(Default)]
pub(super) struct CssWriter {
    layers: Vec<CssLayer>,
}

impl CssWriter {
    /// Register even an empty ancestor so later targets cannot move it behind a child.
    pub(super) fn register(&mut self, source: &CssSource) -> usize {
        self.layers
            .iter()
            .position(|layer| layer.source.key == source.key)
            .unwrap_or_else(|| {
                self.layers.push(CssLayer {
                    source: source.clone(),
                    rules: Vec::new(),
                });
                self.layers.len() - 1
            })
    }

    /// Merge identical declarations from the same source and retain their scope descriptions.
    pub(super) fn rule(&mut self, source: &CssSource, mut rule: CssRule, scope: &str) {
        if rule.declarations.is_empty() {
            return;
        }
        if !scope.is_empty() {
            rule.scopes.push(scope.into());
        }
        let index = self.register(source);
        let rules = &mut self.layers[index].rules;
        if let Some(existing) = rules
            .iter_mut()
            .find(|item| item.declarations == rule.declarations && item.purpose == rule.purpose)
        {
            for selector in rule.selectors {
                if !existing.selectors.contains(&selector) {
                    existing.selectors.push(selector);
                }
            }
            for scope in rule.scopes {
                if !existing.scopes.contains(&scope) {
                    existing.scopes.push(scope);
                }
            }
        } else {
            rules.push(rule);
        }
    }

    /// Separate typography from paragraph layout so caption containers and inner p can share it.
    pub(super) fn style(&mut self, source: &CssSource, target: &StyleTarget, style: &StyleSpec) {
        let metrics = matches!(
            target.mode,
            CssStyleMode::Paragraph | CssStyleMode::CustomParagraph | CssStyleMode::ParagraphReset
        );
        let mut typography = Vec::new();
        let mut layout = Vec::new();
        for (key, value) in style_declarations(style, metrics) {
            if matches!(target.mode, CssStyleMode::Character)
                && matches!(key, "line-height" | "text-align")
            {
                continue;
            }
            let declaration = format!("  {key}: {value};");
            if matches!(
                key,
                "margin-top" | "margin-bottom" | "text-indent" | "margin-left" | "margin-right"
            ) {
                layout.push(declaration);
            } else {
                typography.push(declaration);
            }
        }
        let scope = if target.id.is_empty() {
            target.mode.description().to_owned()
        } else {
            format!(
                "styleId={} ({})",
                css_string(&target.id),
                target.mode.description()
            )
        };
        for declarations in [typography, layout] {
            let mut rule = CssRule::new(target.selector.clone(), declarations);
            if source.key == "html-defaults" || matches!(target.mode, CssStyleMode::ParagraphReset)
            {
                rule = rule.explain("Reset browser paragraph spacing and indentation; caption spacing belongs to the outer caption");
            }
            self.rule(source, rule, &scope);
        }
    }

    /// Emit a configured style using the same escaping, comments, and selector formatter.
    pub(super) fn configured_style(
        selector: &str,
        style: &StyleSpec,
        name: &str,
        metrics: bool,
    ) -> String {
        let mut writer = Self::default();
        let declarations = style_declarations(style, metrics)
            .into_iter()
            .map(|(key, value)| format!("  {key}: {value};"))
            .collect();
        writer.rule(
            &CssSource::configured(name),
            CssRule::new(selector.into(), declarations),
            "configured semantic selector",
        );
        writer.finish()
    }

    /// Render readable selector lists and traceable comments through one output path.
    pub(super) fn finish(self) -> String {
        let mut output = Vec::new();
        for layer in self.layers {
            for rule in layer.rules {
                let mut comments = layer.source.details.clone();
                if let Some(purpose) = rule.purpose {
                    comments.push(format!("Purpose: {purpose}"));
                }
                for scope in &rule.scopes {
                    comments.push(format!("Target: {scope}"));
                }
                let comments = comments
                    .into_iter()
                    .map(|line| format!("   * {}", line.replace("*/", "* /").replace('<', "\\3c ")))
                    .collect::<Vec<_>>()
                    .join("\n");
                output.push(format!(
                    "{} {{\n  /*\n{comments}\n   */\n{}\n}}",
                    format_selectors(&rule.selectors),
                    rule.declarations.join("\n")
                ));
            }
        }
        output.join("\n\n")
    }
}

/// Split only top-level commas, preserving commas and escapes inside selectors or style names.
fn format_selectors(groups: &[String]) -> String {
    let mut selectors = Vec::new();
    for group in groups {
        let mut depth = 0;
        let mut quote = None;
        let mut escaped = false;
        let mut start = 0;
        for (index, character) in group.char_indices() {
            if escaped {
                escaped = false;
                continue;
            }
            if character == '\\' {
                escaped = true;
                continue;
            }
            if let Some(current) = quote {
                if character == current {
                    quote = None;
                }
                continue;
            }
            match character {
                '\'' | '"' => quote = Some(character),
                '(' | '[' => depth += 1,
                ')' | ']' => depth -= 1,
                ',' if depth == 0 => {
                    let selector = group[start..index].trim().to_owned();
                    if !selectors.contains(&selector) {
                        selectors.push(selector);
                    }
                    start = index + 1;
                }
                _ => {}
            }
        }
        let selector = group[start..].trim().to_owned();
        if !selectors.contains(&selector) {
            selectors.push(selector);
        }
    }
    // Standalone heading tags have equal specificity; :is preserves it while
    // reducing a shared six-heading list without weakening h1.title rules.
    let headings: Vec<String> = (1..=6).map(|level| format!("h{level}")).collect();
    if headings.iter().all(|heading| selectors.contains(heading)) {
        let first = selectors
            .iter()
            .position(|selector| headings.contains(selector))
            .unwrap();
        selectors.retain(|selector| !headings.contains(selector));
        selectors.insert(first, format!(":is({})", headings.join(", ")));
    }
    selectors.join(",\n")
}
