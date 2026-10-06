"""Render representative generated HTML pages in pinned Chromium and compare pixels.

The test deliberately keeps visual baselines separate from semantic HTML snapshots:
CSS and browser layout may be reorganized when the rendered document is unchanged.
Run ``uv run pytest tests/test_html_visual.py --visual --visual-update`` only after
reviewing an intentional rendering change.
"""

from __future__ import annotations

import hashlib
import importlib.metadata
import io
import json
import logging
import mimetypes
import os
import platform
import shutil
import subprocess
import sys
from pathlib import Path
from collections.abc import Iterator
from urllib.parse import unquote, urlparse

import pytest
from PIL import Image, ImageChops
from playwright.sync_api import Browser, Route, sync_playwright

from snapshot_utils import semantic_html
from test_build_snapshots import CASES, DOCX_ONLY_CASES, build_case


ROOT = Path(__file__).resolve().parents[1]
VISUAL_ROOT = Path(__file__).with_name("visual")
VISUAL_SNAPSHOTS = VISUAL_ROOT / "snapshots" / f"{sys.platform}-{platform.machine().lower()}"
VISUAL_RESULTS = VISUAL_ROOT / "results"
VISUAL_CASES = tuple(name for name in CASES if name not in DOCX_ONLY_CASES)
LOGGER = logging.getLogger(__name__)


class HtmlVisualSession:
    """Own browser rendering state and explicitly verify the baseline environment."""

    def __init__(self, browser: Browser, update: bool) -> None:
        """Fingerprint Chromium, offline math assets and the Windows document fonts."""
        self.browser = browser
        self.update = update
        self.katex = VISUAL_ROOT / "node_modules/katex/dist"
        assert self.katex.is_dir(), "Install fixed math assets with npm ci --prefix tests/visual"
        assets = hashlib.sha256()
        for path in sorted(self.katex.rglob("*")):
            if path.is_file():
                assets.update(path.relative_to(self.katex).as_posix().encode())
                assets.update(path.read_bytes())
        fonts = {}
        if sys.platform == "win32":
            font_root = Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts"
            for name in ("times.ttf", "timesbd.ttf", "timesbi.ttf", "timesi.ttf", "simsun.ttc", "simhei.ttf", "msyh.ttc", "msyhbd.ttc", "cambria.ttc", "arial.ttf"):
                path = font_root / name
                fonts[name] = hashlib.sha256(path.read_bytes()).hexdigest() if path.exists() else None
            assert all(fonts[name] for name in ("times.ttf", "timesbd.ttf", "timesbi.ttf", "timesi.ttf", "simsun.ttc")), "Visual baselines require Times New Roman and SimSun"
        self.environment = {
            "platform": sys.platform,
            "architecture": platform.machine().lower(),
            "os_release": platform.release(),
            "browser": browser.version,
            "playwright": importlib.metadata.version("playwright"),
            "viewport": {"width": 1280, "height": 900},
            "device_scale_factor": 1,
            "locale": "en-US",
            "timezone": "UTC",
            "media": "screen",
            "katex_sha256": assets.hexdigest(),
            "font_sha256": fonts,
        }
        manifest = VISUAL_SNAPSHOTS / "environment.json"
        if not update:
            assert manifest.exists(), f"Missing visual environment baseline: {manifest}"
            expected = json.loads(manifest.read_text(encoding="utf-8"))
            assert expected["environment"] == self.environment, (
                "Visual baseline environment differs; use the recorded browser, OS and fonts\n"
                + json.dumps({"expected": expected["environment"], "actual": self.environment}, indent=2)
            )

    def capture(self, output: Path) -> bytes:
        """Serve local fixture resources offline and require two matching screenshots."""
        context = self.browser.new_context(
            viewport=self.environment["viewport"], device_scale_factor=1,
            locale="en-US", timezone_id="UTC", reduced_motion="reduce",
            color_scheme="light", service_workers="block",
        )
        failures: list[str] = []

        def serve(route: Route) -> None:
            """Route generated URLs to fixture/KaTeX files; unexpected network is an error."""
            url = urlparse(route.request.url)
            if url.netloc == "papper.test":
                root = output.parent.parent
                path = root / unquote(url.path.lstrip("/"))
            elif "/npm/katex@" in url.path and "/dist/" in url.path:
                root = self.katex
                path = root / unquote(url.path.split("/dist/", 1)[1])
            else:
                failures.append(f"Unexpected external resource: {route.request.url}")
                route.abort()
                return
            # Never resolve ../ references outside the copied fixture or pinned assets.
            if not path.resolve().is_relative_to(root.resolve()) or not path.is_file():
                failures.append(f"Missing resource: {route.request.url}")
                route.abort()
                return
            content_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
            route.fulfill(path=path, content_type=content_type, headers={"Access-Control-Allow-Origin": "*"})

        context.route("**/*", serve)
        page = context.new_page()
        page.set_default_timeout(15000)

        def record_error(error: Exception) -> None:
            """Reject JavaScript failures even if the page still produces a screenshot."""
            failures.append(str(error))

        page.on("pageerror", record_error)
        try:
            page.goto(f"http://papper.test/{output.parent.name}/{output.name}", wait_until="load")
            assert not failures, "\n".join(failures)
            page.wait_for_function("""() => {
                // Missing images or unrendered math must never become a golden image.
                return Array.from(document.images).every(image => image.complete && image.naturalWidth > 0)
                    && Array.from(document.querySelectorAll('.math')).every(math => math.querySelector('.katex'));
            }""")
            page.evaluate("""async () => {
                // Font readiness precedes layout comparison, including KaTeX web fonts.
                await document.fonts.ready;
            }""")
            errors = page.locator(".katex-error").all_text_contents()
            assert not errors, f"KaTeX rendering errors: {errors}"
            previous = page.screenshot(full_page=True, animations="disabled", caret="hide", scale="css")
            for _ in range(4):
                current = page.screenshot(full_page=True, animations="disabled", caret="hide", scale="css")
                if current == previous:
                    assert not failures, "\n".join(failures)
                    return current
                previous = current
            raise AssertionError("HTML rendering did not stabilize over five screenshots")
        finally:
            context.close()

    def save_environment(self) -> None:
        """Record environment and source provenance only during explicit baseline updates."""
        revision = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=ROOT, check=True, capture_output=True, text=True,
        ).stdout.strip()
        VISUAL_SNAPSHOTS.mkdir(parents=True, exist_ok=True)
        (VISUAL_SNAPSHOTS / "environment.json").write_text(
            json.dumps({"source_commit": revision, "environment": self.environment}, indent=2) + "\n",
            encoding="utf-8",
        )


