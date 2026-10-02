//! Run the native Papper CLI with Rust configuration and document pipelines.
//!
//! `papper build` dispatches to a single native engine or the project-bound
//! Rust service. The retained Haskell/Lua/C# components remain external native
//! dependencies; no command is forwarded to the legacy Python implementation.

/// Dispatch arguments and return the public command exit code.
fn main() {
    std::process::exit(papper_cli::run());
}
