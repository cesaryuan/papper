//! Prepare DOCX metadata and edit Word packages without a Python runtime.

mod borders;
mod captions;
mod directives;
mod footer;
mod formatting;
mod lists;
mod mathtype;
mod native;
mod note_marks;
mod package;
mod paragraph_format;
mod syntax;
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
    pub reply_style_formatting: bool,
    pub native_crossrefs: bool,
}

/// Supply optional equation conversion and explicitly requested inspection artifacts.
pub struct DocxMathTypeOptions<'a> {
    pub resources: &'a ResourcePaths,
    pub project_dir: &'a Path,
    pub work_dir: Option<&'a Path>,
    pub marked_output: Option<&'a Path>,
}

/// Format and inject equations in one package, then publish the complete DOCX atomically.
pub fn finalize_docx(
    input: &Path,
    output: &Path,
    effective: &EffectiveMetadata,
    postprocess: Option<&DocxPostprocessOptions>,
    equations: Option<&DocxMathTypeOptions<'_>>,
) -> Result<()> {
    let mut package = Package::open(input)?;
    let custom_properties_changed = package.normalize_custom_properties()?;
    let changed = if let Some(options) = postprocess {
        postprocess_package(&mut package, effective, options)?;
        // Previously the intermediate save normalized these parts before
        // MathType read them. Preserve that order without recompressing media.
        package.normalize()?;
        true
    } else {
        finalize_package_content(&mut package, effective)?
    } || custom_properties_changed;
    let report = if let Some(options) = equations {
        if let Some(marked) = options.marked_output {
            // Explicit inspection keeps the pre-conversion DOCX; ordinary
            // builds never write this additional archive.
            if changed {
                package.save_preserving(marked)?;
            } else {
                papper_core::paths::atomic_write(marked, &std::fs::read(input)?)?;
            }
        }
        Some(mathtype::convert_package(
            &mut package,
            options.resources,
            effective,
            options.project_dir,
            options.work_dir,
        )?)
    } else {
        None
    };
    if changed || report.as_ref().is_some_and(|report| report.total > 0) {
        package.save_preserving(output)?;
    } else if input != output {
        papper_core::paths::atomic_write(output, &std::fs::read(input)?)?;
    }
    if let Some(report) = report {
        mathtype::log_conversion(&report);
    }
    Ok(())
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

/// Prepare reference styles and margins before Pandoc derives document formatting and image widths.
pub fn prepare_reference(
    resources: &ResourcePaths,
    effective: &EffectiveMetadata,
    override_path: Option<&Path>,
    work_dir: &Path,
) -> Result<Option<PathBuf>> {
    let settings = &effective.pmt_settings;
    let margins = settings
        .fields()
        .docx_page_margins
        .as_ref()
        .filter(|margins| !margins.is_empty());
    let has_styles = settings
        .fields()
        .docx_style
        .as_ref()
        .is_some_and(|styles| styles.iter().next().is_some());
    // Retaining reference margins with null must not skip configured text styles.
    if margins.is_none() && !has_styles {
        return Ok(override_path.map(Path::to_path_buf));
    }
    let bundled = resources
        .root
        .join("pandoc/manuscript-template/reference-doc.docx");
    let source = override_path.unwrap_or(&bundled);
    let mut package = Package::open(source)?;
    if has_styles {
        let mut styles = package.xml("word/styles.xml")?;
        formatting::apply_styles(&mut styles, settings)?;
        package.set_xml("word/styles.xml", &styles);
    }
    if let Some(margins) = margins {
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
    }
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
    finalize_docx(input, output, effective, Some(options), None)
        .with_context(|| format!("DOCX post-processing failed: {}", output.display()))
}

/// Apply the ordered formatting pipeline to an open package without publishing intermediate media.
fn postprocess_package(
    package: &mut Package,
    effective: &EffectiveMetadata,
    options: &DocxPostprocessOptions,
) -> Result<()> {
    let mut document = package.xml("word/document.xml")?;
    let mut styles = package.xml("word/styles.xml")?;
    footer::apply(package, &mut document, &styles, &effective.pmt_settings)?;
    directives::apply(package, &mut document, &mut styles)?;
    note_marks::apply(package, &mut document)?;
    if options.native_crossrefs {
        native::finalize(package, &mut document, &mut styles)?;
    }
    lists::bracketed(package, &mut document, &mut styles)?;
    formatting::apply_line_numbers(&mut document, &effective.pmt_settings)?;
    tables::convert_table_text_styles(&mut document, &styles);
    tables::equation_metadata(&mut document);
    tables::table_metadata(&mut document)?;
    captions::keep_groups(&mut document, &styles);
    tables::format_equations(&mut document);
    formatting::apply_para_equation(&mut document, &mut styles)?;
    if options.reply_style_formatting {
        formatting::apply_reply_style(&mut document, &mut styles);
    }
    directives::index_captions(&mut document, &mut styles)?;
    paragraph_format::apply(package, &mut document, &styles)?;
    syntax::annotate(package, &mut document)?;
    package.set_xml("word/document.xml", &document);
    package.set_xml("word/styles.xml", &styles);
    Ok(())
}

/// Finalize generated header/footer content and note marks even when optional formatting is disabled.
pub fn finalize_output_content(
    input: &Path,
    output: &Path,
    effective: &EffectiveMetadata,
) -> Result<()> {
    finalize_docx(input, output, effective, None, None)
}

/// Finalize mandatory story and note content, reporting whether publication needs a new archive.
fn finalize_package_content(package: &mut Package, effective: &EffectiveMetadata) -> Result<bool> {
    let mut document = package.xml("word/document.xml")?;
    let mut styles = package.xml("word/styles.xml")?;
    if effective.pmt_settings.fields().docx_footer.is_some()
        || effective.pmt_settings.fields().docx_header.is_some()
    {
        footer::apply(package, &mut document, &styles, &effective.pmt_settings)?;
    }
    let directives_changed = directives::apply(package, &mut document, &mut styles)?;
    let captions_changed = directives::index_captions(&mut document, &mut styles)?;
    let paragraph_format_changed = paragraph_format::apply(package, &mut document, &styles)?;
    if note_marks::apply(package, &mut document)?
        || directives_changed
        || captions_changed
        || paragraph_format_changed
        || effective.pmt_settings.fields().docx_footer.is_some()
        || effective.pmt_settings.fields().docx_header.is_some()
    {
        package.set_xml("word/document.xml", &document);
        package.set_xml("word/styles.xml", &styles);
        Ok(true)
    } else {
        Ok(false)
    }
}
