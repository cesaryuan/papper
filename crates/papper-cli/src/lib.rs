//! Command definitions and dispatch for both native executable names.

mod commands;
mod docx_pipeline;
mod guide;
mod images;
mod reply;
mod tools;

use clap::{Args, Parser, Subcommand, ValueEnum};
use std::path::PathBuf;

/// Parse commands natively without loading document engines during help/version.
#[derive(Debug, Parser)]
#[command(
    name = "papper",
    bin_name = "papper",
    version = env!("CARGO_PKG_VERSION"),
    about = "Papper command-line interface.",
    disable_help_subcommand = true,
    after_help = "Use `papper <COMMAND> --help` for argument descriptions and defaults.\nFor example: papper build --help"
)]
pub struct Cli {
    #[command(subcommand)]
    pub command: Option<CliCommand>,
}

/// Keep product command names stable while hiding internal service entry points.
#[derive(Debug, Subcommand)]
pub enum CliCommand {
    /// Browse manuscript syntax and style configuration topics.
    Guide(GuideArgs),
    /// Initialize a manuscript project from the packaged template.
    Init(InitArgs),
    /// Validate the native engine or prepare managed tools.
    Setup(SetupArgs),
    /// Build a manuscript or a reviewer reply identified by its YAML reply path.
    Build(BuildArgs),
    /// Convert a DOCX document to Markdown.
    Convert(ConvertArgs),
    /// Build a reply to reviewers.
    #[command(hide = true)]
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

/// Select the authoring guide content written to standard output.
#[derive(Clone, Copy, Debug, Default, PartialEq, Eq, ValueEnum)]
pub enum GuideSection {
    /// Browse both Markdown syntax and style configuration.
    #[default]
    All,
    /// Browse Markdown manuscript syntax.
    Syntax,
    /// Browse reusable style configuration.
    Style,
}

/// Choose which authoring guide to print.
#[derive(Debug, Args)]
#[command(
    after_help = "Examples:\n  papper guide syntax\n  papper guide syntax equations\n  papper guide syntax advanced-table-formatting/cell-merging\n  papper guide style --full"
)]
pub struct GuideArgs {
    /// Guide whose topics to list; defaults to both guides.
    #[arg(value_enum, default_value = "all")]
    pub section: GuideSection,
    /// Topic slug or nested path from the index, e.g. equations or author-metadata/format-3-keyed-affiliations.
    pub subsection: Option<String>,
    /// Print the complete guide instead of its topic index.
    #[arg(long, conflicts_with = "subsection")]
    pub full: bool,
}

/// Common logging option with no ambient environment-variable input.
#[derive(Debug, Default, Args)]
pub struct VerboseArgs {
    /// Request detailed diagnostic logging.
    #[arg(long)]
    pub verbose: bool,
}

/// Existing manuscript output targets.
#[derive(Clone, Copy, Debug, Default, ValueEnum, PartialEq, Eq)]
pub enum BuildTarget {
    /// Word document (.docx).
    #[default]
    Docx,
    /// LaTeX source (.tex).
    Latex,
    /// HTML document (.html).
    Html,
    /// Pandoc document AST (.json).
    Json,
}

/// Preserve explicit option presence rather than filling project defaults during parsing.
#[derive(Debug, Args)]
#[command(
    after_help = "Examples:\n  papper build docx paper.md\n  papper build html -m paper.md -o output/paper.html --start-server\n  papper build docx reply.md\nFor HTML/DOCX, a YAML header containing `reply: manuscript.md` selects reviewer-reply builds."
)]
pub struct BuildArgs {
    /// Output format to build.
    #[arg(value_enum, default_value = "docx")]
    pub target: BuildTarget,
    /// Input Markdown file; defaults to PMT_MANUSCRIPT_FILE or manuscript.md.
    pub markdown: Option<PathBuf>,
    /// Input Markdown file instead of the positional MARKDOWN argument.
    #[arg(short = 'm', long = "manuscript")]
    pub manuscript: Option<PathBuf>,
    /// Use this style YAML file instead of discovering style.yml; overrides PMT_STYLE_FILE.
    #[arg(long)]
    pub style_file: Option<PathBuf>,
    /// Destination file; otherwise use the target's output directory and manuscript name.
    #[arg(short = 'o', long = "output-file")]
    pub output: Option<PathBuf>,
    /// Replace resource search paths; separate directories with ';' on Windows or ':' elsewhere.
    #[arg(long)]
    pub resource_path: Option<String>,
    /// Override the line-number source for replies; defaults to the YAML reply path.
    #[arg(long)]
    pub manuscript_line_source: Option<PathBuf>,
    /// DOCX equations: true, false, or auto (follow metadata); no value means true.
    #[arg(long, num_args = 0..=1, default_missing_value = "true")]
    pub mathtype: Option<String>,
    /// Disable MathType conversion and retain native Word equations (DOCX only).
    #[arg(long, conflicts_with = "mathtype")]
    pub no_mathtype: bool,
    /// Override document language, e.g. zh-cn for Chinese defaults (DOCX only).
    #[arg(long)]
    pub lang: Option<String>,
    /// Override the Word reference document (DOCX only).
    #[arg(long, hide = true)]
    pub reference_doc: Option<PathBuf>,
    /// Start or reuse a background conversion server for HTML builds only.
    #[arg(long)]
    pub start_server: bool,
    /// Conversion server host used with --start-server.
    #[arg(long, default_value = "127.0.0.1")]
    pub server_host: String,
    /// Conversion server port used with --start-server.
    #[arg(long, default_value_t = 3030)]
    pub server_port: u16,
    /// Custom server startup command used with --start-server; overrides PMT_PANDOC_SERVER_COMMAND.
    #[arg(long)]
    pub server_command: Option<String>,
    #[command(flatten)]
    pub logging: VerboseArgs,
}

