"""Verify portable citation formatting recovered from real Zotero DOCX properties.

The existing import snapshot covers references but cannot detect a missing CSL,
lost locale, or dependent-style failure. These cases import actual DOCX packages
and render their citations using the generated companion style, entirely offline.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from zipfile import ZipFile

import pytest
import yaml
from lxml import etree

from native_support import ROOT, native_pandoc_executable


STYLE_ID = "http://www.zotero.org/styles/papper-local-test"
CSL = '''<?xml version="1.0" encoding="utf-8"?>
<style xmlns="http://purl.org/net/xbiblio/csl" version="1.0" class="in-text">
  <info><title>Local citation test</title><id>STYLE_ID</id>
    <updated>2026-01-01T00:00:00+00:00</updated></info>
  <citation><layout prefix="LOCAL(" suffix=")"><text variable="title"/></layout></citation>
  <bibliography><layout><text variable="title"/></layout></bibliography>
</style>'''.replace("STYLE_ID", STYLE_ID)


def source_with_preferences(directory: Path, preferences: str) -> Path:
    """Replace the sample's plugin properties, splitting data into out-of-order numbered chunks."""
    with ZipFile(ROOT / "tests/snapshot_cases_convert/comprehensive/comprehensive.docx") as archive:
        parts = {entry.filename: archive.read(entry) for entry in archive.infolist()}
    ns = "http://schemas.openxmlformats.org/officeDocument/2006/custom-properties"
    vt = "http://schemas.openxmlformats.org/officeDocument/2006/docPropsVTypes"
    properties = etree.Element(f"{{{ns}}}Properties", nsmap={None: ns, "vt": vt})
    middle = len(preferences) // 2
    for index, text in [(2, preferences[middle:]), (1, preferences[:middle])]:
        prop = etree.SubElement(properties, f"{{{ns}}}property", name=f"ZOTERO_PREF_{index}",
                                pid=str(index + 1), fmtid="{D5CDD505-2E9C-101B-9397-08002B2CF9AE}")
        etree.SubElement(prop, f"{{{vt}}}lpwstr").text = text
    parts["docProps/custom.xml"] = etree.tostring(properties)
    source = directory / "引用 draft.docx"
    with ZipFile(source, "w") as archive:
        for name, content in parts.items():
            archive.writestr(name, content)
    return source


