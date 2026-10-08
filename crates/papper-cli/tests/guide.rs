//! Verify public guide navigation on rendered command output, including Markdown example boundaries.

use std::process::{Command, Output};

/// Run the installed-style CLI without asynchronous update messages or network work.
fn guide(arguments: &[&str]) -> Output {
    Command::new(env!("CARGO_BIN_EXE_papper"))
        .arg("guide")
        .args(arguments)
        .env("PAPPER_DISABLE_UPDATE_CHECK", "1")
        .output()
        .expect("guide must run")
}

/// Decode successful public output while retaining stderr on failures.
fn output(arguments: &[&str]) -> String {
    let result = guide(arguments);
    assert!(
        result.status.success(),
        "{}",
        String::from_utf8_lossy(&result.stderr)
    );
    String::from_utf8(result.stdout).expect("guide output is UTF-8")
}

/// Ensure indexes avoid examples and topic reads stop before the next sibling section.
#[test]
fn guides_are_compact_and_topics_preserve_complete_examples() {
    let syntax = output(&["syntax"]);
    let full = output(&["syntax", "--full"]);
    assert!(syntax.len() < full.len() / 5);
    assert!(!syntax.contains("```"));
    assert!(!syntax.contains("Methods {#sec:methods}"));
    let equations = output(&["syntax", "equations"]);
    assert!(equations.contains("\\hat{\\mathbf{C}}"));
    assert!(!equations.contains("## Images"));
    assert!(equations.contains("papper guide style mathtype-equations"));
    assert_eq!(equations, output(&["syntax", "Equations"]));
    let style = output(&["style"]);
    assert!(style.len() < output(&["style", "--full"]).len() / 5);
    assert!(!style.contains("Optional rasterization control"));
    let both = output(&[]);
    assert!(both.contains(&syntax));
    assert!(both.contains(&style));
}

/// Verify parent navigation and child retrieval without leaking sibling examples.
#[test]
fn nested_topics_support_progressive_reading() {
    let parent = output(&["syntax", "advanced-table-formatting"]);
    assert!(parent.contains("advanced-table-formatting/cell-merging"));
    assert!(!parent.contains("```"));
    let child = output(&["syntax", "advanced-table-formatting/cell-merging"]);
    assert!(child.contains("!<!"));
    assert!(child.contains("!^!"));
    assert!(!child.contains("### 3. Auto-fit Tables"));
    assert_eq!(child, output(&["syntax", "Cell Merging"]));
    let latex = output(&["syntax", "optional-latex-source-configuration"]);
    assert!(latex.contains("Use the IEEE journal document class"));
    assert!(!latex.contains("documentclass: IEEEtran"));
}

/// Invalid topics must fail explicitly instead of silently returning the entire guide.
#[test]
fn invalid_topic_requests_do_not_dump_guides() {
    for arguments in [
        vec!["syntax", "missing-topic"],
        vec!["all", "equations"],
        vec!["style", "docx-text-styles", "--full"],
    ] {
        let result = guide(&arguments);
        assert!(!result.status.success());
        assert!(result.stdout.is_empty());
    }
    let error = guide(&["syntax", "missing-topic"]);
    assert!(String::from_utf8_lossy(&error.stderr).contains("papper guide syntax"));
}
