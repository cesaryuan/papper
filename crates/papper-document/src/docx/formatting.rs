//! Apply metadata-driven Word styles, section settings, and equation paragraphs.

use super::xml::{Element, Node};
use anyhow::{Result, bail};
use papper_core::metadata::{LengthInput, LineNumberMode, PmtSettings};
use papper_core::style_values::{
    FirstLineIndents, LayoutLength, LineSpacingAmount, parse_alignment, parse_font_color,
    parse_font_family, parse_font_size_points, parse_length, parse_line_spacing, parse_number,
};
use serde_json::{Map, Value};
use std::borrow::Cow;
use std::collections::BTreeSet;

/// Convert shared Word lengths through integer EMUs before rounding to twips.
pub(crate) fn length_twips(value: &Value) -> Result<i64> {
    Ok((length_emu(value)? as f64 / 635.0).round_ties_even() as i64)
}

/// Preserve the table-attribute pipeline's intentional truncation of point margins.
pub(crate) fn length_twips_truncated(value: &Value) -> Result<i64> {
    Ok(length_emu(value)? / 635)
}

/// Quantize common units using the existing DOCX integer-EMU precision.
fn length_emu(value: &Value) -> Result<i64> {
    let raw = value
        .as_str()
        .map(str::to_owned)
        .unwrap_or_else(|| value.to_string());
    length_emu_text(&raw)
}

/// Parse physical units identically for typed settings and arbitrary document attributes.
fn length_emu_text(raw: &str) -> Result<i64> {
    static PATTERN: std::sync::OnceLock<regex::Regex> = std::sync::OnceLock::new();
    let pattern = PATTERN.get_or_init(|| {
        regex::Regex::new(r"^(-?\d+(?:\.\d+)?)\s*(pt|磅|cm|厘米|mm|毫米|in|inch|inches|英寸)?$")
            .expect("valid Word length grammar")
    });
    let cleaned = raw.trim().to_lowercase();
    let captures = pattern.captures(&cleaned).ok_or_else(|| {
        anyhow::anyhow!("Expected a Word length such as 12pt, 0.5cm, or 1in: {raw}")
    })?;
    let amount: f64 = captures[1].parse()?;
    let factor = match captures.get(2).map(|value| value.as_str()).unwrap_or("pt") {
        "pt" | "磅" => 12700.0,
        "cm" | "厘米" => 360000.0,
        "mm" | "毫米" => 36000.0,
        _ => 914400.0,
    };
    Ok((amount * factor) as i64)
}

/// Convert a configured length without recreating the generic settings JSON dictionary.
pub(crate) fn configured_length_twips(value: &LengthInput) -> Result<i64> {
    let raw: Cow<'_, str> = match value {
        LengthInput::Text(raw) => Cow::Borrowed(raw),
        LengthInput::Number(raw) => Cow::Owned(raw.to_string()),
    };
    Ok((length_emu_text(&raw)? as f64 / 635.0).round_ties_even() as i64)
}

/// Resolve a style by exact/localized display name before considering its ID.
pub(crate) fn style_index(styles: &Element, name: &str) -> Option<usize> {
    let compact: String = name.split_whitespace().collect();
    let alias = match compact.as_str() {
        "正文" => "Normal",
        "正文文本" => "Body Text",
        "标题" => "Title",
        "副标题" => "Subtitle",
        "题注" => "Caption",
        "页眉" => "Header",
        "页脚" => "Footer",
        "脚注文本" => "Footnote Text",
        "脚注引用" => "Footnote Reference",
        "尾注文本" => "Endnote Text",
        "尾注引用" => "Endnote Reference",
        "引用" => "Quote",
        "明显引用" => "Intense Quote",
        "列表段落" => "List Paragraph",
        "无间隔" => "No Spacing",
        _ => "",
    };
    let numbered = compact
        .strip_prefix("标题")
        .map(|number| format!("Heading {number}"))
        .or_else(|| {
            compact
                .strip_prefix("目录")
                .map(|number| format!("TOC {number}"))
        });
    let candidates = [
        name.to_owned(),
        alias.to_owned(),
        numbered.unwrap_or_default(),
    ];
    for candidate in &candidates {
        if candidate.is_empty() {
            continue;
        }
        if let Some(index) = styles.children.iter().position(|node| matches!(node, Node::Element(style) if style.name == "w:style" && style.child("w:name").and_then(|name|name.attr("w:val")).is_some_and(|actual| actual == candidate || actual == candidate.to_lowercase()))) { return Some(index); }
    }
    styles.children.iter().position(|node| matches!(node, Node::Element(style) if style.name == "w:style" && style.attr("w:styleId") == Some(name)))
}

