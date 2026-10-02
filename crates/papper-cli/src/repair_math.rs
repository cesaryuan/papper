//! Restore math escaping in Word-derived Markdown while preserving surrounding text.
//!
//! `papper repair-math input.md -o output.md` replaces the former template Python
//! helper. Without `-o` it writes to stdout; an explicit output is published
//! atomically, including when input and output name the same file.

use anyhow::{Context, Result};
use regex::{Captures, Regex};
use std::sync::LazyLock;

use crate::RepairMathArgs;

static SPANS: LazyLock<[(Regex, &str, &str); 3]> = LazyLock::new(|| {
    [
        (
            Regex::new(r"(?s)\\\$(.+?)\\\$").expect("literal dollar regex"),
            "$",
            "$",
        ),
        (
            Regex::new(r"(?s)\\\((.+?)\\\)").expect("literal parenthesis regex"),
            r"\(",
            r"\)",
        ),
        (
            Regex::new(r"(?s)\\\[(.+?)\\\]").expect("literal bracket regex"),
            r"\[",
            r"\]",
        ),
    ]
});

/// Undo Word's extra escapes only inside recognized math spans and blocks.
fn restore(text: &str) -> String {
    let mut restored = text.to_string();
    for (pattern, opening, closing) in SPANS.iter() {
        restored = pattern
            .replace_all(&restored, |captures: &Captures<'_>| {
                let mut content = captures[1].to_string();
                for (from, to) in [
                    (r"\\\\", r"\\"),
                    (r"\{", "{"),
                    (r"\}", "}"),
                    (r"\_", "_"),
                    (r"\^", "^"),
                    (r"\%", "%"),
                    (r"\#", "#"),
                    (r"\&", "&"),
                ] {
                    content = content.replace(from, to);
                }
                format!(
                    "{opening}{}{closing}",
                    content.split_whitespace().collect::<Vec<_>>().join(" ")
                )
            })
            .into_owned();
    }
    restored
}

/// Read the entire source before publishing so in-place repair preserves data on failure.
pub fn run(args: RepairMathArgs) -> Result<()> {
    use std::io::Write;
    let text = std::fs::read_to_string(&args.input)
        .with_context(|| format!("Could not read {}", args.input.display()))?;
    let fixed = restore(&text);
    if let Some(output) = args.output {
        papper_core::paths::atomic_write(&output, fixed.as_bytes())?;
    } else {
        std::io::stdout().lock().write_all(fixed.as_bytes())?;
    }
    Ok(())
}
