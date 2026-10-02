//! Preserve the `pmt` executable alias while keeping one bundled native runtime.
//!
//! The alias invokes the neighboring `papper` native executable with unchanged
//! arguments and console handles. Unix replaces the alias process; Windows waits
//! for the child and forwards its exit status. No interpreter or uv wrapper runs.

use std::process::{Command, ExitCode};

/// Forward the compatibility command to its neighboring native executable.
fn main() -> ExitCode {
    match forward() {
        Ok(code) => ExitCode::from(code),
        Err(error) => {
            eprintln!("[ERROR] Could not launch papper: {error}");
            ExitCode::FAILURE
        }
    }
}

/// Preserve arguments, environment, cwd and status across the native alias boundary.
fn forward() -> std::io::Result<u8> {
    let executable = std::env::current_exe()?;
    let papper = executable.with_file_name(if cfg!(windows) {
        "papper.exe"
    } else {
        "papper"
    });
    let mut command = Command::new(papper);
    command.args(std::env::args_os().skip(1));
    #[cfg(unix)]
    {
        use std::os::unix::process::CommandExt;
        Err(command.exec())
    }
    #[cfg(not(unix))]
    {
        Ok(command.status()?.code().unwrap_or(1).clamp(0, 255) as u8)
    }
}