/// Expose the style tree selected by the existing style lookup semantics.
pub(crate) fn get_style_mut<'a>(styles: &'a mut Element, name: &str) -> Option<&'a mut Element> {
    let index = style_index(styles, name)?;
    if let Node::Element(style) = &mut styles.children[index] {
        Some(style)
    } else {
        None
    }
}

/// Read a paragraph's explicitly selected Word style ID.
pub(crate) fn paragraph_style(paragraph: &Element) -> Option<&str> {
    paragraph
        .child("w:pPr")
        .and_then(|properties| properties.child("w:pStyle"))
        .and_then(|style| style.attr("w:val"))
}

/// Set Word boolean properties using omitted true and explicit false values.
pub(crate) fn set_bool(parent: &mut Element, name: &str, value: bool) {
    // Word style visibility uses absence for false, unlike run bold/italic.
    if name == "w:semiHidden" && !value {
        parent.remove(name);
        return;
    }
    let property = parent.word(name);
    if value {
        property.attrs.remove("w:val");
    } else {
        property.set("w:val", "0");
    }
}

/// Read a canonical property while treating null as an unspecified override.
fn field<'a>(mapping: &'a Map<String, Value>, name: &str) -> Option<&'a Value> {
    mapping.get(name).filter(|value| !value.is_null())
}

/// Convert EMUs to Word twips without repeating the domain unit parser.
fn layout_twips(length: LayoutLength) -> i64 {
    (length.0 as f64 / 635.0).round_ties_even() as i64
}

/// Apply canonical text properties to named Word styles, retaining unspecified formatting.
pub(crate) fn apply_styles(styles: &mut Element, settings: &PmtSettings) -> Result<()> {
    let Some(configured) = &settings.fields().docx_style else {
        return Ok(());
    };
    for (name, raw) in configured.iter() {
        let Some(style) = get_style_mut(styles, name) else {
            eprintln!("[DOCX] Style '{name}' was not found, skipping");
            continue;
        };
        let prefix = format!("docxStyle.{name}");
        if ["fontSize", "fontFamily", "bold", "fontColor"]
            .iter()
            .any(|key| field(raw, key).is_some())
        {
            apply_text_properties(style.word("w:rPr"), raw, &prefix)?;
        }
        // Inline character styles must never acquire paragraph properties.
        if style.attr("w:type") != Some("character") {
            apply_paragraph_properties(style.word("w:pPr"), raw, &prefix)?;
        }
    }
    super::borders::apply_styles(styles, settings)?;
    Ok(())
}

/// Apply shared text overrides to a Word style or a directly formatted run.
pub(super) fn apply_text_properties(
    properties: &mut Element,
    raw: &Map<String, Value>,
    prefix: &str,
) -> Result<()> {
    if let Some(value) = field(raw, "fontSize") {
        let points = parse_font_size_points(value, &format!("{prefix}.fontSize"))?;
        // Preserve Word's established twip-rounding followed by half-point truncation.
        let half_points = layout_twips(LayoutLength::from_unit(points, "pt")) / 10;
        if half_points <= 0 {
            bail!("{prefix}.fontSize must be greater than 0");
        }
        properties.word("w:sz").set("w:val", half_points);
    }
    if let Some(value) = field(raw, "fontFamily") {
        let families = parse_font_family(value, &format!("{prefix}.fontFamily"))?;
        let fonts = properties.word("w:rFonts");
        if let Some(family) = families.get("western") {
            fonts.set("w:ascii", family);
            fonts.set("w:hAnsi", family);
            fonts.attrs.remove("w:asciiTheme");
            fonts.attrs.remove("w:hAnsiTheme");
        }
        if let Some(family) = families.get("chinese") {
            fonts.set("w:eastAsia", family);
            fonts.attrs.remove("w:eastAsiaTheme");
        }
    }
    if let Some(value) = field(raw, "bold") {
        let bold = value
            .as_bool()
            .or_else(|| {
                value
                    .as_str()
                    .and_then(|raw| match raw.trim().to_lowercase().as_str() {
                        "true" => Some(true),
                        "false" => Some(false),
                        _ => None,
                    })
            })
            .ok_or_else(|| anyhow::anyhow!("{prefix}.bold must be true or false"))?;
        set_bool(properties, "w:b", bold);
        set_bool(properties, "w:bCs", bold);
    }
    if let Some(value) = field(raw, "fontColor") {
        let [red, green, blue] = parse_font_color(value, &format!("{prefix}.fontColor"))?;
        properties
            .word("w:color")
            .set("w:val", format!("{red:02X}{green:02X}{blue:02X}"));
    }
    Ok(())
}

