//! Forward Pandoc's SVG fallback conversions to the bundled papper-svg renderer.
//!
//! This native launcher inherits binary stdin/stdout/stderr and passes arguments
//! directly to `papper-svg rsvg-convert`, avoiding shell quoting and text encodings.
//! It uses PAPPER_SVG_RENDERER when configured, otherwise its sibling papper-svg.
//! Papper adds this directory only to its DOCX conversion child's PATH.

use std::path::PathBuf;
use std::process::{Command, ExitStatus};

/// Report launch failures on stderr and preserve the renderer's exit code.
fn main() {
    match run() {
        Ok(status) => std::process::exit(status.code().unwrap_or(1)),
        Err(error) => {
            eprintln!("[papper-rsvg] Cannot launch SVG renderer: {error}");
            std::process::exit(1);
        }
    }
}

/// Forward binary streams unchanged, hiding only the owned child console on Windows.
fn run() -> std::io::Result<ExitStatus> {
    let renderer = match std::env::var_os("PAPPER_SVG_RENDERER") {
        Some(path) => PathBuf::from(path),
        None => std::env::current_exe()?.with_file_name(if cfg!(windows) {
            "papper-svg.exe"
        } else {
            "papper-svg"
        }),
    };
    let mut command = Command::new(renderer);
    command
        .arg("rsvg-convert")
        .args(std::env::args_os().skip(1));
    #[cfg(windows)]
    {
        use std::os::windows::process::CommandExt;
        command.creation_flags(0x08000000);
    }
    command.status()
}
