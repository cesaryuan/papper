# Maintainer releases and native build caches

[Back to the README](../README.md) · [Development](../DEVELOPMENT.md)

These are maintainer instructions for packaging, verifying, and publishing Papper. End users should follow the installation commands in the README.

Release wheel builds require the Typst CLI version locked for `typst-library`
in `Cargo.lock`. CI installs that version from the official release archives.
For local packaging, run `uv run --script tools/ci/prepare-typst.py` and add the
repository's `.pmt/typst/bin` directory to `PATH` before running `papper-dev wheel`.
The packager rejects a missing or mismatched CLI, removes cached MiTeX release
specifications, and enables `papper-platform/generate-mitex-spec` so the upstream
prebuilt specification cannot silently enter a newly compiled wheel. Installed-wheel
checks require circled math operators to become MathType objects without OMML fallback.

Release with `uvx bump-my-version bump patch` followed by
`git push origin main --tags`. The **Publish to PyPI** workflow publishes only
on `v*` tag pushes. Its `main` builds and manual runs build and verify the same
three platform wheels without publishing them, retaining the tested wheel
artifacts for seven days. A tag run waits for the `main` build of the exact same
commit and publishes those wheels without compiling the platforms again. If no
matching build appears, that build failed, or its artifacts expired, the tag run
builds and checks fresh wheels. An unfinished matching build has a bounded wait;
rerun the tag workflow if that wait expires.

Changes to either Rust submodule, the Haskell worker, the native build hook, helper inputs, or the
publishing workflow trigger cache warming on `main`. To warm or refresh caches
manually, run **Publish to PyPI** with **Run workflow**, selecting `main`.
GitHub allows tag builds to restore default-branch caches, but not caches saved
under other tags. Pushing `main` and a tag together is supported: the tag waits
for the tested artifacts instead of racing the branch's cache warm-up.

The workflow pins Rust to `1.98.0` and caches Cargo downloads and release build
outputs separately for Windows, macOS 14, and manylinux 2.28. Cache keys include
the toolchain, native lockfiles, Rust sources, embedded resources, submodule
revisions, build configuration, and equation preferences. Source changes create
new immutable snapshots even when dependencies stay unchanged; older compatible
snapshots seed those builds. Only successful `main` builds save caches. After all
platform builds succeed, cache housekeeping keeps the newest snapshot per
platform and cache kind, including replacing the legacy release cache layouts.
Unrelated workflow caches and caches under tag refs are preserved.

The workflow also pins GHC to `9.14.1` and Cabal to `3.18.1.0`, compiles the HTML
worker on all three platforms, and caches its Haskell dependencies, package
index, `.pmt/pandoc-worker` build directory and `.pmt/pandoc-source` tree separately
from Rust. macOS retains the pinned GHC/Cabal installation; Linux retains Rust
and GHC/Cabal toolchains at stable paths inside the manylinux container. A changed
source profile or index-state refreshes the Hackage index before resolving
dependencies. The first run seeds compatible legacy caches where available.
Linux compiles native components inside manylinux 2.28. The Rust wheel builder
stages and audits shared dependencies before embedding the runtime; it repairs
Linux RUNPATH and macOS load paths, and bundles official Windows CRT dependencies
only where the staged components' PE imports require them.
The Windows CLI uses a static CRT. The builder leaves original component files intact.
Each installed wheel must render citations and table cross-references through
its real background HTML server, reuse unchanged HTML, and invalidate edited
source before it can be published. GHC/Cabal are build-time tools only.

For MathType, the CLI directly links `mathtype-rust` and the same pinned Git
revision of `latex2wmf`. A safe Rust API returns owned OLE/MTEF bytes and previews;
no formula DLL, dynamic symbol lookup, or binary hex/JSON C ABI is shipped.
The renderer retains backend, style, font size, and math-font options.
`mathtypeTypstMathFont` accepts a string for one font throughout, or an object with
required `font` and `calligraphicFont` fields. Its default value explicitly selects
XITS Math and New Computer Modern Math respectively. Both default fonts are bundled.
Formula caches use `native-v2` and a compile-time engine fingerprint, plus the current
preferences, normalized font pair, both font-file digests, and optional helper inputs.
Equivalent string/object selections share cached previews. The standalone `latex2wmf` crate
and CLI remain available for development. The separate image helper is built
without `papper-core` or the embedded runtime archive. MuPDF is linked into the
CLI through `mupdf-sys`; the optional Windows C# helper remains a packaged
external component.

```yaml
# One font for ordinary math and calligraphy:
mathtypeTypstMathFont: XITS Math
```

```yaml
# Explicit default, with separate calligraphy for \mathcal, \mathscr, and \cal:
mathtypeTypstMathFont:
  font: XITS Math
  calligraphicFont: New Computer Modern Math
```

Both object fields accept a family name or `.otf`, `.ttf`, `.ttc`, or `.otc` file
path. Relative paths resolve from the owning style file. Effective metadata
normalizes string shorthand to the same two-field object used for rendering and
cache keys; the renderer metadata reports both selected fonts.

CI sets `CARGO_TARGET_DIR` to persist native build artifacts. On Linux this directory and Cargo's download cache live on the host via
the container's `/host` mount, so they survive the manylinux container. Local
builds retain their normal per-project target directories unless this environment
variable is set. Cabal dependency caches also persist through the Linux `/host`
mount. Build logs report elapsed time for the Haskell worker, native Rust executables, and
Windows .NET helper. A cold cache or a toolchain change still requires compilation;
actual release speedups should be measured after a successful warm-up.

The GPL HTML worker's adapted Haskell sources, Pandoc license/copyright notices,
and resolved dependency source links are included under
`share/papper/bin/pandoc-worker-source` in each wheel's data directory. Non-editable local
wheel builds require GHC/Cabal as well as Cargo and, on Windows, .NET; editable
native development installations retain the existing manually built worker path.

Before saving the Linux cache, the workflow transfers its container-created files
to the runner user and checks directory sizes and readability. A lookup after
saving requires the exact cache entry to exist remotely; an archive/upload failure
therefore fails the warm-up instead of silently leaving future releases uncached.
