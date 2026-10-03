//! Embed the package builder's validated runtime archive in native launchers.
//!
//! Source builds use ordinary checkout resources and carry no archive. Release
//! wheels set PAPPER_RUNTIME_ARCHIVE after staging the retained native engines.
//! The generated module embeds bytes plus their content identity, enabling uv's
//! copied binary entry points to locate resources without a Python shim.

use sha2::{Digest, Sha256};
use std::path::PathBuf;

/// Generate immutable resource bytes and a content-addressed installation key.
fn main() {
    // A Lua image cache must change when its separately compiled renderer changes,
    // including source builds that do not carry an embedded runtime archive.
    let root = PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("../..");
    let mut renderer = Sha256::new();
    for relative in [
        "Cargo.lock",
        "crates/papper-svg/Cargo.toml",
        "crates/papper-svg/src/main.rs",
    ] {
        let path = root.join(relative);
        println!("cargo:rerun-if-changed={}", path.display());
        renderer.update(relative.as_bytes());
        renderer.update([0]);
        renderer.update(std::fs::read(&path).expect("Renderer source must be readable"));
    }
    renderer.update(
        std::env::var("TARGET")
            .expect("Cargo sets TARGET")
            .as_bytes(),
    );
    let renderer_id: String = renderer
        .finalize()
        .iter()
        .map(|byte| format!("{byte:02x}"))
        .collect();
    println!("cargo:rustc-env=PAPPER_SVG_RENDERER_ID={renderer_id}");
    println!("cargo:rerun-if-env-changed=PAPPER_RUNTIME_ARCHIVE");
    let output = PathBuf::from(std::env::var_os("OUT_DIR").expect("Cargo sets OUT_DIR"));
    let module = match std::env::var_os("PAPPER_RUNTIME_ARCHIVE") {
        Some(path) => {
            let path = PathBuf::from(path)
                .canonicalize()
                .expect("Runtime archive must exist");
            println!("cargo:rerun-if-changed={}", path.display());
            let bytes = std::fs::read(&path).expect("Runtime archive must be readable");
            let key = Sha256::digest(&bytes)
                .iter()
                .map(|byte| format!("{byte:02x}"))
                .collect::<String>();
            format!(
                "pub const ARCHIVE: &[u8] = include_bytes!({:?});\npub const ID: &str = {key:?};\n",
                path.to_string_lossy()
            )
        }
        None => "pub const ARCHIVE: &[u8] = &[];\npub const ID: &str = \"\";\n".into(),
    };
    std::fs::write(output.join("embedded_resources.rs"), module)
        .expect("Cargo build output must be writable");
}
