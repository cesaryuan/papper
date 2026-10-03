//! Command definitions and dispatch for both native executable names.

mod commands;
mod images;
mod reply;
mod tools;

use clap::{Args, Parser, Subcommand, ValueEnum};
use std::path::PathBuf;

/// Parse commands natively without loading document engines during help/version.
#[derive(Debug, Parser)]
#[command(name = "papper", bin_name = "papper", version = env!("CARGO_PKG_VERSION"), about = "Papper command-line interface.", disable_help_subcommand = true)]
pub struct Cli {
    #[command(subcommand)]
    pub command: Option<CliCommand>,
}

/// Keep product command names stable while hiding internal service entry points.
#[derive(Debug, Subcommand)]
pub enum CliCommand {
    /// Initialize a manuscript project from the packaged template.
    Init(InitArgs),
    /// Validate the native engine or prepare managed tools.
    Setup(SetupArgs),
    /// Build a manuscript target.
    Build(BuildArgs),
    /// Convert a DOCX document to Markdown.
    Convert(ConvertArgs),
    /// Build a reply to reviewers.
    BuildReply(ReplyArgs),
    /// Remove generated outputs, work files, and reusable project caches.
    Clean(CleanArgs),
    /// Diagnose available tools and project resources.
    Doctor(VerboseArgs),
    #[command(name = "__server", hide = true)]
    NativeServer(ServerArgs),
    #[command(name = "__decode_docx", hide = true)]
    NativeDecode(DecodeArgs),
    #[command(name = "__pdf_extract", hide = true)]
    NativePdf(PdfArgs),
    #[command(name = "__update", hide = true)]
    NativeUpdate,
}

/// Common logging option with no ambient environment-variable input.
#[derive(Debug, Default, Args)]
pub struct VerboseArgs {
    #[arg(long)]
    pub verbose: bool,
}

/// Existing manuscript output targets.
#[derive(Clone, Copy, Debug, Default, ValueEnum, PartialEq, Eq)]
pub enum BuildTarget {
    #[default]
    Docx,
    Latex,
    Html,
    Json,
}

/// Preserve explicit option presence rather than filling project defaults during parsing.
#[derive(Debug, Args)]
pub struct BuildArgs {
    #[arg(value_enum, default_value = "docx")]
    pub target: BuildTarget,
    pub markdown: Option<PathBuf>,
    #[arg(short = 'm', long = "manuscript")]
    pub manuscript: Option<PathBuf>,
    #[arg(long)]
    pub style_file: Option<PathBuf>,
    #[arg(short = 'o', long = "output-file")]
    pub output: Option<PathBuf>,
    #[arg(long)]
    pub resource_path: Option<String>,
    #[arg(long, num_args = 0..=1, default_missing_value = "true")]
    pub mathtype: Option<String>,
    #[arg(long, conflicts_with = "mathtype")]
    pub no_mathtype: bool,
    #[arg(long)]
    pub lang: Option<String>,
    #[arg(long, hide = true)]
    pub reference_doc: Option<PathBuf>,
    #[arg(long)]
    pub start_server: bool,
    #[arg(long, default_value = "127.0.0.1")]
    pub server_host: String,
    #[arg(long, default_value_t = 3030)]
    pub server_port: u16,
    #[arg(long)]
    pub server_command: Option<String>,
    #[command(flatten)]
    pub logging: VerboseArgs,
}

/// Native template initialization options.
#[derive(Debug, Args)]
pub struct InitArgs {
    #[arg(default_value = ".")]
    pub directory: PathBuf,
    #[arg(long)]
    pub force: bool,
    #[arg(long)]
    pub merge: bool,
    #[arg(long)]
    pub setup: bool,
    #[arg(long)]
    pub lang: Option<String>,
    #[command(flatten)]
    pub logging: VerboseArgs,
}

/// Managed tool validation or explicit refresh.
#[derive(Debug, Args)]
pub struct SetupArgs {
    #[arg(long)]
    pub force: bool,
    #[command(flatten)]
    pub logging: VerboseArgs,
}

/// Keep cleanup confined to a project's generated output directory.
#[derive(Debug, Args)]
pub struct CleanArgs {
    #[arg(short = 'o', long, default_value = "output")]
    pub output_dir: PathBuf,
    #[command(flatten)]
    pub logging: VerboseArgs,
}

/// DOCX conversion input and explicit Markdown destination.
#[derive(Debug, Args)]
pub struct ConvertArgs {
    #[arg(default_value = "manuscript.docx")]
    pub input: PathBuf,
    #[arg(short = 'o', long = "output-dir", default_value = "converted")]
    pub output_dir: PathBuf,
    #[command(flatten)]
    pub logging: VerboseArgs,
}

/// Preserve the existing reply command's manuscript, line-source and output options.
#[derive(Debug, Args)]
pub struct ReplyArgs {
    pub markdown: PathBuf,
    #[arg(long, default_value = "manuscript.md")]
    pub reply_manuscript: PathBuf,
    #[arg(long, default_value = "manuscript.md")]
    pub manuscript_line_source: PathBuf,
    #[arg(long, default_value = "markdown")]
    pub from_format: String,
    #[arg(long, hide = true)]
    pub reference_doc: Option<PathBuf>,
    #[arg(
        short = 'o',
        long = "output-file",
        default_value = "output/docx/<reply-name>.docx"
    )]
    pub output: String,
    #[command(flatten)]
    pub logging: VerboseArgs,
}

/// Private console-free HTTP child configuration.
#[derive(Debug, Args)]
pub struct ServerArgs {
    #[arg(long)]
    pub config: PathBuf,
    #[arg(long, default_value = "127.0.0.1")]
    pub host: String,
    #[arg(long, default_value_t = 3030)]
    pub port: u16,
}

/// Decode MathType objects for a standalone retained Lua DOCX import filter.
#[derive(Debug, Args)]
pub struct DecodeArgs {
    #[arg(required = true)]
    pub documents: Vec<PathBuf>,
}

/// Isolate the native PDF engine from malformed-document failures in a child process.
#[derive(Debug, Args)]
pub struct PdfArgs {
    pub input: PathBuf,
}

/// Parse and dispatch without forwarding any product path to Python.
pub fn run() -> i32 {
    let cli = match Cli::try_parse() {
        Ok(cli) => cli,
        Err(error) => {
            let code = error.exit_code();
            let _ = error.print();
            return code;
        }
    };
    let Some(command) = cli.command else {
        println!("Use `papper --help` to see available commands.");
        return 1;
    };
    let public_command = !matches!(
        &command,
        CliCommand::NativeServer(_)
            | CliCommand::NativePdf(_)
            | CliCommand::NativeDecode(_)
            | CliCommand::NativeUpdate
    );
    match commands::dispatch(command) {
        Ok(()) => {
            if public_command {
                tools::notify_and_schedule_update_check(env!("CARGO_PKG_VERSION"));
            }
            0
        }
        Err(error) => {
            eprintln!("[ERROR] {error:#}");
            1
        }
    }
}