/// Apply paragraph properties using the shared typography parsers and Word-specific setters.
pub(super) fn apply_paragraph_properties(
    properties: &mut Element,
    raw: &Map<String, Value>,
    prefix: &str,
) -> Result<()> {
    let empty = Map::new();
    let spacing = field(raw, "paragraphSpacing")
        .and_then(Value::as_object)
        .unwrap_or(&empty);
    let indent = field(raw, "indentation")
        .and_then(Value::as_object)
        .unwrap_or(&empty);
    let chars = field(raw, "firstLineIndentChars");
    let first = field(indent, "firstLine");
    let hanging = field(indent, "hanging");
    // The core normalizer owns cross-field conflicts; do not maintain a second rule list here.
    FirstLineIndents {
        characters: chars,
        length: first,
        hanging,
    }
    .validate(prefix)?;
    if let Some(value) = chars {
        let number = parse_number(value, &format!("{prefix}.firstLineIndentChars"))?;
        if number < 0.0 {
            bail!("{prefix}.firstLineIndentChars must be greater than or equal to 0");
        }
        let indentation = properties.word("w:ind");
        indentation.set(
            "w:firstLineChars",
            (number * 100.0).round_ties_even() as i64,
        );
        for key in ["w:firstLine", "w:hanging", "w:hangingChars"] {
            indentation.attrs.remove(key);
        }
    }
    for (side, attribute) in [("before", "w:before"), ("after", "w:after")] {
        if let Some(value) = field(spacing, side) {
            let points = papper_core::style_values::parse_points(
                value,
                &format!("{prefix}.paragraphSpacing.{side}"),
            )?;
            properties.word("w:spacing").set(
                attribute,
                layout_twips(LayoutLength::from_unit(points, "pt")),
            );
        }
    }
    if let Some(value) = field(raw, "lineSpacing") {
        let parsed = parse_line_spacing(value, &format!("{prefix}.lineSpacing"))?;
        let (line, rule) = match parsed.amount {
            LineSpacingAmount::Points(length) => (layout_twips(length), "exact"),
            LineSpacingAmount::Multiple(multiple) => {
                ((multiple * 240.0).round_ties_even() as i64, "auto")
            }
        };
        if line <= 0 {
            bail!("{prefix}.lineSpacing must be greater than 0");
        }
        let spacing = properties.word("w:spacing");
        spacing.set("w:line", line);
        spacing.set("w:lineRule", rule);
    }
    if let Some(value) = field(raw, "alignment") {
        let (alignment, _) = parse_alignment(value, &format!("{prefix}.alignment"))?;
        properties.word("w:jc").set(
            "w:val",
            if alignment == "justify" {
                "both"
            } else {
                &alignment
            },
        );
    }
    for (side, attribute) in [("left", "w:left"), ("right", "w:right")] {
        if let Some(value) = field(indent, side) {
            properties.word("w:ind").set(
                attribute,
                layout_twips(parse_length(
                    value,
                    &format!("{prefix}.indentation.{side}"),
                )?),
            );
        }
    }
    if let Some(value) = first.or(hanging) {
        let amount = layout_twips(parse_length(value, &format!("{prefix}.indentation"))?)
            * if hanging.is_some() { -1 } else { 1 };
        let indentation = properties.word("w:ind");
        for key in ["w:firstLineChars", "w:hangingChars"] {
            indentation.attrs.remove(key);
        }
        if amount < 0 {
            indentation.attrs.remove("w:firstLine");
            indentation.set("w:hanging", -amount);
        } else {
            indentation.attrs.remove("w:hanging");
            indentation.set("w:firstLine", amount);
        }
    }
    Ok(())
}