def _assert_visual(actual: bytes, expected: Path, result_dir: Path) -> None:
    """Compare screenshots and retain a readable diff artifact after a mismatch."""
    with Image.open(expected) as reference, Image.open(io.BytesIO(actual)) as received:
        width, height = max(reference.width, received.width), max(reference.height, received.height)
        before, after = Image.new("RGB", (width, height), "white"), Image.new("RGB", (width, height), "white")
        before.paste(reference.convert("RGB"))
        after.paste(received.convert("RGB"))
        difference = ImageChops.difference(before, after)
        red, green, blue = difference.split()
        mask = ImageChops.lighter(ImageChops.lighter(red, green), blue).point([0] + [255] * 255)
        changed = mask.histogram()[255]
        if changed or reference.size != received.size:
            result_dir.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(expected, result_dir / "expected.png")
            (result_dir / "actual.png").write_bytes(actual)
            overlay = Image.blend(before, Image.new("RGB", before.size, "magenta"), 0.65)
            Image.composite(overlay, before, mask).save(result_dir / "diff.png")
            details = {
                "changed_pixels": changed, "total_pixels": width * height,
                "expected_size": reference.size, "actual_size": received.size,
                "changed_bounds": mask.getbbox(),
            }
            (result_dir / "difference.json").write_text(json.dumps(details, indent=2) + "\n", encoding="utf-8")
            raise AssertionError(f"Visual mismatch: {details}; artifacts: {result_dir}")


@pytest.fixture(scope="module")
def visual_session(request: pytest.FixtureRequest) -> Iterator[HtmlVisualSession]:
    """Share the pinned browser without sharing page state across document cases."""
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        try:
            session = HtmlVisualSession(browser, request.config.getoption("--visual-update"))
            yield session
        finally:
            browser.close()


@pytest.mark.visual
@pytest.mark.parametrize("case_name", VISUAL_CASES)
def test_html_render_matches_visual_snapshot(
    case_name: str, tmp_path: Path, visual_session: HtmlVisualSession,
) -> None:
    """Protect user-visible layout while allowing equivalent HTML/CSS refactors."""
    case_dir, markdown = CASES[case_name]
    copied_case_dir = tmp_path / case_name
    shutil.copytree(case_dir, copied_case_dir)
    if case_name == "bilingual_captions":
        # This fixture references ../crossrefs/figure.svg; preserve that actual
        # resource layout instead of accepting a broken-image screenshot.
        shutil.copytree(CASES["crossrefs"][0], tmp_path / "crossrefs")
    output = copied_case_dir / "rendered.html"
    build_case(copied_case_dir, markdown, "html", output)
    baseline = VISUAL_SNAPSHOTS / f"{case_name}.png"
    actual = visual_session.capture(output)
    if visual_session.update:
        baseline.parent.mkdir(parents=True, exist_ok=True)
        baseline.write_bytes(actual)
        visual_session.save_environment()
        LOGGER.info("Updated visual baseline: %s", baseline)
    else:
        if not baseline.exists():
            raise AssertionError(f"Visual baseline is missing: {baseline}; use --visual-update explicitly")
        _assert_visual(actual, baseline, VISUAL_RESULTS / case_name)


@pytest.mark.visual
def test_equivalent_css_passes_but_one_visible_pixel_fails(
    tmp_path: Path, visual_session: HtmlVisualSession,
) -> None:
    """Independently exercise the intended CSS-refactor contract and strict visual detection."""
    document = tmp_path / "comparison" / "page.html"
    document.parent.mkdir()
    before = '<style>#marker {width:1px; height:1px; background:black}</style><p>Alpha <em>beta</em></p><div id="marker"></div>'
    after = '<style>/* equivalent presentation */ #marker {background:black; height:1px; width:1px}</style><p>Alpha <em>beta</em></p><div id="marker"></div>'
    document.write_text(before, encoding="utf-8")
    baseline = tmp_path / "baseline.png"
    baseline.write_bytes(visual_session.capture(document))
    document.write_text(after, encoding="utf-8")
    assert semantic_html(before) == semantic_html(after)
    _assert_visual(visual_session.capture(document), baseline, tmp_path / "equivalent")
    document.write_text(after.replace("background:black", "background:red"), encoding="utf-8")
    with pytest.raises(AssertionError, match="Visual mismatch"):
        _assert_visual(visual_session.capture(document), baseline, tmp_path / "changed")
    details = json.loads((tmp_path / "changed/difference.json").read_text(encoding="utf-8"))
    assert details["changed_pixels"] == 1
