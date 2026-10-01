# HTML server performance, 2026-09-30

Preview measurements below are historical. The public preview endpoints have
since been removed; current HTTP benchmarks measure complete HTML documents.

Warm conversions exceed the requested 50% latency reduction for both the bundled
template and the real 3D mesh paper. The measurements cover a complete persistent
local HTTP request, including dependency checks, Pandoc/filter execution, HTML
postprocessing, and response transfer. They exclude worker startup, first-time
remote downloads, and independent CLI output checks.

| Input and operation | Previous worker, median | Optimized worker, median | Reduction |
| --- | ---: | ---: | ---: |
| Template, prose edit | 151.045 ms | 65.190 ms | 56.8% |
| Template, citation locator edit | 154.839 ms | 66.039 ms | 57.3% |
| Template preview, prose edit | 142.089 ms | 57.168 ms | 59.8% |
| Template preview, citation locator edit | 140.341 ms | 57.622 ms | 58.9% |
| Real paper, prose edit | 3337.537 ms | 217.675 ms | 93.5% |
| Real paper, citation locator edit | 3371.950 ms | 225.494 ms | 93.3% |

Unchanged exact HTML takes about 1.9 ms for the template and 2.4 ms for the real paper.
That is a separate HTML cache hit, rather than a faster conversion. Because both
workers use the same corrected frontend, these unchanged-source measurements
also give the previous worker the new frontend cache improvements.

The template contains 14,353 bytes; the real paper contains 78,401 bytes. Every
scenario used two warmups followed by fifteen samples, with no outlier removal.
All prose-edit samples missed the whole-HTML cache. Optimized prose edits reused
citation evaluation; changed locators recomputed it. Every scenario's final
HTML matched a fresh CLI conversion plus Papper's normal postprocessing.

Detailed reports and retained samples for this checkout:

- [Template exact/preview report](../../output/benchmarks/server/20260930T195915.482407Z/summary.md)
  and [raw samples](../../output/benchmarks/server/20260930T195915.482407Z/results.json).
- [Real paper report](../../output/benchmarks/server/20260930T195557.713920Z/summary.md)
  and [raw samples](../../output/benchmarks/server/20260930T195557.713920Z/results.json).

The reports are generated local artifacts under ignored `output/`. This checked-in
note preserves the results and method; run `scripts/benchmark_html_server.py` to
create fresh reports in another checkout. Executable and input hashes, tool
versions, individual stages, means, standard deviations, and cache hit counts
are recorded in each `results.json`. The tested toolchain used Pandoc 3.11,
pandoc-crossref 0.3.25, GHC 9.10.3, and Cabal 3.16.1 on Windows.

## What dominated elapsed time

The real paper selects a dependent CSL whose independent parent is
`http://www.zotero.org/styles/elsevier-with-titles`. The previous worker fetched
that parent repeatedly. Wall-stage timings and GHC RTS CPU/elapsed statistics
identified the network fetch as most of its approximately three-second delay.
Reusing the parsed CSL and bibliography reduced a changed-locator citeproc
evaluation to about 18 ms, even without a citation-result cache hit. The formal
remote-asset cache now supplies local snapshots and validates them periodically.
Network conditions therefore contribute to the real-paper baseline; the 93%
reduction should not be extrapolated to a paper whose CSL is already fully local.

The template has local citation assets and still improves by over 50%. Its gains
come from embedded crossref, prepared citation caches, reuse of parsed defaults,
and batching the five audited Lua filters into one Lua environment with separate
script namespaces. The paragraph-style filter also avoids recursively scanning
preceding prose unless the current paragraph begins with "where". Larger GHC
allocation nurseries did not materially improve warm conversions; `-A16m` remains
the default.

Both exact and preview modes parse the entire current document. The removed
background section-AST strategy could lose global heading IDs, link definitions,
and footnote context. Prepared citation reuse preserves those reader semantics
while making prose changes inexpensive.

## CLI boundary and verification

`papper build html --start-server` now builds through the service. Independent
CLI processes with captured output reused one PID across the initial build, a
prose edit, and an unchanged repeat. The native metrics recorded one citation
evaluation hit and one HTML hit, and the final HTML exactly matched the normal
CLI build. The Windows launch/liveness fixes prevent inherited output pipes from
keeping callers alive and prevent PID probes from terminating a live process.

The separate template CLI smoke run measured roughly 1.74 s for the first launch,
0.58 s after editing prose, and 0.53 s for an unchanged repeat. These are single
observations including Python/CLI/uv startup and configuration work, not the
HTTP medians above. Repeated editor builds can call `/convert/raw` directly to
avoid that per-command overhead.

Validation: `uv run pytest -q` passed **234 tests**, including all original 221
and thirteen real server/CLI output and recovery cases. The two existing archive
deprecation warnings are unchanged. The native worker compiled successfully,
changed Python files passed compilation, and `git diff --check` passed. Original
manuscripts and reference resources were read-only during benchmarking.