/// Enable configured line numbers while preserving disabled reference settings.
pub(crate) fn apply_line_numbers(document: &mut Element, settings: &PmtSettings) -> Result<()> {
    let raw = match &settings.fields().docx_show_line_numbers {
        LineNumberMode::Enabled(false) => return Ok(()),
        LineNumberMode::Enabled(true) => "true".to_owned(),
        LineNumberMode::Named(raw) => raw.trim().to_lowercase().replace(['_', ' '], "-"),
    };
    let restart = match raw.as_str() {
        "false" | "off" | "no" | "0" | "none" | "null" | "disable" | "disabled" | "不显示"
        | "关闭" | "无" | "" => return Ok(()),
        "continuous" | "continue" | "continuously" | "true" | "on" | "yes" | "1" | "连续" => {
            "continuous"
        }
        "restart-page" | "restart-each-page" | "new-page" | "newpage" | "page" | "每页"
        | "每页重编" | "按页重启" => "newPage",
        "restart-section"
        | "restart-each-section"
        | "new-section"
        | "newsection"
        | "section"
        | "每节"
        | "每节重编"
        | "按节重启" => "newSection",
        _ => bail!("Unsupported docxShowLineNumbers value: {raw}"),
    };
    document.visit_mut(&mut |element| {
        if element.name == "w:sectPr" {
            let numbering = element.word("w:lnNumType");
            numbering.set("w:countBy", "1");
            numbering.set("w:restart", restart);
        }
    });
    Ok(())
}

/// Apply the named equation paragraph style only to top-level tab-layout equations.
pub(crate) fn apply_para_equation(document: &mut Element, styles: &mut Element) -> Result<()> {
    let Some(body) = document.child("w:body") else {
        return Ok(());
    };
    let indices: Vec<usize> = body
        .children
        .iter()
        .enumerate()
        .filter_map(|(index, node)| {
            if let Node::Element(element) = node {
                (element.name == "w:p"
                    && (element.contains("w:tab")
                        || element.contains("w:tabs")
                        || paragraph_style(element) == Some("ParaEquation"))
                    && (element.contains("m:oMath")
                        || element.contains("m:oMathPara")
                        || mathtype_object(element)))
                .then_some(index)
            } else {
                None
            }
        })
        .collect();
    if indices.is_empty() {
        return Ok(());
    }
    let body_index = style_index(styles, "Body Text")
        .or_else(|| style_index(styles, "正文文本"))
        .ok_or_else(|| anyhow::anyhow!("Neither 'Body Text' nor '正文文本' style was found"))?;
    let Node::Element(body_style) = &styles.children[body_index] else {
        unreachable!()
    };
    let base_id = body_style
        .attr("w:styleId")
        .unwrap_or("BodyText")
        .to_owned();
    let mut inherited_positions = BTreeSet::new();
    let mut visited = BTreeSet::new();
    let mut current_id = Some(base_id.clone());
    while let Some(identifier) = current_id.filter(|identifier| visited.insert(identifier.clone()))
    {
        let Some(style) = styles
            .elements()
            .find(|style| style.attr("w:styleId") == Some(identifier.as_str()))
        else {
            break;
        };
        if let Some(tabs) = style
            .child("w:pPr")
            .and_then(|properties| properties.child("w:tabs"))
        {
            for tab in tabs.elements().filter(|tab| tab.name == "w:tab") {
                if let Some(position) = tab
                    .attr("w:pos")
                    .and_then(|position| position.parse::<i64>().ok())
                {
                    inherited_positions.insert(position);
                }
            }
        }
        current_id = style
            .child("w:basedOn")
            .and_then(|base| base.attr("w:val"))
            .map(str::to_owned);
    }
    let mut dimensions = None;
    document.visit(&mut |element| {
        if element.name == "w:sectPr" && dimensions.is_none() {
            let size = element.child("w:pgSz");
            let margins = element.child("w:pgMar");
            dimensions = Some((
                size.and_then(|value| value.attr("w:w"))
                    .and_then(|value| value.parse::<i64>().ok())
                    .unwrap_or(11906),
                margins
                    .and_then(|value| value.attr("w:left"))
                    .and_then(|value| value.parse::<i64>().ok())
                    .unwrap_or(1080),
                margins
                    .and_then(|value| value.attr("w:right"))
                    .and_then(|value| value.parse::<i64>().ok())
                    .unwrap_or(1080),
            ));
        }
    });
    let (width, left, right) = dimensions
        .ok_or_else(|| anyhow::anyhow!("DOCX section dimensions are required for equation tabs"))?;
    let right = width - left - right;
    if right <= 0 {
        bail!("DOCX margins leave no equation width")
    };
    let center = (right as f64 / 2.0).round_ties_even() as i64;
    if style_index(styles, "Para Equation").is_none() {
        let mut style = Element::with_attrs(
            "w:style",
            &[
                ("w:type", "paragraph"),
                ("w:customStyle", "1"),
                ("w:styleId", "ParaEquation"),
            ],
        );
        style.push(Element::with_attrs("w:name", &[("w:val", "Para Equation")]));
        styles.push(style);
    }
    let style = get_style_mut(styles, "Para Equation").unwrap();
    let style_id = style.attr("w:styleId").unwrap_or("ParaEquation").to_owned();
    style.word("w:basedOn").set("w:val", &base_id);
    let properties = style.word("w:pPr");
    properties.remove("w:tabs");
    let tabs = properties.word("w:tabs");
    for position in inherited_positions
        .into_iter()
        .filter(|position| *position != center && *position != right)
    {
        tabs.push(Element::with_attrs(
            "w:tab",
            &[("w:pos", &position.to_string()), ("w:val", "clear")],
        ));
    }
    tabs.push(Element::with_attrs(
        "w:tab",
        &[("w:pos", &center.to_string()), ("w:val", "center")],
    ));
    tabs.push(Element::with_attrs(
        "w:tab",
        &[("w:pos", &right.to_string()), ("w:val", "right")],
    ));
    properties.word("w:textAlignment").set("w:val", "center");
    let spacing = properties.word("w:spacing");
    spacing.set("w:line", "240");
    spacing.set("w:lineRule", "auto");
    spacing.attrs.remove("w:after");
    spacing.set("w:afterLines", "50");
    spacing.set("w:afterAutospacing", "0");
    set_bool(style, "w:semiHidden", false);
    set_bool(style, "w:qFormat", true);
    let body = document.child_mut("w:body").unwrap();
    for index in indices {
        if let Node::Element(paragraph) = &mut body.children[index] {
            let properties = paragraph.word("w:pPr");
            properties.word("w:pStyle").set("w:val", &style_id);
            properties.remove("w:textAlignment");
            properties.remove("w:tabs");
            if let Some(spacing) = properties.child_mut("w:spacing") {
                for key in [
                    "w:after",
                    "w:afterLines",
                    "w:afterAutospacing",
                    "w:line",
                    "w:lineRule",
                ] {
                    spacing.attrs.remove(key);
                }
                if spacing.attrs.is_empty() && spacing.children.is_empty() {
                    properties.remove("w:spacing");
                }
            }
        }
    }
    Ok(())
}

