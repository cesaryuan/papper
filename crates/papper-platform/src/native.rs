//! Safe, directly linked Rust equation conversion and DOCX equation extraction.

use anyhow::{Context, Result};
pub use latex2wmf::{FormulaStyle, MathFontSelection, SvgBackend, WmfPreview, WmfRenderOptions};
pub use mathtype_rust::EquationPayload;
pub use mathtype_rust::trim_latex_whitespace;
use serde_json::{Value, json};
use sha1::{Digest, Sha1};
use std::collections::BTreeMap;
use std::io::Read;
use std::panic::UnwindSafe;
use std::path::{Path, PathBuf};

/// Build-time identity of equation code, embedded resources, dependency lock, and target.
pub const EQUATION_ENGINE_FINGERPRINT: &str = env!("PAPPER_EQUATION_ENGINE_FINGERPRINT");

/// Preserve per-equation fallback when an upstream renderer panics, as the old C ABI did.
fn conversion_result<T>(operation: impl FnOnce() -> Result<T, String> + UnwindSafe) -> Result<T> {
    std::panic::catch_unwind(operation)
        .map_err(|_| anyhow::anyhow!("Equation converter panicked"))?
        .map_err(anyhow::Error::msg)
}

/// Generate owned OLE/MTEF artifacts without JSON envelopes or allocator-sharing FFI.
pub fn encode_latex(latex: &str, preferences: Option<&Path>) -> Result<EquationPayload> {
    conversion_result(|| mathtype_rust::encode_latex(latex, preferences))
}

/// Recover validated MTEF directly from a generated or cached OLE artifact.
pub fn mtef_from_ole(ole: &[u8]) -> Result<Vec<u8>> {
    conversion_result(|| mathtype_rust::mtef_from_ole(ole))
}

/// Render a typed preview directly in process while retaining upstream error recovery.
pub fn render_wmf(
    latex: &str,
    options: WmfRenderOptions,
    math_font: MathFontSelection<'_>,
) -> Result<WmfPreview> {
    let preview_latex = preview_latex(latex, options.svg_backend)?;
    conversion_result(|| {
        latex2wmf::render_latex_to_wmf_with_fonts(&preview_latex, options, math_font)
    })
}

/// Adapt unsupported preview syntax while keeping the editable OLE source unchanged.
fn preview_latex(latex: &str, backend: SvgBackend) -> Result<String> {
    let text = trim_latex_whitespace(latex);
    let body = if text.starts_with("$$") && text.ends_with("$$") && text.len() >= 4 {
        &text[2..text.len() - 2]
    } else if text.starts_with('$') && text.ends_with('$') && text.len() >= 2 {
        &text[1..text.len() - 1]
    } else {
        text
    };
    let body = trim_latex_whitespace(body);
    // The pinned renderer trims again after stripping delimiters. An empty group
    // protects a final control-space without changing its width or visible content.
    let mut preview = if body.ends_with(char::is_whitespace) {
        format!("{body}{{}}")
    } else {
        body.to_owned()
    };
    if backend == SvgBackend::Typst {
        // MiTeX lacks mspace; its hspace handler accepts em. TeX defines 18mu = 1em.
        // Consume other escape tokens too so an escaped backslash is never rewritten.
        static COMMANDS: std::sync::LazyLock<regex::Regex> = std::sync::LazyLock::new(|| {
            regex::Regex::new(
                r"\\mspace\s*\{\s*([+-]?(?:\d+(?:\.\d*)?|\.\d+))\s*mu\s*\}|\\(?:[a-zA-Z]+|[^a-zA-Z])",
            ).expect("valid preview command pattern")
        });
        preview = COMMANDS
            .replace_all(&preview, |captures: &regex::Captures<'_>| {
                match captures
                    .get(1)
                    .and_then(|value| value.as_str().parse::<f64>().ok())
                {
                    Some(mu) if mu.is_finite() => format!(r"\hspace{{{}em}}", mu / 18.0),
                    _ => captures[0].to_owned(),
                }
            })
            .into_owned();
    }
    Ok(format!("${preview}$"))
}

