# Development

## Build benchmark

Benchmark the current Papper/Pandoc toolchain against `template/manuscript.md`:

```powershell
uv run python scripts/benchmark_build.py
```

The reusable script benchmarks one toolchain per invocation; it does not download
or compare specific Pandoc releases. By default it measures DOCX, HTML and JSON,
honors the manuscript's MathType metadata, and collects seven samples per target
for both empty and prewarmed Papper state, after two unmeasured warmups. Each build
uses a new Python process. Existing manuscript files and user project caches are
untouched; only temporary project state is redirected, leaving managed tools
available. Operating-system filesystem caches are not flushed.

Select the Pandoc executable with `PAPPER_BENCH_PANDOC`, or `--pandoc`. The
executable must be named `pandoc` or `pandoc.exe`. For example, with both versions
already installed, run the same workload twice:

```powershell
$env:PAPPER_BENCH_PANDOC = 'C:/Tools/pandoc-3.11/pandoc.exe'
uv run python scripts/benchmark_build.py --mathtype off --runs 11

$env:PAPPER_BENCH_PANDOC = 'C:/Tools/pandoc-3.12/pandoc.exe'
uv run python scripts/benchmark_build.py --mathtype off --runs 11

Remove-Item Env:PAPPER_BENCH_PANDOC
```

`--mathtype off` measures native Word equations; omit it to benchmark the usual
metadata-driven build, or use `--mathtype on` to request MathType explicitly. Keep
the same Python environment, manuscript, style files, filters, pandoc-crossref,
targets, cache policies and MathType policy when comparing reports. Avoid running
the sessions concurrently. The report records both CLI-resolved and
filter-resolved crossref paths, since managed tools can precede system tools in
Pandoc's filter environment.
For small differences, repeat the sessions in reverse order and inspect both
`pandoc_s` and `process_s`; Python startup and background system load can obscure
the conversion change.

To benchmark another manuscript or a subset of targets:

```powershell
uv run python scripts/benchmark_build.py --manuscript template/manuscript.md --targets docx --targets html --cache-modes warm --runs 11
```

Every setting also accepts its `PAPPER_BENCH_` environment variable; list settings
use JSON, for example `$env:PAPPER_BENCH_TARGETS = '["docx","html"]'`.
Run `uv run python scripts/benchmark_build.py --help` for the full option list.

Each invocation creates a timestamped directory under `output/benchmarks/`, or
under the parent selected with `--output-dir`. It contains `results.json` with
every measured sample, tool versions, executable paths, Python/platform details,
input/style hashes and Pandoc commands; `summary.md` with median, mean, standard
deviation and sample count; one log per build; and the last output of each case.
Failed builds stop the session and retain partial results and diagnostic logs.
No samples are discarded as outliers.

The four timing metrics distinguish process startup from conversion work:

- `process_s`: Python process start through exit, including imports, benchmark
  worker setup/timing export and cleanup.
- `build_s`: Papper CLI parsing and build dispatch, excluding module imports.
- `pandoc_s`: actual Pandoc subprocess execution, including JSON/Lua filters,
  citeproc, resource loading and output writing; tool version probes are excluded.
- `non_pandoc_build_s`: `build_s - pandoc_s`, including metadata preparation,
  tool checks, Python postprocessing and optional MathType conversion.

The controller invokes the public Papper CLI parser and dispatcher but skips the
unrelated PyPI update notification/refresh hook. Neither `uv` startup nor tool
downloads during preflight are timed. Source builds of native helpers can happen
during warmups; their global compiled artifacts are not reset by the cold-cache
policy, so this is not a clean-room native compilation benchmark.

## Build snapshots

The build snapshots cover the manuscript template and focused fixtures for
citations, cross-references and metadata, using both standalone HTML and DOCX.
They also cover `build-reply` output in DOCX and TXT.
DOCX snapshots list every decompressed ZIP entry: canonical XML is split at tag
boundaries for readable diffs, while binary entries retain their SHA-256 and
size. Refreshing snapshots is explicit; review the changes before accepting
them.