/// Native template initialization options.
#[derive(Debug, Args)]
pub struct InitArgs {
    /// Directory in which to create the manuscript project.
    #[arg(default_value = ".")]
    pub directory: PathBuf,
    /// Overwrite existing template files; cannot be combined with --merge.
    #[arg(long)]
    pub force: bool,
    /// Add missing template files except examples, keeping existing files; incompatible with --force.
    #[arg(long)]
    pub merge: bool,
    /// Also validate the native engine or prepare managed tools after initialization.
    #[arg(long)]
    pub setup: bool,
    /// Template language: zh-cn for Chinese; omit for the English template.
    #[arg(long)]
    pub lang: Option<String>,
    #[command(flatten)]
    pub logging: VerboseArgs,
}

/// Managed tool validation or explicit refresh.
#[derive(Debug, Args)]
pub struct SetupArgs {
    /// Redownload and reinstall managed tools when no native engine is available.
    #[arg(long)]
    pub force: bool,
    #[command(flatten)]
    pub logging: VerboseArgs,
}

/// Keep cleanup confined to a project's generated output directory.
#[derive(Debug, Args)]
pub struct CleanArgs {
    /// Generated output directory to remove; project work and caches are also removed.
    #[arg(short = 'o', long, default_value = "output")]
    pub output_dir: PathBuf,
    #[command(flatten)]
    pub logging: VerboseArgs,
}

/// DOCX conversion input and explicit Markdown destination.
#[derive(Debug, Args)]
pub struct ConvertArgs {
    /// DOCX document to import as Markdown.
    #[arg(default_value = "manuscript.docx")]
    pub input: PathBuf,
    /// Directory for the converted Markdown file and extracted media.
    #[arg(short = 'o', long = "output-dir", default_value = "converted")]
    pub output_dir: PathBuf,
    /// Infer figure/table/equation references from numbers and plain-text reference phrases.
    #[arg(long)]
    pub fuzzy_crossrefs: bool,
    #[command(flatten)]
    pub logging: VerboseArgs,
}

/// Preserve the existing reply command's manuscript, line-source and output options.
#[derive(Debug, Args)]
pub struct ReplyArgs {
    /// Reviewer-reply Markdown file containing response text and placeholders.
    pub markdown: PathBuf,
    /// Manuscript used to resolve section, figure, table, and equation references.
    #[arg(long, default_value = "manuscript.md")]
    pub reply_manuscript: PathBuf,
    /// Source for line-number placeholders: Markdown, DOCX/DOCM, or PDF.
    #[arg(long, default_value = "manuscript.md")]
    pub manuscript_line_source: PathBuf,
    /// Pandoc input format, including any extensions, used to parse Markdown.
    #[arg(long, default_value = "markdown")]
    pub from_format: String,
    /// Override the Word reference document for DOCX output.
    #[arg(long, hide = true)]
    pub reference_doc: Option<PathBuf>,
    /// Destination .docx or .txt file; <reply-name> is the input filename without its extension.
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
    /// JSON configuration file for the project-bound conversion server.
    #[arg(long)]
    pub config: PathBuf,
    /// Address on which the HTTP server listens.
    #[arg(long, default_value = "127.0.0.1")]
    pub host: String,
    /// Port on which the HTTP server listens.
    #[arg(long, default_value_t = 3030)]
    pub port: u16,
    /// Refresh bundled resources after handing off to an upgraded runtime.
    #[arg(long, hide = true)]
    pub refresh_runtime: bool,
}

/// Decode MathType objects for a standalone retained Lua DOCX import filter.
#[derive(Debug, Args)]
pub struct DecodeArgs {
    /// DOCX documents from which to decode embedded MathType equations.
    #[arg(required = true)]
    pub documents: Vec<PathBuf>,
}

/// Isolate the native PDF engine from malformed-document failures in a child process.
#[derive(Debug, Args)]
pub struct PdfArgs {
    /// PDF document from which to extract text and line positions.
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
