//! Prepare DOCX metadata and edit Word packages without a Python runtime.

mod authors;
mod captions;
mod formatting;
mod lists;
mod mathtype;
mod native;
mod package;
mod tables;
mod xml;

use anyhow::{Context, Result, bail};
use package::Package;
use papper_core::metadata::{EffectiveMetadata, LineNumberMode, is_chinese_language};
use papper_core::resources::ResourcePaths;
use serde_json::{Map, Value, json};
use std::path::{Path, PathBuf};

pub use mathtype::{
    MathTypeConversionResult, check_mathtype_available, convert_marked_docx,
    convert_marked_docx_with_work_dir,
};

/// Control manuscript/reply formatting without changing shared metadata state.
#[derive(Clone, Debug, Default)]
pub struct DocxPostprocessOptions {
    pub skip_author_info: bool,
    pub reply_style_formatting: bool,
    pub native_crossrefs: bool,
}

/// Measure Han characters among letters and numbers in DOCX body and note text.
pub fn chinese_character_ratio(input: &Path) -> Result<f64> {
    use std::io::Read;

    let mut archive = zip::ZipArchive::new(std::fs::File::open(input)?)?;
    let han = regex::Regex::new(r"\p{Han}")?;
    let mut chinese = 0_u64;
    let mut total = 0_u64;
    for name in [
        "word/document.xml",
        "word/footnotes.xml",
        "word/endnotes.xml",
    ] {
        let mut part = match archive.by_name(name) {
            Ok(part) => part,
            Err(zip::result::ZipError::FileNotFound) if name != "word/document.xml" => continue,
            Err(error) => return Err(error.into()),
        };
        let mut text = String::new();
        part.read_to_string(&mut text)?;
        let document = roxmltree::Document::parse(&text)
            .with_context(|| format!("Cannot parse DOCX part {name}"))?;
        for node in document.descendants().filter(|node| {
            node.has_tag_name((
                "http://schemas.openxmlformats.org/wordprocessingml/2006/main",
                "t",
            ))
        }) {
            // Count authored text, not XML, Markdown attributes, or media paths;
            // punctuation and whitespace must not dilute a Chinese manuscript.
            let text = node.text().unwrap_or_default();
            total += text.chars().filter(|c| c.is_alphanumeric()).count() as u64;
            chinese += han
                .find_iter(text)
                .filter(|matched| matched.as_str().chars().all(char::is_alphanumeric))
                .count() as u64;
        }
    }
    // Empty and picture-only documents have no evidence of a Chinese language.
    Ok(if total == 0 {
        0.0
    } else {
        chinese as f64 / total as f64
    })
}

/// Apply Chinese-only line-number defaults to a cloned effective configuration.
pub fn prepare_docx_metadata(effective: &EffectiveMetadata) -> Result<EffectiveMetadata> {
    let mut prepared = effective.clone();
    if effective
        .pandoc_metadata
        .get("lang")
        .is_some_and(is_chinese_language)
        && (!effective.pmt_settings.was_provided("docxShowLineNumbers")
            || matches!(&effective.pmt_settings.fields().docx_show_line_numbers, LineNumberMode::Named(mode) if mode == "连续"))
    {
        prepared
            .pmt_settings
            .set_line_numbers(LineNumberMode::Enabled(false));
    }
    Ok(prepared)
}

/// Prepare reference margins before Pandoc uses the reference's text width for images.
pub fn prepare_reference(
    resources: &ResourcePaths,
    effective: &EffectiveMetadata,
    override_path: Option<&Path>,
    work_dir: &Path,
) -> Result<Option<PathBuf>> {
    let Some(margins) = &effective.pmt_settings.fields().docx_page_margins else {
        return Ok(override_path.map(Path::to_path_buf));
    };
    if margins.is_empty() {
        return Ok(override_path.map(Path::to_path_buf));
    }
    let bundled = resources
        .root
        .join("pandoc/manuscript-template/reference-doc.docx");
    let source = override_path.unwrap_or(&bundled);
    let mut package = Package::open(source)?;
    let mut document = package.xml("word/document.xml")?;
    let mut parsed = Vec::new();
    for (side, aliases) in [
        ("top", vec!["top"]),
        ("bottom", vec!["bottom"]),
        ("left", vec!["left", "inside"]),
        ("right", vec!["right", "outside"]),
    ] {
        if let Some(value) = aliases.iter().find_map(|alias| margins.get(*alias)) {
            let amount = formatting::configured_length_twips(value)?;
            if amount < 0 {
                bail!("docxPageMargins.{side} must be greater than or equal to 0");
            }
            parsed.push((format!("w:{side}"), amount));
        }
    }
    document.visit_mut(&mut |element| {
        if element.name == "w:sectPr" {
            let margins = element.word("w:pgMar");
            for (name, amount) in &parsed {
                margins.set(name, amount);
            }
        }
    });
    package.set_xml("word/document.xml", &document);
    let target = work_dir.join("reference.docx");
    package.save(&target)?;
    Ok(Some(target))
}

