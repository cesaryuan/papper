//! Apply metadata-driven Word styles, section settings, and equation paragraphs.

use super::package::Package;
use super::xml::{Element, Node};
use anyhow::{Result, bail};
use papper_core::metadata::PmtSettings;
use serde_json::{Map, Value};
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
    let pattern =
        regex::Regex::new(r"^(-?\d+(?:\.\d+)?)\s*(pt|磅|cm|厘米|mm|毫米|in|inch|inches|英寸)?$")?;
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

/// Derive MathType template tabs only when explicit page margins are configured.
pub(crate) fn equation_tabs(settings: &PmtSettings) -> Result<(i64, i64)> {
    let Some(margins) = settings.get("docxPageMargins").and_then(Value::as_object) else {
        return Ok((4156, 8312));
    };
    let width = settings
        .get("docxPageWidth")
        .filter(|value| !value.is_null())
        .map(length_twips)
        .transpose()?
        .unwrap_or(11906);
    let left = margins
        .get("left")
        .filter(|value| !value.is_null())
        .map(length_twips)
        .transpose()?
        .unwrap_or(1080);
    let right = margins
        .get("right")
        .filter(|value| !value.is_null())
        .map(length_twips)
        .transpose()?
        .unwrap_or(1080);
    let text_width = width - left - right;
    if text_width <= 0 {
        bail!("docxPageMargins left/right values leave no positive DOCX text width");
    }
    Ok((
        (text_width as f64 / 2.0).round_ties_even() as i64,
        text_width,
    ))
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

/// Pick aliases in supplied order while retaining explicit false and empty values.
fn field<'a>(mapping: &'a Map<String, Value>, names: &[&str]) -> Option<&'a Value> {
    names
        .iter()
        .find_map(|name| mapping.get(*name))
        .filter(|value| !value.is_null())
}

/// Parse Chinese font sizes or point values in half-point Word units.
fn font_half_points(value: &Value) -> Result<i64> {
    let chinese = match value.as_str().map(str::trim).unwrap_or_default() {
        "初号" => Some(42.0),
        "小初" | "小初号" => Some(36.0),
        "一号" => Some(26.0),
        "小一" | "小一号" => Some(24.0),
        "二号" => Some(22.0),
        "小二" | "小二号" => Some(18.0),
        "三号" => Some(16.0),
        "小三" | "小三号" => Some(15.0),
        "四号" => Some(14.0),
        "小四" | "小四号" => Some(12.0),
        "五号" => Some(10.5),
        "小五" | "小五号" => Some(9.0),
        "六号" => Some(7.5),
        "小六" | "小六号" => Some(6.5),
        "七号" => Some(5.5),
        "八号" => Some(5.0),
        _ => None,
    };
    let half = if let Some(points) = chinese {
        (points * 2.0) as i64
    } else {
        length_twips(value)? / 10
    };
    if half <= 0 {
        bail!("Font size must be greater than 0");
    }
    Ok(half)
}

/// Parse all supported font color forms into Word's uppercase RGB encoding.
fn color_hex(value: &Value) -> Result<String> {
    if let Some(channels) = value.as_array()
        && channels.len() == 3
        && channels
            .iter()
            .all(|channel| channel.as_u64().is_some_and(|number| number <= 255))
    {
        return Ok(channels
            .iter()
            .map(|channel| format!("{:02X}", channel.as_u64().unwrap()))
            .collect());
    }
    if let Some(raw) = value.as_str() {
        let cleaned = raw.trim().trim_start_matches('#');
        if cleaned.len() == 6
            && cleaned
                .chars()
                .all(|character| character.is_ascii_hexdigit())
        {
            return Ok(cleaned.to_uppercase());
        }
        if let Some(body) = cleaned
            .strip_prefix("rgb(")
            .and_then(|body| body.strip_suffix(')'))
        {
            let channels: Vec<u16> = body
                .split(',')
                .map(|channel| channel.trim().parse())
                .collect::<std::result::Result<_, _>>()?;
            if channels.len() == 3 && channels.iter().all(|channel| *channel <= 255) {
                return Ok(channels
                    .iter()
                    .map(|channel| format!("{channel:02X}"))
                    .collect());
            }
        }
    }
    bail!("Font color must be #RRGGBB, rgb(r,g,b), or three RGB channels")
}