#[cfg(test)]
mod tests {
    use super::*;

    /// Check actual preview widths so unsupported mspace cannot silently become a zero-width hint.
    #[test]
    fn typst_mspace_preserves_signed_width_and_source() -> Result<()> {
        let options = WmfRenderOptions {
            svg_backend: SvgBackend::Typst,
            formula_style: FormulaStyle::Inline,
            font_size_pt: 12.0,
        };
        let plain = render_wmf("xy", options, MathFontSelection::default())?;
        for (mu, width) in [
            ("18", 12.0),
            ("2", 12.0 / 9.0),
            ("-2.5", -12.0 * 2.5 / 18.0),
        ] {
            let latex = format!(r"x\mspace{{{mu}mu}}y");
            let rendered = render_wmf(&latex, options, MathFontSelection::default())?;
            assert!(
                (rendered.width_pt - plain.width_pt - width).abs() < 0.51,
                "math spacing must match mu width within Word's half-point rounding"
            );
            let encoded = encode_latex(&latex, None)?;
            assert_eq!(ole_to_latex(&encoded.ole)?, latex);
        }
        Ok(())
    }

    /// Exercise public encoding and both preview backends after trailing source normalization.
    #[test]
    fn trailing_control_space_survives_encoding_and_rendering() -> Result<()> {
        for latex in ["x\\ ", "$x\\ $", "$$x\\ $$", "  x\\   \r\n"] {
            let encoded = encode_latex(latex, None)?;
            assert_eq!(ole_to_latex(&encoded.ole)?, "x\\ ");
            for backend in [SvgBackend::Typst, SvgBackend::Ratex] {
                let rendered = render_wmf(
                    latex,
                    WmfRenderOptions {
                        svg_backend: backend,
                        ..Default::default()
                    },
                    MathFontSelection::default(),
                )?;
                assert!(!rendered.wmf.is_empty());
            }
        }
        assert!(
            encode_latex("x\\", None).is_err(),
            "a genuinely dangling slash must still fail"
        );
        Ok(())
    }
}

/// Recover source TeX from OLE, then retain structural fallback for source-free equations.
pub fn ole_to_latex(ole: &[u8]) -> Result<String> {
    conversion_result(|| mathtype_rust::decode_ole(ole, mathtype_rust::DecodeMode::Auto))
}

/// Format small content digests used by cache keys and the Lua equation map.
pub fn hex(bytes: &[u8]) -> String {
    const DIGITS: &[u8] = b"0123456789abcdef";
    let mut result = String::with_capacity(bytes.len() * 2);
    for &byte in bytes {
        result.push(DIGITS[(byte >> 4) as usize] as char);
        result.push(DIGITS[(byte & 15) as usize] as char);
    }
    result
}

/// Resolve OPC relationship paths within an archive without directory traversal.
fn relationship_path(base: &str, target: &str) -> Option<String> {
    let joined = if target.starts_with('/') {
        target.trim_start_matches('/').to_string()
    } else {
        format!("{base}/{target}")
    };
    let mut segments = Vec::new();
    for segment in joined.split('/') {
        match segment {
            "" | "." => (),
            ".." => {
                segments.pop()?;
            }
            segment => segments.push(segment),
        }
    }
    Some(segments.join("/"))
}