/// Recognize only equation OLE programs so ordinary embedded objects retain their styling.
fn mathtype_object(element: &Element) -> bool {
    let mut found = false;
    element.visit(&mut |object| {
        if object.name == "o:OLEObject"
            && object.attr("ProgID").is_some_and(|program| {
                program.starts_with("Equation.") || program.contains("MathType")
            })
        {
            found = true;
        }
    });
    found
}

/// Color reviewer-reply captions and regular tables without changing layout helpers.
pub(crate) fn apply_reply_style(document: &mut Element, styles: &mut Element) {
    let mut captions = Vec::new();
    for style in styles.elements_mut() {
        let name = style
            .child("w:name")
            .and_then(|name| name.attr("w:val"))
            .unwrap_or_default()
            .to_owned();
        // Word stores its built-in Caption name as lowercase; python-docx
        // exposes the UI alias "Caption", including in reviewer replies.
        if name == "caption" || name.contains("Caption") || name.contains("题注") {
            captions.push(style.attr("w:styleId").unwrap_or_default().to_owned());
            style.word("w:rPr").word("w:color").set("w:val", "0000FF");
            set_bool(style.word("w:rPr"), "w:i", true);
        }
        if name == "Para Where" {
            style.word("w:rPr").word("w:color").set("w:val", "0000FF");
        }
    }
    document.visit_mut(&mut |element| {
        if element.name == "w:p"
            && paragraph_style(element)
                .is_some_and(|name| captions.iter().any(|caption| caption == name))
        {
            for run in element.elements_mut().filter(|child| child.name == "w:r") {
                run.word("w:rPr").word("w:color").set("w:val", "0000FF");
                set_bool(run.word("w:rPr"), "w:i", true);
            }
        }
    });
    document.visit_mut(&mut |element| {
        if element.name == "w:tbl" && !super::tables::is_equation(element) {
            element.visit_mut(&mut |child| {
                if child.name == "w:r" {
                    child.word("w:rPr").word("w:color").set("w:val", "0000FF");
                    set_bool(child.word("w:rPr"), "w:i", true);
                }
            });
        }
    });
}