/// Apply immutable typography settings to paragraph styles without rewriting content.
pub(crate) fn apply_styles(styles: &mut Element, settings: &PmtSettings) -> Result<()> {
    let Some(configured) = settings.get("docxStyle").and_then(Value::as_object) else {
        return Ok(());
    };
    for (name, raw) in configured {
        let raw = raw
            .as_object()
            .ok_or_else(|| anyhow::anyhow!("docxStyle.{name} must be a mapping"))?;
        let Some(style) = get_style_mut(styles, name) else {
            eprintln!("[DOCX] Style '{name}' was not found, skipping");
            continue;
        };
        let empty = Map::new();
        let font = field(raw, &["font", "textStyle", "text-style", "text_style"])
            .and_then(Value::as_object)
            .unwrap_or(&empty);
        if let Some(value) = field(raw, &["fontSize", "font-size", "font_size"])
            .or_else(|| field(font, &["size", "fontSize", "font-size"]))
        {
            style
                .word("w:rPr")
                .word("w:sz")
                .set("w:val", font_half_points(value)?);
        }
        if let Some(value) = field(
            raw,
            &[
                "fontFamily",
                "font-family",
                "font_family",
                "fontName",
                "font-name",
                "font_name",
            ],
        )
        .or_else(|| {
            field(
                font,
                &[
                    "family",
                    "name",
                    "fontFamily",
                    "font-family",
                    "fontName",
                    "font-name",
                ],
            )
        }) {
            let western = value.as_str().or_else(|| {
                value
                    .as_object()
                    .and_then(|mapping| {
                        field(
                            mapping,
                            &[
                                "western", "latin", "ascii", "en", "fontName", "name", "family",
                            ],
                        )
                    })
                    .and_then(Value::as_str)
            });
            let chinese = value.as_str().or_else(|| {
                value
                    .as_object()
                    .and_then(|mapping| {
                        field(
                            mapping,
                            &[
                                "chinese",
                                "eastAsia",
                                "east-asian",
                                "eastAsian",
                                "cjk",
                                "zh",
                            ],
                        )
                    })
                    .and_then(Value::as_str)
            });
            let fonts = style.word("w:rPr").word("w:rFonts");
            if let Some(family) = western {
                fonts.set("w:ascii", family);
                fonts.set("w:hAnsi", family);
                fonts.attrs.remove("w:asciiTheme");
                fonts.attrs.remove("w:hAnsiTheme");
            }
            if let Some(family) = chinese {
                fonts.set("w:eastAsia", family);
                fonts.attrs.remove("w:eastAsiaTheme");
            }
        }
        if let Some(value) = field(raw, &["bold", "fontBold", "font-bold", "font_bold"])
            .or_else(|| field(font, &["bold", "fontBold", "font-bold", "font_bold"]))
        {
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
                .ok_or_else(|| anyhow::anyhow!("docxStyle.{name}.bold must be true or false"))?;
            set_bool(style.word("w:rPr"), "w:b", bold);
            set_bool(style.word("w:rPr"), "w:bCs", bold);
        }
        if let Some(value) = field(raw, &["fontColor", "font-color", "font_color", "color"])
            .or_else(|| field(font, &["color", "fontColor", "font-color"]))
        {
            style
                .word("w:rPr")
                .word("w:color")
                .set("w:val", color_hex(value)?);
        }
        apply_paragraph_properties(style.word("w:pPr"), raw)?;
    }
    Ok(())
}

