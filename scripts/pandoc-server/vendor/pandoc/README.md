# Papper's Pandoc 3.12 overrides

This directory maintains adapted upstream modules, the original GPL notices,
and `SOURCES.json` with the immutable Hackage archive identity and supported formats.
`papper-dev worker` verifies the source archive, prepares a private copy under
`.pmt/pandoc-source/pandoc-3.12`, and installs these modules before Cabal builds it.
The normal wheel command performs the same preparation automatically.
Compiled packages live under `.pmt/pandoc-worker`; source commands select a
stripped immutable Worker copy through `current.json`, so a running Windows
service cannot lock Cabal's executable output during subsequent builds.

The registry changes remove unused Reader/Writer entries. The DOCX parser also
flushes open field contents at explicit numbered paragraph boundaries, matching
its existing handling of ordinary paragraphs. Without this fix, MathType or other
open fields can leave empty list items and numbered headings while moving their
text into later paragraphs or table cells. Upstream exported functions,
extensions, templates and dependencies remain intact.
All upstream modules still compile; fewer formats are reachable from the registry,
so the executable linker can discard their implementations. Bibliography readers
remain necessary for citeproc, while HTML/LaTeX readers and plain/Markdown writers
remain available for DOCX conversion, replies and Lua filters.

To upgrade, update the pinned archive version/SHA-256 and Cabal source path, rebase
the overrides, and run the output snapshots and installed-wheel smoke validation.
The wheel builder verifies the actual prebuilt Worker's reported format profile,
and ships these modified sources plus their content hashes in its source record.

Upstream: <https://hackage.haskell.org/package/pandoc-3.12>
