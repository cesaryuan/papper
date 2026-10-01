# Persistent HTML worker

This executable is the native half of Papper's project-bound HTTP service.
Python owns metadata preparation, dependency discovery, bounded HTML caching,
HTTP responses, and normal HTML postprocessing. The serialized native worker
keeps Pandoc and citation assets alive between conversions. Markdown is parsed
from a fresh immutable snapshot for both exact documents and preview fragments.

## Build and install

The supported versions are Pandoc 3.11, pandoc-crossref 0.3.25, and citeproc
0.13.x. `cabal.project` pins Pandoc and relaxes crossref's initial 3.10 upper
bound; the worker uses the compatible exposed crossref library API. The measured
Windows build used GHC 9.10.3 and Cabal 3.16.1.

```powershell
Push-Location .\scripts\pandoc-server
cabal build exe:pmt-pandoc-worker
cabal install . --installdir "$HOME\.papper\tools\bin" --overwrite-policy=always
Pop-Location
uv run papper build html --start-server
```

For a development binary, set `PMT_PANDOC_SERVER_WORKER_COMMAND` to its executable
path instead. Quote paths containing spaces. Rebuild older workers: their private
protocol accepts fewer fields and their citation/filter pipeline does not use
these caches. Cabal can use `--offline` when dependencies are already available.

## Conversion and cache boundaries

- The standard `pandoc-crossref` filter runs through its Haskell library, with
  upstream project/home configuration handling. Explicit custom JSON filters
  retain their external-process execution and original ordering.
- Defaults are reparsed only when the dependency fingerprint changes. Requests
  supply current metadata, resource roots, and the citation range delimiter;
  restoring the delimiter's default clears its old native environment value.
- CSL styles are cached by actual Pandoc metadata. Parsed references are cached
  by metadata and cited IDs, including `nocite`. Citeproc evaluation is cached
  by metadata and actual citation inputs, including locators, prefixes/suffixes,
  note numbers, and section reset positions. Each cache holds at most eight
  entries and is cleared when the dependency generation changes.
- Even with a citation evaluation hit, citation insertion, punctuation movement,
  note conversion, and bibliography placement run against the current document.
  This preserves edits around citations and fresh footnote context.
- Five bundled HTML Lua scripts can share one Lua state and sequential document
  walks, with separate script environments. Python verifies audited content
  hashes; the worker checks the exact contiguous filter order. Edited, reordered,
  or custom scripts use Pandoc's normal independent filter boundaries.
- The Python HTML cache holds at most eight successful results and 32 MiB.
  Its key includes mode, source, effective metadata, dependencies, and referenced
  local resources. Missing and recreated dependencies, template partials, and
  higher-priority assets created after startup invalidate the relevant result.
- Remote citation resources use exact URL-to-local mappings. Downloaded CSL
  parents are validated as CSL before publication. Conditional HTTP validation
  happens every five minutes; an unavailable remote preserves the last valid
  snapshot with a warning and a retry after 30 seconds. First downloads fail if
  no valid local snapshot exists. Arbitrary remote templates disable HTML reuse.

Private JSON lines name input and output files. Both paths must be inside the
canonical project directory or explicitly allocated private work directories.
The public HTTP API only accepts source paths inside the project. A failed
conversion is not published to the HTML cache; a dead native child restarts on
the next uncached conversion. The CLI decodes a successful response before
opening its output destination, preserving existing HTML when conversion fails.
Detached Windows services close unrelated inherited handles so captured CLI
output can reach EOF. PID liveness checks use a process query rather than the
Windows termination behavior of `os.kill(pid, 0)`; managed tree shutdown can
therefore stop both the service and its native child.

## Profiling and reproducible measurements

Run from the repository root:

```powershell
uv run python scripts/benchmark_html_server.py --manuscript template/manuscript.md
uv run python scripts/benchmark_html_server.py --manuscript ../1-3d-mesh/manuscript.md --modes exact,preview
uv run pytest tests/test_html_server.py -q
```

Use `--baseline-worker PATH` for a saved pre-change executable. The same corrected
HTTP frontend serves both workers; legacy worker intermediates stay inside the
temporary project because its old path guard rejects system-temp outputs.
Each scenario uses two warmups and fifteen retained samples by default. Changed
prose, changed citation locators, and identical-source HTML reuse are measured
separately. The final output is compared to a fresh CLI build outside the timed
interval. Reports include all samples, stage timings, executable/input hashes,
and tool versions. Original manuscript inputs are read-only.

`Server-Timing` and worker `filters_ms` report forced wall-clock filter stages.
`X-PMT-Citeproc-Cache` distinguishes native evaluation reuse from a skipped
worker on an HTML cache hit. GHC RTS statistics can also be captured by running
the private worker with `+RTS -s -RTS`. Comparing CPU and elapsed time exposed
the real paper's remote parent-CSL fetch as the dominant delay. Larger allocation
nurseries were tried without a useful warm-build improvement; the default is
`-A16m`. Startup and initial downloads remain outside the warm latency claim.

## Adapted upstream sources

`Papper/Citeproc.hs` and `Papper/Locator.hs` derive from Pandoc 3.11's
`Text.Pandoc.Citeproc`, `Text.Pandoc.Citeproc.Locator`, and the small
`splitStrWhen` helper in `Text.Pandoc.Shared`:
[upstream source](https://github.com/jgm/pandoc/tree/3.11/src/Text/Pandoc).
The adapted modules preserve the upstream citation/document algorithms, expose
prepared-data/result caching, and substitute validated local remote resources.
Reference parsing still calls Pandoc's exposed `getReferences` implementation.

These adapted modules and the linked native worker use GPL-2.0-or-later. The
upstream license and copyright notices are retained in `vendor/pandoc/` and at
the top of the adapted modules. The Python package's existing license is
unchanged. A future Pandoc upgrade must reconcile these adapters against its
upstream implementations and rerun the complete HTML output tests.
