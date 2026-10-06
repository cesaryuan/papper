//! Table cell padding, borders, and first-row conditional geometry.

use super::css::*;
use super::model::*;

/// Combine table cell margins with paragraph spacing as inherited CSS variables.
pub(super) fn table_padding_css(
    table: Option<&StyleSpec>,
    paragraph: &StyleSpec,
    label: &str,
) -> String {
    let default = StyleSpec::default();
    let table = table.unwrap_or(&default);
    let values = [
        ("--pmt-table-cell-margin-top", table.cell_top),
        ("--pmt-table-cell-margin-right", table.cell_right),
        ("--pmt-table-cell-margin-bottom", table.cell_bottom),
        ("--pmt-table-cell-margin-left", table.cell_left),
        ("--pmt-table-text-before", paragraph.before),
        ("--pmt-table-text-after", paragraph.after),
    ];
    let variables = values
        .into_iter()
        .map(|(key, value)| format!("  {key}: {}pt;", general(value.unwrap_or(0.0))))
        .collect();
    let mut writer = CssWriter::default();
    let source = CssSource::table_spacing(label);
    writer.rule(
        &source,
        CssRule::new("table".into(), variables),
        "table padding variables",
    );
    writer.rule(
        &source,
        cell_padding_rule("table td, table th".into(), 0.0, 0.0),
        "table cells",
    );
    writer.finish()
}

/// Reuse the same padding formula for default tables and custom direct-cell rules.
fn cell_padding_rule(cells: String, before: f64, after: f64) -> CssRule {
    CssRule::new(cells, vec![
        format!("  padding-top: calc(var(--pmt-table-cell-margin-top, 0pt) + var(--pmt-table-text-before, {}pt));", general(before)),
        "  padding-right: var(--pmt-table-cell-margin-right, 0pt);".into(),
        format!("  padding-bottom: calc(var(--pmt-table-cell-margin-bottom, 0pt) + var(--pmt-table-text-after, {}pt));", general(after)),
        "  padding-left: var(--pmt-table-cell-margin-left, 0pt);".into(),
    ]).explain("Add cell margins to Table Text paragraph spacing; inherited variables preserve configured and per-table overrides")
}
/// Render table geometry on direct cells, resetting HTML defaults only at the root.
pub(super) fn table_geometry_rules(
    selector: &str,
    style: &StyleSpec,
    table_text: Option<&StyleSpec>,
    custom: bool,
    reset: bool,
) -> Vec<CssRule> {
    // Default Table geometry must not match an independently selected custom
    // Word style. Its first-row selectors outrank a custom border reset, so a
    // TableNoBorder header otherwise retains the default separator. :where
    // keeps this exclusion from increasing the default rule's specificity.
    let default_selector = format!("{selector}:where(:not([data-custom-style]))");
    let selector = if custom { selector } else { &default_selector };
    let groups = format!("{selector} > :is(thead, tbody, tfoot)");
    let cells = format!("{groups} > tr > :is(td, th)");
    // A borderless Word style often omits tblBorders altogether. Reset the
    // template's three-line borders instead of inheriting those HTML defaults.
    let mut declarations = if reset {
        vec!["  border: none;".to_owned()]
    } else {
        Vec::new()
    };
    for (side, border) in ["top", "right", "bottom", "left"]
        .into_iter()
        .zip(&style.borders)
    {
        if let Some(border) = border {
            declarations.push(format!("  border-{side}: {border};"));
        }
    }
    if custom {
        for (side, value) in [
            ("top", style.cell_top),
            ("right", style.cell_right),
            ("bottom", style.cell_bottom),
            ("left", style.cell_left),
        ] {
            if !reset && value.is_none() {
                continue;
            }
            declarations.push(format!(
                "  --pmt-table-cell-margin-{side}: {}pt;",
                general(value.unwrap_or(0.0))
            ));
        }
    }
    let mut rules = Vec::new();
    if !declarations.is_empty() {
        let mut rule = CssRule::new(selector.into(), declarations);
        if reset {
            rule = rule.explain("Reset HTML template borders before applying Word table geometry");
        }
        rules.push(rule);
    }
    if reset {
        rules.push(
            CssRule::new(format!("{groups}, {cells}"), vec!["  border: none;".into()])
                .explain("Reset HTML template borders before applying Word table geometry"),
        );
    }
    // Draw inner edges once, including the boundary between separate row groups.
    if let Some(border) = &style.borders[4] {
        rules.push(CssRule::new(format!("{groups} > tr + tr > :is(td, th), {selector} > :is(thead, tbody, tfoot) ~ :is(thead, tbody, tfoot) > tr:first-child > :is(td, th)"), vec![format!("  border-top: {border};")]));
    }
    if let Some(border) = &style.borders[5] {
        rules.push(CssRule::new(
            format!("{cells} + :is(td, th)"),
            vec![format!("  border-left: {border};")],
        ));
    }
    let header = format!("{selector} > thead > tr:first-child > :is(td, th)");
    let mut header_declarations = Vec::new();
    for (side, border) in ["top", "right", "bottom", "left"]
        .into_iter()
        .zip(&style.header_borders)
    {
        if let Some(border) = border {
            header_declarations.push(format!("  border-{side}: {border};"));
        }
    }
    if !header_declarations.is_empty() {
        rules.push(CssRule::new(header, header_declarations).explain(
            "Word tblStylePr[type=firstRow]/tcPr/tcBorders; apply to the first HTML header row",
        ));
        if let Some(border) = &style.header_borders[0] {
            // The table and its header share the outer collapsed top edge.
            rules.push(CssRule::new(
                format!("{selector}:has(> thead > tr)"),
                vec![format!("  border-top: {border};")],
            ).explain("The table and header share the collapsed outer top edge; keep both sides consistent"));
        }
        if let Some(border) = &style.header_borders[2] {
            // Both sides share one collapsed edge. The body insideH border
            // must agree, otherwise its width can defeat the header override.
            rules.push(CssRule::new(
                format!("{selector} > thead + :is(tbody, tfoot) > tr:first-child > :is(td, th)"),
                vec![format!("  border-top: {border};")],
            ).explain("Match the body top edge to the header bottom so a competing insideH border cannot defeat the header"));
        }
    }
    if custom && reset {
        let before = table_text.and_then(|style| style.before).unwrap_or(0.0);
        let after = table_text.and_then(|style| style.after).unwrap_or(0.0);
        rules.push(cell_padding_rule(cells, before, after));
    }
    rules
}