Run the checks with:

```bash
uv run pytest tests/test_build_snapshots.py -p no:cacheprovider
```

After an intentional output change, regenerate them with:

```bash
uv run pytest tests/test_build_snapshots.py -p no:cacheprovider --snapshot-update
```

The build snapshots require `pandoc` and `pandoc-crossref` on `PATH`.
Each CLI build runs from its temporary output directory so concurrently running
tests cannot overwrite the same project metadata. DOCX normalization uses that
build directory's cache path while retaining the repository resource paths.
Cache filename hashes derived from absolute SVG source paths are normalized;
embedded image bytes, their SHA-256 hashes, and drawing dimensions remain exact.

Focused syntax fixtures also cover directional table margins and attribute
aliases, selective and whole-table revisions including captions, content-sized
tables, emphasis and revised figure captions, standalone inline math, keyed
author affiliations and custom correspondence text, and per-image SVG
rasterization. The `metadata`
case checks the title in the generated default corresponding-author text.
The DOCX-only `equation_attributes` case covers trailing `revision=true` and
`revision=false`, and labeled and unnumbered equations.
Revised formulas retain their red math runs inside the default equation-layout
tables, while the unchanged control and following explanations remain uncolored.
SVG rasterization uses a small font-free source so PNG snapshots do not depend
on system fonts; the HTML case retains the original SVG image references.

The DOCX-only `native_crossrefs` case explicitly enables `docxNativeCrossref` and
covers figures, tables, equations, native heading lists, subfigures, numeric
citations, and footnote references. Its snapshot preserves field instructions,
cached results, numbering definitions, and bookmark ranges. Random bookmark names
and numeric IDs are mapped consistently across XML parts and REF instructions;
reference targets remain distinguishable. Other snapshots keep their existing
bookmark serialization.

Check or refresh only this case with:

```bash
uv run pytest tests/test_build_snapshots.py -k native_crossrefs -p no:cacheprovider
uv run pytest tests/test_build_snapshots.py -k native_crossrefs -p no:cacheprovider --snapshot-update
```

The `reply` case builds through the public `build-reply` CLI. Its manuscript
contains baseline and revised content so copied figure, table, equation, section,
and citation numbers must follow the manuscript rather than restart in the reply.
The checked-in `tests/snapshot_cases/reply/manuscript.pdf` is a real export of
the companion `manuscript.md`, built to DOCX by Papper and then exported by
Microsoft Word. It provides stable numbered lines for both line placeholders
(currently 7 and 13). Tests read this PDF directly and never invoke Word COM;
Microsoft Word is not required to run them. These reply snapshots remain scoped
to Windows. When changing the manuscript, rebuild its DOCX with the fixture's
`style.yml`, export a replacement PDF with line numbers enabled, and refresh the
reply snapshots together. MathType is disabled in the fixture.
The DOCX snapshot retains reply styles, blue caption/table formatting, equation
layout, and reply-specific
page/line-number overrides. The TXT snapshot covers resolved references and
removal of answer-style wrappers, HTML breaks, image markup, and caption/equation
labels. The header's custom-style wrapper is retained by the current TXT renderer.

Check or refresh only the reply outputs with:

```bash
uv run pytest tests/test_build_snapshots.py -k reply -p no:cacheprovider
uv run pytest tests/test_build_snapshots.py -k reply -p no:cacheprovider --snapshot-update
```

## Private native submodules

The `scripts/mathtype-rust` and `scripts/latex2wmf` source trees are private
submodules. A source checkout therefore requires GitHub read access to both
repositories:

```bash
git clone --recurse-submodules https://github.com/cesaryuan/papper.git
```

For an existing checkout, initialize or refresh them with:

```bash
git submodule update --init --recursive
```

