//! Exercise machine exports outside a project and verify emitted authoring choices against runtime parsing.

use anyhow::Result;
use papper_core::metadata::PmtSettings;
use serde_json::{Value, json};
use std::process::Command;

/// Run configuration queries in an empty directory with deliberately unavailable build resources.
fn describe(arguments: &[&str]) -> Result<std::process::Output> {
    let directory = tempfile::tempdir()?;
    let result = Command::new(env!("CARGO_BIN_EXE_papper"))
        .args(["config", "describe"])
        .args(arguments)
        .current_dir(directory.path())
        .env("PMT_STYLE_FILE", "missing-style.yml")
        .env("PMT_PANDOC", "missing-engine.exe")
        .output()?;
    assert_eq!(std::fs::read_dir(directory.path())?.count(), 0);
    Ok(result)
}

/// A query must be project-independent, emit clean JSON, and suggest settings the actual parser accepts.
#[test]
fn machine_export_is_independent_of_project_and_engine() -> Result<()> {
    let result = describe(&["--format", "json"])?;
    assert!(
        result.status.success(),
        "{}",
        String::from_utf8_lossy(&result.stderr)
    );
    assert!(result.stderr.is_empty());
    let export: Value = serde_json::from_slice(&result.stdout)?;
    assert_eq!(result.stdout, describe(&[])?.stdout);
    for (field, schema) in export["schemas"]["style"]["properties"]
        .as_object()
        .unwrap()
    {
        if let Some(choices) = schema["enum"].as_array() {
            for choice in choices {
                let settings = json!({field: choice});
                PmtSettings::from_mapping(settings.as_object().unwrap())?;
            }
        }
    }
    let template: Value =
        serde_yaml::from_str(export["templates"]["styleProject"].as_str().unwrap())?;
    PmtSettings::from_mapping(template.as_object().unwrap())?;
    let footer = &export["schemas"]["style"]["properties"]["docxFooter"];
    for alignment in footer["properties"]["alignment"]["enum"]
        .as_array()
        .unwrap()
    {
        PmtSettings::from_mapping(
            json!({"docxFooter": {"content": "", "alignment": alignment}})
                .as_object()
                .unwrap(),
        )?;
    }
    Ok(())
}

/// Unsupported formats must fail with no partial JSON that an editor could mistake for a valid export.
#[test]
fn unsupported_formats_do_not_emit_contracts() -> Result<()> {
    let result = describe(&["--format", "yaml"])?;
    assert!(!result.status.success());
    assert!(result.stdout.is_empty());
    assert!(!result.stderr.is_empty());
    Ok(())
}
