# Development

## Build snapshots

The build snapshots cover the manuscript template and focused fixtures for
citations, cross-references and metadata, using both standalone HTML and DOCX.
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

## Private native submodules

The `scripts/mathtype-rust` and `scripts/latex2wmf` source trees are private
submodules. A source checkout therefore requires GitHub read access to both
repositories:

```bash
git clone --recurse-submodules https://github.com/cesaryuan/pandoc-manuscript-template.git
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