/// Derive the existing crossref equation templates for native Word or MathType layout.
pub fn derive_docx_pandoc_metadata(
    effective: &EffectiveMetadata,
    use_mathtype: bool,
) -> Result<Map<String, Value>> {
    let mut metadata = effective.pandoc_metadata.clone();
    metadata.insert("tableEqns".into(), json!(true));
    if use_mathtype {
        // Inline XML supplies tab characters only. Para Equation derives the
        // stops from the final section width without injecting a second pPr.
        metadata.insert(
            "eqnBlockTemplate".into(),
            json!("`<w:r><w:tab /></w:r>`{=openxml}$$t$$`<w:r><w:tab /></w:r>`{=openxml}$$nmi$$"),
        );
        metadata.insert("eqnBlockInlineMath".into(), json!(true));
    } else {
        metadata.insert("eqnBlockTemplate".into(), json!("+:------+:--------------------------------------------------:+--------:+\n|       | $$t$$                                              | $$nmi$$ |\n+-------+----------------------------------------------------+---------+\n"));
        metadata.remove("eqnBlockInlineMath");
    }
    Ok(metadata)
}

/// Reproduce the ordered document pipeline and atomically preserve complete OPC data.
pub fn postprocess_docx(
    input: &Path,
    output: &Path,
    effective: &EffectiveMetadata,
    options: &DocxPostprocessOptions,
) -> Result<()> {
    let mut package = Package::open(input)?;
    let mut document = package.xml("word/document.xml")?;
    let mut styles = package.xml("word/styles.xml")?;
    if options.native_crossrefs {
        native::finalize(&mut package, &mut document, &mut styles)?;
    }
    lists::bracketed(&mut package, &mut document, &mut styles)?;
    if !options.skip_author_info {
        authors::insert(
            &mut package,
            &mut document,
            &mut styles,
            &effective.pandoc_metadata,
        )?;
    }
    formatting::apply_styles(&mut styles, &effective.pmt_settings)?;
    formatting::apply_line_numbers(&mut document, &effective.pmt_settings)?;
    formatting::apply_page_numbers(
        &mut package,
        &mut document,
        &styles,
        &effective.pmt_settings,
    )?;
    tables::convert_table_text_styles(&mut document, &styles);
    tables::equation_metadata(&mut document);
    tables::table_metadata(&mut document)?;
    captions::keep_groups(&mut document, &styles);
    tables::format_equations(&mut document);
    formatting::apply_para_equation(&mut document, &mut styles)?;
    if options.reply_style_formatting {
        formatting::apply_reply_style(&mut document, &mut styles);
    }
    validate_syntax(&package, &document)?;
    package.set_xml("word/document.xml", &document);
    package.set_xml("word/styles.xml", &styles);
    package
        .save(output)
        .with_context(|| format!("DOCX post-processing failed: {}", output.display()))
}

