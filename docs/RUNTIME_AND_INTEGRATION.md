# Runtime and editor integration

[Back to the README](../README.md) · [Development](../DEVELOPMENT.md) · [Worker implementation](../scripts/pandoc-server/README.md)

These notes cover runtime discovery, caches, resource handling, and the local HTML API for developers integrating Papper with editors. For manuscript authoring, start with [syntax](manuscript-syntax.md) and [style configuration](style-configuration.md).

## Bundled engine, state, and resources

Platform wheels use the bundled `pmt-pandoc-worker` for all Pandoc conversions,
including DOCX/LaTeX/HTML/JSON builds, reviewer replies, and DOCX imports. Ordinary
arguments use Pandoc's CLI; the HTML service uses the same binary's private worker
mode. The standard `pandoc-crossref` filter runs in-process. `papper setup` and
`papper init --setup` validate this engine locally, even with `--force`; they do
not download additional Pandoc or crossref executables. `papper doctor` reports
the linked versions. Lua filters and custom external JSON filters remain supported.

Source checkouts prefer a binary built under `scripts/pandoc-server/dist-newstyle`,
then a worker installed in `~/.papper/tools/bin`. Rebuild older workers to enable
the CLI. An unbuilt checkout retains the legacy PATH/managed-tool fallback,
requiring Pandoc 3.11+ and a separate crossref; it can install shared copies into
`~/.papper/tools` when missing. An unusable bundled engine fails explicitly rather
than silently downloading a different conversion engine.
Reusable caches and persistent build state live under
`~/.papper/projects/<project-id>/cache` and `~/.papper/projects/<project-id>/work`.
The project ID is derived from its absolute directory, so projects cannot overwrite
each other's state. One-build intermediates such as MathType OLE/WMF previews and
line-source conversion files use the system temporary directory and are removed
when the Papper process exits. `papper clean` removes generated outputs and clears
the current project's work and caches, including MathType equation caches and the
legacy `.pandoc-cache`. Existing project-local `.pmt` or `.papper`
directories are not migrated or deleted automatically.
Legacy tool downloads and executable installation use temporary files followed by atomic
replacement, so an interrupted build can be rerun. Invalid cached archives are
discarded and downloaded again once; unusable managed executables are reinstalled.
The six formerly Python-based bundled filters run as Lua inside Pandoc. AST
transforms and resource handling share Lua modules; a separate `papper-svg`
native helper handles SVGZ decompression and PNG rendering when needed. It
carries no embedded runtime archive, and filters do not copy or launch the
complete `papper` executable or exchange whole documents through JSON.
DOCX builds also supply a bundled `rsvg-convert` PNG adapter on the conversion
child's PATH. Pandoc uses it to retain the original SVG and embed a PNG fallback
through `papper-svg`, without requiring a separate librsvg installation or changing
the system PATH. The adapter reuses content-addressed fallback PNGs under the
project cache when the SVG is self-contained; edits to the SVG, renderer, DPI, or
system fonts produce a new entry. SVGs with unresolved external image dependencies
continue through the normal renderer. `docxConvertSvgToPng: true` still replaces
the SVG with PNG when full rasterization is requested.
Fallback entries live in `cache/svg-rsvg` within the project state and are cleared
by `papper clean`. Set `PAPPER_SVG_CACHE=0` to bypass this adapter cache temporarily.
Cache hits skip SVG rendering and per-image system font loading; Pandoc still
launches its converter for each request. Font identity is collected once per DOCX
build. Unreadable or damaged caches fall back to rendering and cannot prevent a
successful document build.

Legacy tool downloads automatically use `HTTPS_PROXY` (or `https_proxy`) when set,
otherwise the configured Windows/macOS system HTTP/HTTPS proxy, and otherwise
a direct connection. This applies to both GitHub release metadata and archive
downloads during `papper build docx`, `papper setup`, and `papper init --setup`.
On Windows, enable your proxy application's **system proxy** option before
building; no extra Papper setting is needed. Papper logs `[TOOLS] Using system proxy for
downloads.` when it selects that route. An explicit `HTTPS_PROXY` takes precedence
over the system setting. PAC scripts and automatic proxy discovery are not
evaluated by this downloader.

## Local HTML service

To start or reuse a local Pandoc HTTP server for an editor or extension while
building HTML, use:

```bash
papper build html --start-server
```

The command checks `http://127.0.0.1:3030/version`, reuses a responsive PMT
server, or starts the working-directory-bound PMT runtime. An explicit generic server can
still be selected with `PMT_PANDOC_SERVER_COMMAND`. The endpoint is printed in the build log and
supports the `/`, `/batch`, and `/version` API. Native state is recorded in
`~/.papper/projects/<project-id>/work/rust-v1/server-state.json`, configuration in
`server-config.json`, and output in the sibling `server.log`.

