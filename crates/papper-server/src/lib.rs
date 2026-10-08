//! Project-bound HTML service, worker lifecycle and dependency-aware caches.

pub mod assets;
mod file_cache;
mod process;
mod service;

pub use process::spawn_background;
pub use service::{
    HtmlBuildRequest, ServerConfig, build_html, run_server, run_server_with_options,
    stop_project_server,
};
