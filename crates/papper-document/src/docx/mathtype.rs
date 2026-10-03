//! Bind hidden TeX to Word equations, generate cached MathType parts, and publish
//! complete OPC packages atomically. The directly linked Rust core and C# SDK
//! helper own equation conversion; this module never automates the user's Word.

mod backend;
mod fonts;

use super::package::Package;
use super::xml::{Element, Node};
use anyhow::{Context, Result, bail, ensure};
use papper_core::metadata::EffectiveMetadata;
use papper_core::resources::ResourcePaths;
use std::collections::BTreeMap;
use std::path::Path;

const MARKER: &str = "MTLATEX:";
const W: &str = "http://schemas.openxmlformats.org/wordprocessingml/2006/main";
const R: &str = "http://schemas.openxmlformats.org/officeDocument/2006/relationships";
const V: &str = "urn:schemas-microsoft-com:vml";
const O: &str = "urn:schemas-microsoft-com:office:office";
const W14: &str = "http://schemas.microsoft.com/office/word/2010/wordml";

/// Report partial conversion explicitly while retaining every unsuccessful OMML equation.
#[derive(Debug, Default)]
pub struct MathTypeConversionResult {
    pub total: usize,
    pub converted: usize,
    pub failures: usize,
    pub cache_hits: usize,
}

/// Hold stable original-tree paths so nested formulas cannot shift later replacements.
pub(super) struct Binding {
    pub latex: String,
    pub style: String,
    pub marker: Vec<usize>,
    pub math: Vec<usize>,
    pub size: Option<f64>,
}

/// Hold all validated artifacts together; failures never inject a partial object.
pub(super) struct Equation {
    pub ole: Vec<u8>,
    pub wmf: Vec<u8>,
    pub metadata: serde_json::Value,
    pub cache_hit: bool,
}

/// Check the selected native/SDK dependencies before choosing the Pandoc layout.
pub fn check_mathtype_available(
    resources: &ResourcePaths,
    effective: &EffectiveMetadata,
) -> Result<()> {
    backend::check(resources, effective)
}

/// Replace marker-bound equations safely, preserving failed formulas and unrelated parts.
pub fn convert_marked_docx(
    input: &Path,
    output: &Path,
    resources: &ResourcePaths,
    effective: &EffectiveMetadata,
    project_dir: &Path,
) -> Result<MathTypeConversionResult> {
    convert_marked_docx_with_work_dir(input, output, resources, effective, project_dir, None)
}

/// Retain debug sidecars in an explicitly configured work directory without global state changes.
pub fn convert_marked_docx_with_work_dir(
    input: &Path,
    output: &Path,
    resources: &ResourcePaths,
    effective: &EffectiveMetadata,
    project_dir: &Path,
    work_dir: Option<&Path>,
) -> Result<MathTypeConversionResult> {
    let mut package = Package::open(input)?;
    let mut document = package.xml("word/document.xml")?;
    let styles = package.xml("word/styles.xml")?;
    let mut bindings = bindings(&document)?;
    fonts::resolve_sizes(&document, &styles, &mut bindings);
    let mut report = MathTypeConversionResult {
        total: bindings.len(),
        ..Default::default()
    };
    if bindings.is_empty() {
        // An equation-free document needs no converter and remains byte-identical.
        if input != output {
            papper_core::paths::atomic_write(output, &std::fs::read(input)?)?;
        }
        return Ok(report);
    }
    let mut generator = backend::Generator::new(resources, effective, project_dir, work_dir)?;
    let mut rels = package.xml("word/_rels/document.xml.rels")?;
    let mut content_types = package.xml("[Content_Types].xml")?;
    let mut rid = rels
        .elements()
        .filter_map(|rel| rel.attr("Id"))
        .filter_map(|id| {
            id.strip_prefix("rId")
                .and_then(|suffix| suffix.parse::<u64>().ok())
        })
        .max()
        .unwrap_or(0)
        + 1;
    let mut replacements = BTreeMap::new();
    for (offset, binding) in bindings.iter().enumerate() {
        let index = offset + 1;
        replacements.insert(binding.marker.clone(), None);
        match generator.generate(binding, index) {
            Ok(equation) => {
                let image_name = format!("word/media/mathtype_formula_{index}.wmf");
                let ole_name = format!("word/embeddings/mathtype_formula_{index}.bin");
                ensure!(
                    !package.entries.contains_key(&image_name)
                        && !package.entries.contains_key(&ole_name),
                    "Generated MathType part already exists for equation {index}"
                );
                let image_rid = format!("rId{rid}");
                let ole_rid = format!("rId{}", rid + 1);
                rid += 2;
                let object = object_run(
                    &equation,
                    &image_rid,
                    &ole_rid,
                    index,
                    binding.style == "inline",
                )?;
                replacements.insert(binding.math.clone(), Some(object));
                relationship(
                    &mut rels,
                    &image_rid,
                    "image",
                    image_name.trim_start_matches("word/"),
                );
                relationship(
                    &mut rels,
                    &ole_rid,
                    "oleObject",
                    ole_name.trim_start_matches("word/"),
                );
                package.entries.insert(image_name, equation.wmf);
                package.entries.insert(ole_name, equation.ole);
                report.converted += 1;
                report.cache_hits += usize::from(equation.cache_hit);
            }
            Err(error) => {
                // Keep aligned binding slots: a later success must never replace this formula.
                report.failures += 1;
                eprintln!(
                    "[WARN] MathType equation {index} failed; retaining the Word equation: {error:#}"
                );
            }
        }
    }
    edit(&mut document, &mut Vec::new(), &replacements);
    for (prefix, uri) in [("w", W), ("r", R), ("v", V), ("o", O), ("w14", W14)] {
        document.set(&format!("xmlns:{prefix}"), uri);
    }
    content_type(
        &mut content_types,
        "bin",
        "application/vnd.openxmlformats-officedocument.oleObject",
    );
    content_type(&mut content_types, "wmf", "image/x-wmf");
    package.set_xml("word/document.xml", &document);
    package.set_xml("word/_rels/document.xml.rels", &rels);
    package.set_xml("[Content_Types].xml", &content_types);
    package.save_preserving(output)?;
    eprintln!(
        "[mathtype] converted={}, retained={}, cache hits={}",
        report.converted, report.failures, report.cache_hits
    );
    Ok(report)
}

