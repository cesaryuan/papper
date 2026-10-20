# Comprehensive DOCX import fixture

`comprehensive.docx` exercises equations, captions, citations, text formatting,
and extracted media. `styles/elsevier-vancouver-nodoi.csl` is the local CSL
matching the Zotero style ID saved in that DOCX; its original attribution and
license are retained in the file. Tests stage this input as a local Zotero style
and run offline, making the successful import reproducible on other computers.

`snapshots-content/` contains the generated Markdown, BibLaTeX bibliography,
recovered CSL, and normalized media. Missing-style recovery is covered separately
by `tests/test_convert_csl.py`.

Refresh and review the snapshot with:

```powershell
uv run pytest 'tests/test_convert_snapshots.py::test_convert_output_matches_snapshot[comprehensive]' --snapshot-update -q
```