/// Apply paragraph settings in the same setter order as the existing DOCX pipeline.
fn apply_paragraph_properties(properties: &mut Element, raw: &Map<String, Value>) -> Result<()> {
    let empty = Map::new();
    let spacing = field(
        raw,
        &["paragraphSpacing", "paragraph-spacing", "paragraph_spacing"],
    )
    .and_then(Value::as_object)
    .unwrap_or(&empty);
    let indent = field(
        raw,
        &[
            "indentation",
            "indent",
            "paragraphIndent",
            "paragraph-indent",
        ],
    )
    .and_then(Value::as_object)
    .unwrap_or(&empty);
    let chars = field(
        raw,
        &[
            "firstLineIndentChars",
            "first-line-indent-chars",
            "first_line_indent_chars",
        ],
    )
    .or_else(|| {
        field(
            indent,
            &[
                "firstLineChars",
                "first-line-chars",
                "first_line_chars",
                "firstLineIndentChars",
                "first-line-indent-chars",
            ],
        )
    });
    if let Some(value) = chars {
        let number = value
            .as_f64()
            .or_else(|| {
                value
                    .as_str()
                    .and_then(|raw| raw.split_whitespace().next()?.parse().ok())
            })
            .ok_or_else(|| anyhow::anyhow!("Character indent must be numeric"))?;
        if number < 0.0 {
            bail!("Character indent must be greater than or equal to 0");
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
    for (attribute, aliases) in [
        (
            "w:before",
            vec!["before", "spaceBefore", "space-before", "space_before"],
        ),
        (
            "w:after",
            vec!["after", "spaceAfter", "space-after", "space_after"],
        ),
    ] {
        if let Some(value) = field(spacing, &aliases) {
            properties
                .word("w:spacing")
                .set(attribute, length_twips(value)?);
        }
    }
    if let Some(value) = field(raw, &["lineSpacing", "line-spacing", "line_spacing"]) {
        let value = value
            .as_object()
            .and_then(|mapping| {
                field(
                    mapping,
                    &[
                        "value",
                        "spacing",
                        "lineSpacing",
                        "line-spacing",
                        "line_spacing",
                    ],
                )
            })
            .unwrap_or(value);
        let text = value
            .as_str()
            .map(|raw| raw.trim().to_lowercase())
            .unwrap_or_else(|| value.to_string());
        let (line, rule) = if text.ends_with("pt") || text.ends_with('磅') {
            (length_twips(value)?, "exact")
        } else {
            let multiple: f64 = match text.as_str() {
                "single" => 1.0,
                "one-half" | "onehalf" => 1.5,
                "double" => 2.0,
                _ => text.parse()?,
            };
            ((multiple * 240.0).round_ties_even() as i64, "auto")
        };
        if line <= 0 {
            bail!("Line spacing must be greater than 0");
        }
        let spacing = properties.word("w:spacing");
        spacing.set("w:line", line);
        spacing.set("w:lineRule", rule);
    }
    if let Some(value) = field(
        raw,
        &[
            "alignment",
            "align",
            "paragraphAlignment",
            "paragraph-alignment",
        ],
    ) {
        let alignment = value.as_str().unwrap_or_default().trim().to_lowercase();
        let alignment = match alignment.as_str() {
            "justify" | "justified" | "both" => "both",
            "centre" => "center",
            "left" | "center" | "right" | "distribute" => &alignment,
            _ => bail!("Invalid paragraph alignment: {alignment}"),
        };
        properties.word("w:jc").set("w:val", alignment);
    }
    for (attribute, top, nested) in [
        (
            "w:left",
            vec!["leftIndent", "left-indent", "left_indent"],
            vec!["left", "leftIndent", "left-indent"],
        ),
        (
            "w:right",
            vec!["rightIndent", "right-indent", "right_indent"],
            vec!["right", "rightIndent", "right-indent"],
        ),
    ] {
        if let Some(value) = field(raw, &top).or_else(|| field(indent, &nested)) {
            properties
                .word("w:ind")
                .set(attribute, length_twips(value)?);
        }
    }
    let first = field(
        raw,
        &["firstLineIndent", "first-line-indent", "first_line_indent"],
    )
    .or_else(|| {
        field(
            indent,
            &["firstLine", "first-line", "first_line", "firstLineIndent"],
        )
    });
    let hanging = field(raw, &["hangingIndent", "hanging-indent", "hanging_indent"])
        .or_else(|| field(indent, &["hanging", "hangingIndent", "hanging-indent"]));
    if first.is_some() && hanging.is_some()
        || chars.is_some() && (first.is_some() || hanging.is_some())
    {
        bail!("Conflicting character/length paragraph indentation");
    }
    if let Some(value) = first.or(hanging) {
        let amount = length_twips(value)? * if hanging.is_some() { -1 } else { 1 };
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
    let Some(value) = settings.get("docxShowLineNumbers") else {
        return Ok(());
    };
    let raw = value
        .as_str()
        .map(|raw| raw.trim().to_lowercase().replace(['_', ' '], "-"))
        .unwrap_or_else(|| value.to_string());
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
        _ if value.is_number() => "continuous",
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

/// Apply explicit footer page visibility without disturbing other footer text.
pub(crate) fn apply_page_numbers(
    package: &mut Package,
    document: &mut Element,
    styles: &Element,
    settings: &PmtSettings,
) -> Result<()> {
    let Some(visible) = settings.get_bool("docxShowPageNumbers") else {
        return Ok(());
    };
    let mut names: Vec<String> = package
        .entries
        .keys()
        .filter(|name| name.starts_with("word/footer") && name.ends_with(".xml"))
        .cloned()
        .collect();
    if visible && names.is_empty() {
        let footer = default_footer();
        package.set_xml("word/footer1.xml", &footer);
        names.push("word/footer1.xml".into());
        let mut relationships = package.xml("word/_rels/document.xml.rels")?;
        let mut index = 1;
        while relationships
            .elements()
            .any(|relation| relation.attr("Id") == Some(&format!("rId{index}")))
        {
            index += 1;
        }
        let identifier = format!("rId{index}");
        relationships.push(Element::with_attrs(
            "Relationship",
            &[
                ("Id", &identifier),
                (
                    "Type",
                    "http://schemas.openxmlformats.org/officeDocument/2006/relationships/footer",
                ),
                ("Target", "footer1.xml"),
            ],
        ));
        package.set_xml("word/_rels/document.xml.rels", &relationships);
        let mut linked = false;
        document.visit_mut(&mut |element| {
            if element.name == "w:sectPr" && !linked {
                element.word("w:footerReference").set("w:type", "default");
                element.word("w:footerReference").set("r:id", &identifier);
                linked = true;
            }
        });
        let mut content_types = package.xml("[Content_Types].xml")?;
        content_types.push(Element::with_attrs(
            "Override",
            &[
                ("PartName", "/word/footer1.xml"),
                (
                    "ContentType",
                    "application/vnd.openxmlformats-officedocument.wordprocessingml.footer+xml",
                ),
            ],
        ));
        package.set_xml("[Content_Types].xml", &content_types);
    }
    for name in names {
        let mut footer = package.xml(&name)?;
        if visible {
            let mut existing = false;
            footer.visit(&mut |element| {
                if element.name == "w:instrText" && page_instruction(&element.direct_text())
                    || element.name == "w:fldSimple"
                        && element.attr("w:instr").is_some_and(page_instruction)
                {
                    existing = true;
                }
            });
            if !existing {
                let index = style_index(styles, "page number").ok_or_else(|| {
                    anyhow::anyhow!(
                        "DOCX reference document must define the `page number` character style"
                    )
                })?;
                let Node::Element(style) = &styles.children[index] else {
                    unreachable!()
                };
                let identifier = style.attr("w:styleId").unwrap_or("page number");
                let paragraph = footer.ensure("w:p");
                paragraph.word("w:pPr").word("w:jc").set("w:val", "center");
                for (kind, text) in [
                    (Some("begin"), None),
                    (None, Some(" PAGE ")),
                    (Some("separate"), None),
                    (None, Some("1")),
                    (Some("end"), None),
                ] {
                    let mut run = Element::new("w:r");
                    run.word("w:rPr").word("w:rStyle").set("w:val", identifier);
                    if let Some(kind) = kind {
                        run.push(Element::with_attrs("w:fldChar", &[("w:fldCharType", kind)]));
                    } else {
                        let mut child = Element::new(if text == Some(" PAGE ") {
                            "w:instrText"
                        } else {
                            "w:t"
                        });
                        if text == Some(" PAGE ") {
                            child.set("xml:space", "preserve");
                        }
                        child
                            .children
                            .push(Node::Text(text.unwrap_or_default().into()));
                        run.push(child);
                    }
                    paragraph.push(run);
                }
            }
        } else {
            footer.visit_mut(&mut |element| {
                if element.name == "w:p" {
                    remove_page_fields(element);
                }
            });
        }
        package.set_xml(&name, &footer);
    }
    Ok(())
}

/// Create the Word-compatible primary footer with the established Footer style.
fn default_footer() -> Element {
    let mut footer = Element::new("w:ftr");
    for (prefix, namespace) in [
        (
            "m",
            "http://schemas.openxmlformats.org/officeDocument/2006/math",
        ),
        (
            "mc",
            "http://schemas.openxmlformats.org/markup-compatibility/2006",
        ),
        (
            "mo",
            "http://schemas.microsoft.com/office/mac/office/2008/main",
        ),
        ("mv", "urn:schemas-microsoft-com:mac:vml"),
        ("o", "urn:schemas-microsoft-com:office:office"),
        (
            "r",
            "http://schemas.openxmlformats.org/officeDocument/2006/relationships",
        ),
        ("v", "urn:schemas-microsoft-com:vml"),
        (
            "w",
            "http://schemas.openxmlformats.org/wordprocessingml/2006/main",
        ),
        ("w10", "urn:schemas-microsoft-com:office:word"),
        (
            "w14",
            "http://schemas.microsoft.com/office/word/2010/wordml",
        ),
        (
            "wne",
            "http://schemas.microsoft.com/office/word/2006/wordml",
        ),
        (
            "wp",
            "http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing",
        ),
        (
            "wp14",
            "http://schemas.microsoft.com/office/word/2010/wordprocessingDrawing",
        ),
        (
            "wpc",
            "http://schemas.microsoft.com/office/word/2010/wordprocessingCanvas",
        ),
        (
            "wpg",
            "http://schemas.microsoft.com/office/word/2010/wordprocessingGroup",
        ),
        (
            "wpi",
            "http://schemas.microsoft.com/office/word/2010/wordprocessingInk",
        ),
        (
            "wps",
            "http://schemas.microsoft.com/office/word/2010/wordprocessingShape",
        ),
    ] {
        footer.set(&format!("xmlns:{prefix}"), namespace);
    }
    footer.set("mc:Ignorable", "w14 wp14");
    let mut paragraph = Element::new("w:p");
    paragraph
        .word("w:pPr")
        .word("w:pStyle")
        .set("w:val", "Footer");
    footer.push(paragraph);
    footer
}

/// Recognize the PAGE instruction without matching NUMPAGES or unrelated text.
fn page_instruction(text: &str) -> bool {
    regex::Regex::new(r"(?i)\bPAGE\b")
        .expect("valid PAGE field grammar")
        .is_match(text)
}

/// Remove simple/complex PAGE ranges while retaining unrelated footer runs.
fn remove_page_fields(paragraph: &mut Element) {
    paragraph.children.retain(|node|!matches!(node,Node::Element(element)if element.name=="w:fldSimple"&&element.attr("w:instr").is_some_and(page_instruction)));
    let mut open: Vec<(usize, String)> = Vec::new();
    let mut ranges = Vec::new();
    for (index, node) in paragraph.children.iter().enumerate() {
        if let Node::Element(element) = node {
            element.visit(&mut |child| {
                if child.name == "w:fldChar" {
                    match child.attr("w:fldCharType") {
                        Some("begin") => open.push((index, String::new())),
                        Some("end") => {
                            if let Some((start, instruction)) = open.pop()
                                && page_instruction(&instruction)
                            {
                                ranges.push((start, index));
                            }
                        }
                        _ => {}
                    }
                }
                if child.name == "w:instrText" {
                    for (_, text) in &mut open {
                        text.push_str(&child.direct_text());
                    }
                }
            });
        }
    }
    for (start, end) in ranges.into_iter().rev() {
        paragraph.children.drain(start..=end);
    }
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
            .find(|style| style.attr("w:styleId") == Some(&identifier))
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