/// Parse a hidden run marker without accepting malformed or unknown equation kinds.
fn marker(run: &Element) -> Result<Option<(String, String)>> {
    if run.name != "w:r" {
        return Ok(None);
    }
    let text = run.text();
    let Some(rest) = text.strip_prefix(MARKER) else {
        return Ok(None);
    };
    let (style, latex) = rest.split_once(':').context("Malformed MathType marker")?;
    ensure!(
        matches!(style, "inline" | "display"),
        "Unknown MathType marker kind: {style}"
    );
    Ok(Some((style.into(), latex.trim().into())))
}

/// Bind document-order marker signals to the immediately following top-level OMML.
fn bindings(root: &Element) -> Result<Vec<Binding>> {
    let mut result = Vec::new();
    let mut pending = None;
    signals(root, &mut Vec::new(), &mut pending, &mut result)?;
    ensure!(
        pending.is_none(),
        "Trailing MathType marker was not followed by OMML"
    );
    Ok(result)
}

/// Skip nested OMML while retaining markers from paragraphs, tables, and wrappers.
fn signals(
    element: &Element,
    path: &mut Vec<usize>,
    pending: &mut Option<(String, String, Vec<usize>)>,
    result: &mut Vec<Binding>,
) -> Result<()> {
    if matches!(element.name.as_str(), "m:oMath" | "m:oMathPara") {
        if let Some((style, latex, marker)) = pending.take() {
            result.push(Binding {
                latex,
                style,
                marker,
                math: path.clone(),
                size: None,
            });
        }
        return Ok(());
    }
    if let Some((style, latex)) = marker(element)? {
        if pending.is_some() {
            bail!("MathType marker was not followed by OMML");
        }
        *pending = Some((style, latex, path.clone()));
        return Ok(());
    }
    for (index, node) in element.children.iter().enumerate() {
        if let Node::Element(child) = node {
            path.push(index);
            signals(child, path, pending, result)?;
            path.pop();
        }
    }
    Ok(())
}

/// Apply all changes using original child indices, then prune marker-only paragraphs.
fn edit(
    element: &mut Element,
    path: &mut Vec<usize>,
    replacements: &BTreeMap<Vec<usize>, Option<Element>>,
) -> bool {
    let mut removed_marker = false;
    let mut children = Vec::new();
    for (index, node) in std::mem::take(&mut element.children)
        .into_iter()
        .enumerate()
    {
        path.push(index);
        if let Some(replacement) = replacements.get(path) {
            if let Some(replacement) = replacement {
                children.push(Node::Element(replacement.clone()));
            } else {
                removed_marker = true;
            }
        } else if let Node::Element(mut child) = node {
            if !edit(&mut child, path, replacements) {
                children.push(Node::Element(child));
            }
        } else {
            children.push(node);
        }
        path.pop();
    }
    element.children = children;
    element.name == "w:p" && removed_marker && element.elements().all(|child| child.name == "w:pPr")
}

/// Append a package relationship using the parent's namespace qualification.
fn relationship(root: &mut Element, id: &str, kind: &str, target: &str) {
    let name = root
        .name
        .rsplit_once(':')
        .map(|(prefix, _)| format!("{prefix}:Relationship"))
        .unwrap_or("Relationship".into());
    root.push(Element::with_attrs(
        &name,
        &[
            ("Id", id),
            ("Type", &format!("{R}/{kind}")),
            ("Target", target),
        ],
    ));
}