def convert(executable: Path, directory: Path, preferences: str) -> tuple[dict, Path, subprocess.CompletedProcess]:
    """Run the public conversion in an isolated offline Zotero installation."""
    source = source_with_preferences(directory, preferences)
    output = directory / "converted"
    result = subprocess.run(
        [str(executable), "convert", str(source), "-o", str(output), "--offline",
         "--zotero-data-dir", str(directory / "zotero")], cwd=directory,
        env={**os.environ, "PAPPER_RESOURCE_ROOT": str(ROOT), "PAPPER_HOME": str(directory / "home"),
             "PAPPER_DISABLE_UPDATE_CHECK": "1"},
        capture_output=True, text=True, encoding="utf-8", timeout=90,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    markdown = (output / "引用 draft.md").read_text(encoding="utf-8")
    assert "[@longRegionawareNeuralRadiance2026]" in markdown
    assert (output / "引用 draft.bib").is_file()
    return yaml.safe_load(markdown[4:].split("\n---\n", 1)[0]), output, result


@pytest.mark.parametrize("encoding", ["xml", "json"])
def test_convert_recovers_local_csl_and_renders_citations(
    encoding: str, tmp_path: Path, rust_executable: Path,
) -> None:
    """Retain local CSL rules and document locale even with renamed files and split properties."""
    styles = tmp_path / "zotero/styles"
    styles.mkdir(parents=True)
    (styles / "用户修改.csl").write_text(CSL, encoding="utf-8")
    preferences = (f'<data><style id="{STYLE_ID}" locale="fr-FR"/></data>' if encoding == "xml"
                   else json.dumps({"dataVersion": 4, "style": {"styleID": STYLE_ID, "locale": "fr-FR"}}))
    metadata, output, _ = convert(rust_executable, tmp_path, preferences)
    assert metadata["zotero-style-id"] == STYLE_ID
    assert metadata["zotero-style-locale"] == "fr-FR"
    csl = output / metadata["csl"]
    assert etree.fromstring(csl.read_bytes()).get("default-locale") == "fr-FR"
    engine = native_pandoc_executable()
    assert engine is not None
    rendered = subprocess.run(
        [str(engine), "引用 draft.md", "--citeproc", "-t", "plain"], cwd=output,
        capture_output=True, text=True, encoding="utf-8", timeout=30,
    )
    assert rendered.returncode == 0, rendered.stderr
    assert "LOCAL(" in rendered.stdout
    # Reimports replace generated companions but remain independent of the local filename.
    original = csl.read_bytes()
    convert(rust_executable, tmp_path, preferences)
    assert csl.read_bytes() == original


def test_convert_flattens_dependent_csl(tmp_path: Path, rust_executable: Path) -> None:
    """Export an independent style whose locale and citation rules survive without its parent."""
    styles = tmp_path / "zotero/styles"
    (styles / "hidden").mkdir(parents=True)
    (styles / "hidden/parent.csl").write_text(CSL, encoding="utf-8")
    dependent_id = "http://www.zotero.org/styles/papper-dependent-test"
    (styles / "child.csl").write_text(
        f'<style xmlns="http://purl.org/net/xbiblio/csl" version="1.0" default-locale="de-DE">'
        f'<info><title>Child</title><id>{dependent_id}</id>'
        f'<link rel="independent-parent" href="{STYLE_ID}"/></info></style>', encoding="utf-8",
    )
    metadata, output, _ = convert(rust_executable, tmp_path, f'<data><style id="{dependent_id}"/></data>')
    assert metadata["zotero-style-id"] == dependent_id
    style = etree.fromstring((output / metadata["csl"]).read_bytes())
    assert style.get("default-locale") == "de-DE"
    assert style.find("{http://purl.org/net/xbiblio/csl}citation") is not None
    assert b"independent-parent" not in etree.tostring(style)


def test_convert_discovers_profile_configured_data_directory(tmp_path: Path, rust_executable: Path) -> None:
    """Discover custom Zotero data via the default profile and preserve its modified citation rules."""
    home = tmp_path / "user"
    roaming = home / "AppData/Roaming"
    if os.name == "nt":
        profile_root = roaming / "Zotero/Zotero"
    elif sys.platform == "darwin":
        profile_root = home / "Library/Application Support/Zotero"
    else:
        profile_root = home / ".zotero/zotero"
    profile = profile_root / "Profiles/default"
    profile.mkdir(parents=True)
    (profile_root / "profiles.ini").write_text(
        "[Profile0]\nName=default\nDefault=1\nIsRelative=1\nPath=Profiles/default\n", encoding="utf-8",
    )
    data = tmp_path / "自定义 Zotero"
    (data / "styles").mkdir(parents=True)
    (data / "styles/modified.csl").write_text(CSL, encoding="utf-8")
    (profile / "prefs.js").write_text(
        f'user_pref("extensions.zotero.useDataDir", true);\n'
        f'user_pref("extensions.zotero.dataDir", {json.dumps(str(data))});\n', encoding="utf-8",
    )
    source = source_with_preferences(tmp_path, f'<data><style id="{STYLE_ID}"/></data>')
    output = tmp_path / "converted"
    result = subprocess.run(
        [str(rust_executable), "convert", str(source), "-o", str(output), "--offline"], cwd=tmp_path,
        env={**os.environ, "USERPROFILE": str(home), "HOME": str(home), "APPDATA": str(roaming),
             "PAPPER_RESOURCE_ROOT": str(ROOT), "PAPPER_HOME": str(tmp_path / "papper-home"),
             "PAPPER_DISABLE_UPDATE_CHECK": "1"},
        capture_output=True, text=True, encoding="utf-8", timeout=90,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert (output / "引用 draft.csl").read_text(encoding="utf-8") == CSL


@pytest.mark.parametrize("failure", ["missing", "cycle", "malformed"])
def test_convert_retains_identity_when_csl_unavailable(
    failure: str, tmp_path: Path, rust_executable: Path,
) -> None:
    """Missing, invalid, or cyclic CSL must preserve manuscript data and explain the recovery gap."""
    styles = tmp_path / "zotero/styles"
    styles.mkdir(parents=True)
    if failure == "cycle":
        (styles / "cycle.csl").write_text(
            f'<style xmlns="http://purl.org/net/xbiblio/csl"><info><id>{STYLE_ID}</id>'
            f'<link rel="independent-parent" href="{STYLE_ID}"/></info></style>', encoding="utf-8",
        )
    elif failure == "malformed":
        (styles / "invalid.csl").write_text("<style>broken", encoding="utf-8")
    metadata, output, result = convert(rust_executable, tmp_path, f'<data><style id="{STYLE_ID}"/></data>')
    assert metadata["zotero-style-id"] == STYLE_ID
    assert "csl" not in metadata
    assert not list(output.glob("*.csl"))
    assert "Could not restore CSL" in result.stderr
