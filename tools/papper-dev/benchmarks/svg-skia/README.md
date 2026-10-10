# CPU SVG renderer experiment

This standalone tool compares Skia's SVG DOM and Chromium on the original report
diagrams without changing the authored SVGs. Production Papper now uses Skia too,
with usvg preprocessing for its DPI, CSS, font and external-resource contracts.
The nested workspace keeps benchmark tooling separate from the product.

## Run on Windows

From the repository root in PowerShell:

```powershell
$env:CARGO_TARGET_DIR = "$PWD/target/svg-skia-benchmark"
cargo build --locked --release --manifest-path tools/papper-dev/benchmarks/svg-skia/Cargo.toml
uv run --script tools/papper-dev/benchmarks/svg-skia/compare.py
```

The comparison script uses Playwright 1.58.0's full Chromium installation. If
that browser is absent, install the matching browser first:

```powershell
uv run --with playwright==1.58.0 playwright install chromium
```

To render just one original SVG:

```powershell
& ./target/svg-skia-benchmark/release/papper-svg-skia-benchmark.exe --source critical_rerouting.svg --output target/svg-skia-benchmark/results/rerouting.png --repeat 3
```

The PNG output directory must already exist for direct Rust CLI calls. The
comparison script creates its own output directory. Use `compare.py --help` to
select other source paths, outputs, repetitions or the renderer timeout.
Generated artifacts stay below
`target/svg-skia-benchmark/results/`, including `comparison.json` with all raw
timings, dimensions, filter controls and full-resolution pixel differences.

## Interpretation

Skia uses `surfaces::raster_n32_premul`, so rendering executes on CPU. The `gl`
build feature selects an available official Windows prebuilt archive; no GPU
surface or context is created. The default SVG+textlayout feature combination
without GL has no matching 0.153.3 Windows archive and falls back to a full source
build requiring LLVM. PNG codecs, system font access and inline image resources
are retained.

Timings include fresh DOM parsing, raster surface allocation, rendering and PNG
encoding. Skia system font initialization is reported separately; browser
startup and output-file writes are excluded. Chromium runs with GPU disabled
and a canvas configured for CPU readback. PNG compression choices differ between
renderers, so encoded file size is not a quality metric.

The Skia benchmark deliberately supports intrinsic-size SVGs
with local system fonts and inline images. It does not yet validate external
resource loading, viewport-relative sizes, Papper DPI/width options, caching or
the complete supported SVG corpus.

See [the measured report](../../../../docs/SVG_RENDERER_BENCHMARK.md).