/// Resolve newly generated binary parts without changing unrelated content declarations.
fn content_type(root: &mut Element, extension: &str, content: &str) {
    if let Some(item) = root
        .elements_mut()
        .find(|element| element.attr("Extension") == Some(extension))
    {
        item.set("ContentType", content);
        return;
    }
    let name = root
        .name
        .rsplit_once(':')
        .map(|(prefix, _)| format!("{prefix}:Default"))
        .unwrap_or("Default".into());
    root.children.insert(
        0,
        Node::Element(Element::with_attrs(
            &name,
            &[("Extension", extension), ("ContentType", content)],
        )),
    );
}

/// Recover exact preview dimensions from a valid Aldus placeable WMF header.
pub(super) fn wmf_size(bytes: &[u8]) -> Result<(f64, f64)> {
    ensure!(
        bytes.len() >= 22 && bytes[..4] == [0xd7, 0xcd, 0xc6, 0x9a],
        "Invalid placeable WMF preview"
    );
    let signed = |offset| i16::from_le_bytes([bytes[offset], bytes[offset + 1]]) as i32;
    let inch = u16::from_le_bytes([bytes[14], bytes[15]]) as f64;
    ensure!(inch > 0.0, "WMF preview has zero resolution");
    let width = (signed(10) - signed(6)) as f64 * 72.0 / inch;
    let height = (signed(12) - signed(8)) as f64 * 72.0 / inch;
    ensure!(
        width > 0.0 && height > 0.0,
        "WMF preview has empty dimensions"
    );
    Ok((width, height))
}

/// Render a minimal Word-native MathType shell with fresh preview and OLE identities.
fn object_run(
    equation: &Equation,
    image_rid: &str,
    ole_rid: &str,
    index: usize,
    inline: bool,
) -> Result<Element> {
    let (width, height) = wmf_size(&equation.wmf)?;
    let shape_id = format!("_x0000_i{}", 3000 + index);
    let mut run = Element::new("w:r");
    if inline {
        let baseline = equation.metadata["mathtype"]["baseline_from_bottom_pt"]
            .as_f64()
            .filter(|value| value.is_finite() && *value >= 0.0);
        let position = baseline
            .map(|value| -(value * 2.0).round_ties_even() as i64)
            .unwrap_or(-((height * 10.0 / 15.85).round_ties_even() as i64).max(1));
        run.word("w:rPr").word("w:position").set("w:val", position);
    }
    let mut object = Element::with_attrs("w:object", &[("w14:anchorId", "5FA61A83")]);
    object.set(
        "w:dxaOrig",
        ((width * 20.0).round_ties_even() as i64).max(1),
    );
    object.set(
        "w:dyaOrig",
        ((height * 20.0).round_ties_even() as i64).max(1),
    );
    let mut shape_type = Element::with_attrs(
        "v:shapetype",
        &[
            ("id", "_x0000_t75"),
            ("coordsize", "21600,21600"),
            ("o:spt", "75"),
            ("o:preferrelative", "t"),
            ("path", "m@4@5l@4@11@9@11@9@5xe"),
            ("filled", "f"),
            ("stroked", "f"),
        ],
    );
    shape_type.push(Element::with_attrs("v:stroke", &[("joinstyle", "miter")]));
    let mut formulas = Element::new("v:formulas");
    for formula in [
        "if lineDrawn pixelLineWidth 0",
        "sum @0 1 0",
        "sum 0 0 @1",
        "prod @2 1 2",
        "prod @3 21600 pixelWidth",
        "prod @3 21600 pixelHeight",
        "sum @0 0 1",
        "prod @6 1 2",
        "prod @7 21600 pixelWidth",
        "sum @8 21600 0",
        "prod @7 21600 pixelHeight",
        "sum @10 21600 0",
    ] {
        formulas.push(Element::with_attrs("v:f", &[("eqn", formula)]));
    }
    shape_type.push(formulas);
    shape_type.push(Element::with_attrs(
        "v:path",
        &[
            ("o:extrusionok", "f"),
            ("gradientshapeok", "t"),
            ("o:connecttype", "rect"),
        ],
    ));
    shape_type.push(Element::with_attrs(
        "o:lock",
        &[("v:ext", "edit"), ("aspectratio", "t")],
    ));
    object.push(shape_type);
    let point = |value: f64| {
        format!("{value:.2}")
            .trim_end_matches('0')
            .trim_end_matches('.')
            .to_owned()
            + "pt"
    };
    let style = format!("width:{};height:{}", point(width), point(height));
    let mut shape = Element::with_attrs(
        "v:shape",
        &[
            ("id", &shape_id),
            ("type", "#_x0000_t75"),
            ("style", &style),
            ("o:ole", ""),
        ],
    );
    shape.push(Element::with_attrs(
        "v:imagedata",
        &[("r:id", image_rid), ("o:title", "")],
    ));
    object.push(shape);
    object.push(Element::with_attrs(
        "o:OLEObject",
        &[
            ("Type", "Embed"),
            ("ProgID", "Equation.DSMT4"),
            ("ShapeID", &shape_id),
            ("DrawAspect", "Content"),
            ("ObjectID", &format!("_{}", 1841932809 + index)),
            ("r:id", ole_rid),
        ],
    ));
    run.push(object);
    Ok(run)
}