/// Inspect visible text in every document/header/footer/note part before publication.
fn validate_syntax(package: &Package, document: &xml::Element) -> Result<()> {
    let part_names =
        regex::Regex::new(r"^word/(document|header\d+|footer\d+|footnotes|endnotes)\.xml$")?;
    let fenced = regex::Regex::new(r"^\s*:{3,}(?:\s*\{[^{}]*\}|\s*)$")?;
    let attribute = regex::Regex::new(r"\{#[A-Za-z][\w.-]*(?::[\w.-]+)?(?:\s+[^{}]+)?\}")?;
    let crossref = regex::Regex::new(
        r"[@#](?:sec|fig|tbl|eq|app|alg|lem|thm|cor|def|prop|exm|exr):[A-Za-z0-9._:-]+",
    )?;
    let citation = regex::Regex::new(r"-?@[A-Za-z0-9_][A-Za-z0-9_:+-]*")?;
    let citation_brackets = regex::Regex::new(r"\[\s*@[\w:-]+(?:\s*;\s*@[\w:-]+)*\s*\]")?;
    // Match only HTML's standard element names. Scientific notation such as
    // `<k>` or `<degree>` is ordinary visible text, while known elements such
    // as `<div>` and `<span>` still indicate raw HTML that Pandoc failed to render.
    let raw_html = regex::Regex::new(concat!(
        r"(?i)<!--.*?-->|</?(?:",
        r"a|abbr|address|area|article|aside|audio|b|base|bdi|bdo|blockquote|body|br|button|",
        r"canvas|caption|cite|code|col|colgroup|data|datalist|dd|del|details|dfn|dialog|div|dl|dt|",
        r"em|embed|fieldset|figcaption|figure|footer|form|h[1-6]|head|header|hgroup|hr|html|",
        r"i|iframe|img|input|ins|kbd|label|legend|li|link|main|map|mark|menu|meta|meter|",
        r"nav|noscript|object|ol|optgroup|option|output|p|picture|pre|progress|q|rp|rt|ruby|",
        r"s|samp|script|search|section|select|slot|small|source|span|strong|style|sub|summary|sup|",
        r"table|tbody|td|template|textarea|tfoot|th|thead|time|title|tr|track|u|ul|var|video|wbr",
        r")(?:\s+[^<>]*?)?\s*/?>",
    ))?;
    let duplicate = regex::Regex::new(
        r"\b(Section|Figure|Table|Equation)\s+(Section|Figure|Table|Equation)\b",
    )?;
    let parenthesized = regex::Regex::new(
        r"\b(Section|Figure|Table|Equation)\s*\(\s*(Section|Figure|Table|Equation)\s+\d+(?:\.\d+)*\s*\)",
    )?;
    let numbered = regex::Regex::new(
        r"\b(Section|Figure|Table|Equation)\s+(\d+(?:\.\d+)*)\s+(Section|Figure|Table|Equation)\s+(\d+(?:\.\d+)*)\b",
    )?;
    let mut texts = Vec::new();
    document.visit(&mut |element| {
        if element.name == "w:p" {
            texts.push(validation_text(element));
        }
    });
    for name in package
        .entries
        .keys()
        .filter(|name| name.as_str() != "word/document.xml" && part_names.is_match(name))
    {
        package.xml(name)?.visit(&mut |element| {
            if element.name == "w:p" {
                texts.push(validation_text(element));
            }
        });
    }
    for text in texts {
        let text = text.split_whitespace().collect::<Vec<_>>().join(" ");
        let label = if fenced.is_match(&text) {
            Some("Pandoc fenced_divs marker")
        } else if attribute.is_match(&text) {
            Some("Pandoc attribute block")
        } else if crossref.find_iter(&text).any(|found| {
            text[..found.start()]
                .chars()
                .next_back()
                .is_none_or(|previous| {
                    !previous.is_alphanumeric() && !matches!(previous, '_' | '/')
                })
        }) {
            Some("Pandoc cross-reference label")
        } else if citation_brackets.is_match(&text)
            || citation.find_iter(&text).any(|found| {
                text[..found.start()]
                    .chars()
                    .next_back()
                    .is_none_or(|previous| {
                        !previous.is_alphanumeric() && !matches!(previous, '_' | '.' | '/' | '-')
                    })
            })
        {
            Some("Pandoc citation syntax")
        } else if raw_html.is_match(&text) {
            Some("raw HTML tag")
        } else if duplicate
            .captures_iter(&text)
            .any(|captures| captures[1] == captures[2])
        {
            Some("duplicated reference label word")
        } else if parenthesized
            .captures_iter(&text)
            .any(|captures| captures[1] == captures[2])
        {
            Some("duplicated parenthesized reference label")
        } else if numbered
            .captures_iter(&text)
            .any(|captures| captures[1] == captures[3] && captures[2] == captures[4])
        {
            Some("duplicated numbered reference label")
        } else {
            None
        };
        if let Some(label) = label {
            bail!("Final DOCX still contains unrendered Pandoc syntax ({label}): {text}");
        }
    }
    Ok(())
}

/// Exclude temporary hidden TeX markers that are removed after MathType conversion.
fn validation_text(paragraph: &xml::Element) -> String {
    if paragraph.name == "w:r"
        && paragraph.text().starts_with("MTLATEX:")
        && paragraph
            .child("w:rPr")
            .is_some_and(|properties| properties.contains("w:vanish"))
    {
        // A formula's source TeX can contain reference-like text. Checking the
        // intermediate marker would reject valid math before it becomes OLE.
        return String::new();
    }
    if paragraph.name == "w:t" {
        return paragraph.direct_text();
    }
    paragraph.elements().map(validation_text).collect()
}
