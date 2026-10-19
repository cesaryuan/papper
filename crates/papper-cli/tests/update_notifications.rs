//! Verify update hints against real executable locations and run the printed upgrade command
//! with only its HTTP boundary replaced, preserving custom paths and explicit upgrade options.

use anyhow::{Context, Result, ensure};
use serde_json::json;
use std::fs;
use std::path::Path;
use std::process::Command;
use std::time::{SystemTime, UNIX_EPOCH};

/// Read a real notification using fresh cached metadata so no background network worker runs.
fn notification(executable: &Path, cache: &Path) -> Result<String> {
    let output = Command::new(executable)
        .args(["guide", "syntax"])
        .env("PAPPER_UPDATE_CACHE", cache)
        .env_remove("PAPPER_DISABLE_UPDATE_CHECK")
        .output()?;
    ensure!(output.status.success(), "Guide failed: {:?}", output);
    let stderr = String::from_utf8(output.stderr)?;
    ensure!(
        stderr.contains("[UPDATE]"),
        "Missing notification: {stderr}"
    );
    Ok(stderr)
}

/// Run the emitted command with a local installer fixture instead of making HTTP requests.
fn run_upgrade_fixture(command: &str, fixture: &Path, capture: &Path) -> Result<()> {
    let mut process = if cfg!(windows) {
        fs::write(
            fixture,
            r#"# Capture installer arguments without changing an installation.
param([string]$InstallDir, [string]$Version, [switch]$NoModifyPath)
[IO.File]::WriteAllText($env:PAPPER_UPGRADE_CAPTURE, (@{directory=$InstallDir; version=$Version; noPath=$NoModifyPath.IsPresent} | ConvertTo-Json -Compress))
"#,
        )?;
        let mut process = Command::new("powershell.exe");
        process.args([
            "-NoProfile",
            "-NonInteractive",
            "-Command",
            // Override only the public script download, retaining real PowerShell parsing.
            r#"function Invoke-RestMethod { <# Serve the local installer fixture through the standard irm alias. #> [IO.File]::ReadAllText($env:PAPPER_UPGRADE_SCRIPT) }; Invoke-Expression $env:PAPPER_UPGRADE_COMMAND"#,
        ]);
        process
    } else {
        fs::write(
            fixture,
            r#"#!/bin/sh
# Capture installer arguments without changing an installation.
set -eu
directory=
version=
no_path=false
while [ "$#" -gt 0 ]; do
    case "$1" in
        --install-dir) directory=$2; shift 2;;
        --version) version=$2; shift 2;;
        --no-modify-path) no_path=true; shift;;
        *) exit 1;;
    esac
done
printf '%s\n%s\n%s\n' "$directory" "$version" "$no_path" > "$PAPPER_UPGRADE_CAPTURE"
"#,
        )?;
        let mut process = Command::new("sh");
        process.args([
            "-c",
            // Override only curl, retaining the pipeline and real shell argument parsing.
            "# Serve the local installer fixture\ncurl() { cat \"$PAPPER_UPGRADE_SCRIPT\"; }; eval \"$PAPPER_UPGRADE_COMMAND\"",
        ]);
        process
    };
    let output = process
        .env("PAPPER_UPGRADE_COMMAND", command)
        .env("PAPPER_UPGRADE_SCRIPT", fixture)
        .env("PAPPER_UPGRADE_CAPTURE", capture)
        // Upgrading must override an old pinned version without relying on ambient defaults.
        .env("PAPPER_VERSION", "0.0.1")
        .env("PAPPER_INSTALL_DIR", "wrong ambient install directory")
        .output()?;
    ensure!(output.status.success(), "Upgrade failed: {:?}", output);
    Ok(())
}

/// Catch incorrect install detection and shell quoting by executing the user's printed command.
#[test]
fn notification_upgrades_the_active_standalone_install_and_retains_uv_hint() -> Result<()> {
    let directory = tempfile::tempdir()?;
    let root = directory
        .path()
        .join("Owner's custom install $PAPPER_WRONG_PATH");
    let bin = root.join("releases/test-release/bin");
    fs::create_dir_all(&bin)?;
    let executable = bin.join(if cfg!(windows) {
        "papper.exe"
    } else {
        "papper"
    });
    fs::copy(env!("CARGO_BIN_EXE_papper"), &executable)?;
    let cache = directory.path().join("update.json");
    let now = SystemTime::now().duration_since(UNIX_EPOCH)?.as_secs_f64();
    fs::write(
        &cache,
        serde_json::to_vec(&json!({
            "latest_version": "9999.0.0", "checked_at": now, "attempted_at": now
        }))?,
    )?;
    // A directory shape alone must not change legacy wheel upgrade guidance.
    let legacy = notification(&executable, &cache)?;
    ensure!(legacy.contains("`uv tool upgrade papper`"));
    fs::write(root.join(".papper-installer"), "papper-installer-v1\n")?;
    let standalone = notification(&executable, &cache)?;
    ensure!(!standalone.contains("uv tool upgrade"));
    let command = standalone
        .split_once("upgrade with `")
        .and_then(|(_, remainder)| remainder.strip_suffix("`\n"))
        .context("Missing runnable upgrade command")?;
    let capture = directory.path().join("captured-arguments");
    run_upgrade_fixture(command, &directory.path().join("installer"), &capture)?;
    let captured = fs::read_to_string(capture)?;
    let expected_root = papper_core::paths::display_path(&fs::canonicalize(&root)?);
    if cfg!(windows) {
        let arguments: serde_json::Value = serde_json::from_str(&captured)?;
        ensure!(arguments["directory"] == expected_root);
        ensure!(arguments["version"] == "latest");
        ensure!(arguments["noPath"] == true);
    } else {
        let arguments: Vec<_> = captured.lines().collect();
        ensure!(arguments == [expected_root.as_str(), "latest", "true"]);
    }
    Ok(())
}
