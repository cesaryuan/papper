# Shared Pandoc CLI and persistent HTML worker

This executable provides a Pandoc 3.12 CLI and the native half of Papper's
project-bound HTTP service. Ordinary arguments follow the upstream CLI, including
stdin/stdout, binary outputs, information requests, Lua and HTTP server modes.
The standard `pandoc-crossref` filter is replaced by an in-process library call;
explicit custom filter paths keep their upstream behavior. CLI citeproc uses
Pandoc's native implementation; the persistent worker uses the caching adapter.

```powershell
pmt-pandoc-worker manuscript.md -o manuscript.docx --filter pandoc-crossref --citeproc
pmt-pandoc-worker --help
pmt-pandoc-worker lua -e "print(pandoc.version)"
pmt-pandoc-worker server --port 3031
pmt-pandoc-worker --pmt-worker --config config.json
```

The old private `--config PATH` launch convention remains supported for existing
developer overrides. `--pmt-crossref-version` reports the compiled crossref
version for Papper diagnostics. Public CLI parsing/error handling follows
Pandoc 3.12's entry point; it is not a promise of byte-identical output across
different platforms or dependency builds.

Python owns metadata preparation, dependency discovery, bounded HTML caching,
HTTP responses, and normal HTML postprocessing. The serialized native worker
keeps Pandoc and citation assets alive between conversions. Markdown is parsed
from a fresh immutable snapshot. Public HTTP conversions return exact documents.

## Installed wheels

The PyPI platform wheels bundle `pmt-pandoc-worker` (with `.exe` on Windows).
All Papper conversions use it directly; users do not install GHC, Cabal, Pandoc,
or crossref. Setup validates the bundled CLI and embedded crossref without
downloading tools. `PMT_PANDOC_SERVER_WORKER_COMMAND` overrides only worker execution for
profiling or development. Without an override, the bundled worker takes priority
over an older manually installed executable in `~/.papper/tools/bin`.

The Hatch wheel hook builds the current checkout with static Haskell libraries.
CI pins GHC 9.14.1 and Cabal 3.18.1.0, caches Cabal dependencies, and builds Linux
inside manylinux 2.28. Auditwheel on Linux and delocate on macOS bundle required
non-system C libraries. Every installed wheel is exercised by
`scripts/check_packaged_html_server.py`: actual citation/cross-reference output,
HTTP input fetching, warm HTML reuse, and source-edit invalidation must pass
before publication. HTTP verification uses a local fixture and needs no internet.

Linux setup pins the PyPI `patchelf` package to 0.19.1.0 before auditwheel runs.
The manylinux image's 0.17.2 RPATH rewrite can cause GHC executables to exit with
SIGSEGV before `main`, even when auditwheel reports a successful repair.

## Build and install from source

The supported versions are Pandoc 3.12, pandoc-crossref 0.3.25, and citeproc
0.14. All direct dependency lower bounds track the latest stable Hackage
versions audited on 2026-10-02. `cabal.project` pins Pandoc and the Hackage
index state. Targeted `allow-newer` entries cover crossref's still-declared
`pandoc <3.12` bound and lagging upper bounds in its dependency graph; all
unlisted bounds remain active. HTTP resource fetching is explicitly enabled so
the solver cannot silently remove remote CSL/image support to satisfy old TLS
dependency bounds. Indirect dependencies are also upgraded, including
crypton 2.1.7, QuickCheck 2.19, tagged 0.9, transformers 0.6.3, and Unicode 18
data. The worker uses the compatible exposed crossref library API. Source builds
require GHC 9.14.1 (base 4.22) and Cabal 3.18.1.0; CI uses these versions.

One indirect dependency deliberately stays at its latest compatible release:
`time-manager 0.3.2`. Version 0.4 changes active-handle `resume` behavior, whereas
http2 5.4.7 still uses it as `tickle`. Its `<0.4` bound remains enforced to avoid
breaking HTTP/2 timeout renewal. GHC bootstrap libraries retain the compiler's
own versions; application dependencies are rebuilt with the newer libraries.

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
uv run python scripts/benchmark_html_server.py --manuscript ../1-3d-mesh/manuscript.md
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

`Papper/Citeproc.hs` and `Papper/Locator.hs` derive from Pandoc 3.12's
`Text.Pandoc.Citeproc`, `Text.Pandoc.Citeproc.Locator`, and the small
`splitStrWhen` helper in `Text.Pandoc.Shared`:
[upstream source](https://github.com/jgm/pandoc/tree/3.12/src/Text/Pandoc).
The adapted modules preserve the upstream citation/document algorithms, expose
prepared-data/result caching, and substitute validated local remote resources.
Reference parsing still calls Pandoc's exposed `getReferences` implementation.

These adapted modules and the linked native worker use GPL-2.0-or-later. The
upstream license and copyright notices are retained in `vendor/pandoc/` and at
the top of the adapted modules. Wheels retain the worker sources and notices,
plus exact resolved dependency versions and upstream source links, under
`pandoc_manuscript/bin/pandoc-worker-source`. The Python package's existing license is
unchanged. A future Pandoc upgrade must reconcile these adapters against its
upstream implementations and rerun the complete HTML output tests.

The CLI dispatch in `Main.hs` also follows Pandoc 3.12's
`pandoc-cli/src/pandoc.hs`. `vendor/pandoc-cli/PandocCLI/` retains the upstream
Lua and server launch modules with their original GPL notices. They and the
adapted entry point are included in wheel source notices. The linked conversion
library is shared by all modes rather than compiled into separate executables.