With `--start-server`, the HTML build itself uses `/convert/raw`, reusing a
persistent Pandoc worker and its prepared citation data. Identical configuration
does not restart the worker. The service reloads changed Markdown headers,
`style.yml`, defaults, Lua filters, bibliography, CSL, and template dependencies,
and applies the normal PMT HTML postprocessing. Markdown may live outside the
working-directory project: `--start-server` still starts or reuses that project's
service, and style/resource lookup retains the source file's directory context.
Builds without this flag use the single-shot CLI build.

Ordinary CLI invocations load only the selected command's settings; root help
still describes every command. HTML typography and margins share metadata
validation with DOCX without loading the Word document backend. The local server
client reuses a proxy-free HTTP opener and initializes certificate verification
only if an actual HTTPS request or redirect occurs.

Set a generic server command only when needed:

```powershell
$env:PMT_PANDOC_SERVER_COMMAND = 'C:\path\to\pandoc-server.exe'
papper build html --start-server --server-port 3030
```

The native engine under `scripts/pandoc-server` links Pandoc 3.12 and
pandoc-crossref 0.3.25, with a dependency-aware citeproc adapter. Platform wheels
include this CLI/worker, so PyPI installations need no GHC/Cabal or separate tool
installation. Worker discovery prefers `PMT_PANDOC_SERVER_WORKER_COMMAND`, then
the bundled engine, a source build, and `~/.papper/tools/bin` for development.
An older manually installed worker cannot shadow a newer wheel's worker.
Server configuration records the package version and resource identity.
For native service upgrades and binary identity checks, see [Development](../DEVELOPMENT.md#source-development-and-validation).

For source-checkout development, with GHC/Cabal available, install it into
the user-managed tool directory with:

```powershell
Push-Location .\scripts\pandoc-server
cabal install . --installdir "$HOME\.papper\tools\bin" --overwrite-policy=always
Pop-Location
```

The PMT wrapper exposes a deliberately smaller API than the generic Pandoc
server. Use `GET /version` for health and `POST /convert` with
`{"path":"manuscript.md"}` for one result (the path defaults to
`manuscript.md` when omitted), or `POST /batch` with an array of
`{"path": ...}` objects. Relative paths resolve from the project that started
the server; absolute paths and relative paths to external Markdown are also accepted.
The response contains the generated HTML, after the same PMT HTML
post-processing used by `papper build html`, together with stage timings and
the HTML result-cache status.

For clients that want to avoid the JSON envelope, `POST /convert/raw` returns
the exact PMT HTML directly as `text/html`. Editors can send
`{"path":"manuscript.md","text":"Current unsaved Markdown"}` to render a buffer
without saving or creating a Markdown mirror. The path may be outside the project
and supplies the style/resource lookup context; omit `text` to read from disk.
An empty `text` renders an empty document. `GET /version` advertises
`source_text: true` and `project_dir` for safe editor reuse. JSON request bodies
are limited to 32 MiB. Conversions parse the complete
current source, preserving global heading IDs, link definitions, and footnotes.
They reuse prepared citation styles and bibliographies; prose-only changes can
also reuse citation evaluation while
applying the resulting citations to the new document. Unchanged successful
HTML is held in a bounded cache. `GET /metrics` distinguishes HTML and citation
cache reuse. Concurrent identical requests share the completed conversion.

Remote CSL parents and other remote citation assets are downloaded to the
project's Papper state directory. The service checks them every five minutes,
using conditional HTTP requests when validators are available; changed content
invalidates prepared citations and HTML. If validation fails after a successful
download, the last valid snapshot remains available and a retry follows after
30 seconds. First-time downloads and periodic validation can still add network
latency. Local bibliography, CSL, filter, and template edits are checked on each
request. Custom filters retain their normal execution path.

Every raw response includes `X-PMT-Cache`, `X-PMT-Citeproc-Cache`, `X-PMT-Mode`,
and `Server-Timing`. The citeproc header is `hit` or `miss` when the native worker
ran, and `skipped` on a whole-HTML cache hit. Timings cover dependency lookup,
native worker and filter stages, output reading, postprocessing, queue wait,
and total request time.

To reproduce warm HTTP benchmarks, including changed prose, changed citation
locators, and unchanged HTML, run from this checkout:

```powershell
uv run python scripts/benchmark_html_server.py --manuscript template/manuscript.md
uv run python scripts/benchmark_html_server.py --manuscript ../1-3d-mesh/manuscript.md
```

The script copies editable inputs into an isolated project, preserves all timed
samples, and verifies final HTML against fresh CLI conversions. Reports under
`output/benchmarks/server/` exclude worker startup and independent CLI checks.
This historical script compares retained Haskell workers through the frozen
Python frontend; it does not measure the native Rust CLI or its HTTP frontend.
Current migration measurements and their executable/configuration boundaries
are recorded in [RUST_MIGRATION_STATUS.md](../RUST_MIGRATION_STATUS.md).
Use `--baseline-worker PATH` to compare an older wrapper through the same HTTP
frontend, or `--no-compare` to measure only the current worker. See
[the native worker notes](../scripts/pandoc-server/README.md) for cache boundaries,
build requirements, profiling, and source attribution.
Recorded warm-request improvements and measurement limits are described in
[the performance note](../scripts/pandoc-server/PERFORMANCE.md).