The GitHub Actions workflows use the repository secret
`PRIVATE_SUBMODULES_TOKEN`. Configure it with a least-privilege token that has
read-only Contents access to the parent repository and both private submodule
repositories. Secrets are not provided to workflows triggered by pull requests
from forks, so those runs cannot fetch the private source trees.

## Release to PyPI

This project publishes to PyPI with GitHub Actions trusted publishing, so release jobs do not need a stored PyPI token.

One-time setup:

1. Create the project on PyPI, or create a pending publisher if this is the first release.
2. In the GitHub repository, create an environment named `pypi`.
3. In the PyPI project settings, add a trusted publisher for this repository, the `publish-pypi.yml` workflow, and the `pypi` environment.

Pending publishers do not reserve the package name until the first successful publish, so run the first release soon after registering one.

Release steps:

```bash
git status --short
uvx bump-my-version bump patch
git push origin main --tags
```

Use `minor` or `major` instead of `patch` when appropriate. The version bump command updates `pyproject.toml` and the root package entry in `uv.lock`, creates a release commit, and tags it as `v{new_version}`. The workflow builds Windows, macOS 14-targeted, and manylinux wheels, smoke-tests their bundled native helpers, then runs `uv publish`.

## Python native conversion API

Platform wheels include only the two Rust shared libraries (`.dll`, `.so`, or
`.dylib`), without their command-line executables. Python conversion requires
these libraries and reports a missing-library error instead of launching a CLI. The C ABI uses Python's standard `ctypes`,
so the wheels retain their `py3-none-<platform>` tags.

```python
from pandoc_manuscript.mathtype.native import latex_to_equation, render_latex_to_wmf

# All returned artifacts are in memory; no converter process or input file.
equation = latex_to_equation(r"\frac{x_1}{2}")
ole_bytes = equation["ole"]
mtef_bytes = equation["mtef"]
preview = render_latex_to_wmf(
    r"\frac{x_1}{2}", svg_backend="typst", math_style="inline",
    font_size_pt=12.0, math_font="XITS Math",
)
wmf_bytes = preview["wmf"]
svg_text = preview["svg"]
metadata_json = preview["metadata_json"]
```

`latex_to_equation` accepts an optional `prefs_file` path. WMF rendering remains
independent of MathType preferences and does not reproduce MathType's visual
style. Conversion errors raise `RuntimeError`; the document pipeline wraps them
in its existing recoverable formula errors and removes failed output files.

In a source checkout the first call checks/builds each release library with Cargo;
subsequent calls reuse the loaded library. Restart Python after changing Rust
sources (Windows cannot replace a loaded DLL). Source use requires initialized
submodules and Cargo, or prebuilt libraries. Wheel use requires neither.
To build optimized libraries manually, run for each project:

```bash
cargo rustc --crate-type cdylib --manifest-path scripts/mathtype-rust/Cargo.toml --lib --features ffi --release
cargo rustc --crate-type cdylib --manifest-path scripts/latex2wmf/Cargo.toml --lib --features ffi --release
```

The build hook packages release libraries. The source loader also checks release artifacts with Cargo on first use so
source changes are not hidden by an old build. Typst reuses a process-wide engine and font catalog for named fonts, passing
formula text, point size, and math font independently on every compilation.
Explicit font files use a content-checked LRU cache of at most four engines;
replacing or deleting a file takes effect on the next call. System-font changes
require restarting Python. Recent Typst compilation work is retained for two
cache generations, while formula outputs remain subject to the existing pmt
artifact cache. First-load/build time must be reported separately from warm-call
benchmarks.

The versioned C entry points are `<project>_convert_v1` and `<project>_free_v1`
(with hyphens replaced by underscores). Requests and responses are UTF-8 JSON;
binary fields use hex on the C boundary and Python exposes them as `bytes`.
Responses are freed by the same library in a `finally` block. Unwinding Rust
panics are caught at the C boundary; process-aborting failures cannot be recovered.