/// Read OLE objects referenced as equations, preserving unrelated embedded attachments.
pub fn mathtype_objects(docx: &Path) -> Result<Vec<Vec<u8>>> {
    let mut archive = zip::ZipArchive::new(std::fs::File::open(docx)?)?;
    let mut parts: Vec<_> = archive
        .file_names()
        .filter(|name| {
            name.starts_with("word/") && name.ends_with(".xml") && !name.contains("/_rels/")
        })
        .map(str::to_string)
        .collect();
    parts.sort();
    let mut result = Vec::new();
    for part in parts {
        let mut xml = String::new();
        archive.by_name(&part)?.read_to_string(&mut xml)?;
        let document = roxmltree::Document::parse(&xml)?;
        let ids: Vec<_> = document
            .descendants()
            .filter(|node| {
                node.has_tag_name(("urn:schemas-microsoft-com:office:office", "OLEObject"))
            })
            .filter(|node| {
                node.attribute("ProgID").is_some_and(|program| {
                    program.starts_with("Equation.") || program.contains("MathType")
                })
            })
            .filter_map(|node| {
                node.attribute((
                    "http://schemas.openxmlformats.org/officeDocument/2006/relationships",
                    "id",
                ))
            })
            .map(str::to_string)
            .collect();
        if ids.is_empty() {
            continue;
        }
        let (folder, filename) = part.rsplit_once('/').context("Invalid DOCX XML part")?;
        let mut rels_xml = String::new();
        let Ok(mut rels) = archive.by_name(&format!("{folder}/_rels/{filename}.rels")) else {
            continue;
        };
        rels.read_to_string(&mut rels_xml)?;
        drop(rels);
        let rels = roxmltree::Document::parse(&rels_xml)?;
        for id in ids {
            let target = rels
                .descendants()
                .find(|node| {
                    node.attribute("Id") == Some(id.as_str())
                        && node
                            .attribute("Type")
                            .is_some_and(|kind| kind.ends_with("/oleObject"))
                        && node.attribute("TargetMode") != Some("External")
                })
                .and_then(|node| node.attribute("Target"));
            if let Some(path) = target.and_then(|target| relationship_path(folder, target))
                && let Ok(mut object) = archive.by_name(&path)
            {
                let mut bytes = Vec::new();
                object.read_to_end(&mut bytes)?;
                result.push(bytes);
            }
        }
    }
    Ok(result)
}

/// Decode unique OLE and MTEF-bearing WMF equations, retaining unsupported previews.
pub fn decode_documents(documents: &[PathBuf]) -> Result<BTreeMap<String, Value>> {
    let mut result = BTreeMap::new();
    for document in documents {
        for ole in mathtype_objects(document)? {
            let key = hex(&Sha1::digest(&ole));
            if result.contains_key(&key) {
                continue;
            }
            let value = match ole_to_latex(&ole) {
                Ok(latex) => json!(latex),
                Err(error) => {
                    eprintln!(
                        "[WARN] Could not decode equation {key} in {}: {error}",
                        document.display()
                    );
                    json!(false)
                }
            };
            result.insert(key, value);
        }
        let mut archive = zip::ZipArchive::new(std::fs::File::open(document)?)?;
        let mut media: Vec<_> = archive
            .file_names()
            .filter(|name| {
                name.starts_with("word/media/") && name.to_ascii_lowercase().ends_with(".wmf")
            })
            .map(str::to_string)
            .collect();
        media.sort();
        for part in media {
            let mut wmf = Vec::new();
            archive.by_name(&part)?.read_to_end(&mut wmf)?;
            let key = hex(&Sha1::digest(&wmf));
            if result.contains_key(&key) {
                continue;
            }
            let decoded = conversion_result(|| {
                mathtype_rust::mtef_from_wmf(&wmf)?
                    .map(|mtef| mathtype_rust::decode_mtef(&mtef, mathtype_rust::DecodeMode::Auto))
                    .transpose()
            });
            let value = match decoded {
                Ok(Some(latex)) => json!(latex),
                Ok(None) => continue,
                Err(error) => {
                    eprintln!(
                        "[WARN] Could not decode WMF {part} in {}: {error}",
                        document.display()
                    );
                    json!(false)
                }
            };
            result.insert(key, value);
        }
    }
    Ok(result)
}
