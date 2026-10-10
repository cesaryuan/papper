# /// script
# requires-python = ">=3.12"
# dependencies = ["pydantic-settings>=2.0", "playwright==1.58.0", "Pillow>=11.0"]
# ///
"""Compare Skia CPU rendering and Chromium output.

Run `uv run --script tools/papper-dev/benchmarks/svg-skia/compare.py` from the
repository after building the Rust benchmark. The default inputs are the two
original report diagrams. Each renderer receives unchanged SVGs at native size;
Chromium uses a software canvas in an owned browser with GPU disabled. Save PNGs,
stage timings and pixel differences to the configured output directory. Render
temporary copies without filter attributes to verify that Skia applies shadows.
"""

import base64
import json
import re
import subprocess
from pathlib import Path
from statistics import median

from PIL import Image, ImageChops, ImageStat
from playwright.sync_api import sync_playwright
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Configure isolated benchmark inputs, executables and output artifacts."""

    model_config = SettingsConfigDict(cli_parse_args=True, cli_kebab_case=True)
    sources: list[Path] = Field(default=["critical_rerouting.svg", "critical_sp_gnn.svg"])  # Original diagrams
    renderer: Path = Field(default="target/svg-skia-benchmark/release/papper-svg-skia-benchmark.exe")  # CPU Skia binary
    output: Path = Field(default="target/svg-skia-benchmark/results")  # Generated PNGs and JSON
    repeat: int = Field(default=3, ge=1)  # Fresh parses per renderer
    timeout: float = Field(default=15.0, gt=0.0)  # Seconds allowed for each Skia run


class Comparison:
    """Keep artifact paths and measurements together across rendering stages."""

    def __init__(self, settings: Settings) -> None:
        """Resolve executable and artifact paths without changing original inputs."""
        self.settings = settings
        self.output = settings.output.resolve()
        self.output.mkdir(parents=True, exist_ok=True)
        self.renderer = settings.renderer.resolve()

    def skia(self, source: Path, output: Path, repeat: int) -> list[dict]:
        """Render fresh DOMs on CPU and collect the Rust stage timings."""
        result = subprocess.run(
            [str(self.renderer), "--source", str(source), "--output", str(output), "--repeat", str(repeat)],
            capture_output=True, text=True, check=True, timeout=self.settings.timeout,
        )
        return [json.loads(line) for line in result.stdout.splitlines() if line]

    def chromium(self, page, source: Path, output: Path) -> list[dict]:
        """Force native-size software rasterization and PNG encoding in Chromium."""
        measurements = []
        svg = source.read_text(encoding="utf-8")
        for repetition in range(1, self.settings.repeat + 1):
            result = page.evaluate("""async source => {
                const started = performance.now();
                const url = URL.createObjectURL(new Blob([source], {type: 'image/svg+xml'}));
                try {
                    const image = new Image();
                    image.src = url;
                    await image.decode();
                    const decoded = performance.now();
                    const canvas = document.createElement('canvas');
                    canvas.width = image.naturalWidth;
                    canvas.height = image.naturalHeight;
                    canvas.getContext('2d', {willReadFrequently: true}).drawImage(image, 0, 0);
                    const png = canvas.toDataURL('image/png');
                    return {width: canvas.width, height: canvas.height,
                        decode_ms: decoded - started,
                        render_and_encode_ms: performance.now() - decoded,
                        total_ms: performance.now() - started, png};
                } finally { URL.revokeObjectURL(url); }
            }""", svg)
            png = base64.b64decode(result.pop("png").split(",", 1)[1])
            result.update(repetition=repetition, png_bytes=len(png))
            measurements.append(result)
            if repetition == self.settings.repeat:
                output.write_bytes(png)
        return measurements

    @staticmethod
    def pixels(left: Path, right: Path) -> dict:
        """Compare native RGBA pixels and visible RGB differences on a white page."""
        with Image.open(left) as image:
            first = image.convert("RGBA")
        with Image.open(right) as image:
            second = image.convert("RGBA")
        if first.size != second.size:
            raise ValueError(f"Renderer dimensions differ: {first.size} vs {second.size}")
        rgba_diff = ImageChops.difference(first, second)
        white = Image.new("RGBA", first.size, "white")
        rgb_diff = ImageChops.difference(
            Image.alpha_composite(white, first).convert("RGB"),
            Image.alpha_composite(white, second).convert("RGB"),
        )
        red, green, blue = rgb_diff.split()
        histogram = ImageChops.lighter(ImageChops.lighter(red, green), blue).histogram()
        pixels = first.width * first.height
        return {
            "size": first.size,
            "rgba_mean_abs_error": ImageStat.Stat(rgba_diff).mean,
            "white_rgb_mean_abs_error": ImageStat.Stat(rgb_diff).mean,
            "white_exact_pixel_percent": 100 * histogram[0] / pixels,
            "white_pixels_difference_above_10_percent": 100 * sum(histogram[11:]) / pixels,
        }

    def run(self) -> None:
        """Publish evidence after each input so interrupted comparisons retain work."""
        report = {"method": "Original native SVG, transparent CPU surface, full PNG encoding", "sources": []}
        with sync_playwright() as runtime:
            browser = runtime.chromium.launch(headless=True, channel="chromium", args=["--disable-gpu"])
            try:
                report["chromium_version"] = browser.version
                page = browser.new_page()
                for path in self.settings.sources:
                    source = path.resolve()
                    name = source.stem
                    skia_png = self.output / f"{name}-skia.png"
                    browser_png = self.output / f"{name}-chromium.png"
                    skia = self.skia(source, skia_png, self.settings.repeat)
                    chromium = self.chromium(page, source, browser_png)
                    quality = self.pixels(skia_png, browser_png)
                    # Diagnostic-only copies expose skipped filters without editing authored SVGs.
                    stripped, filters = re.subn(r'\sfilter="[^"]*"', '', source.read_text(encoding="utf-8"))
                    diagnostic = self.output / f"{name}-no-filter.svg"
                    diagnostic.write_text(stripped, encoding="utf-8")
                    unfiltered_png = self.output / f"{name}-no-filter.png"
                    self.skia(diagnostic, unfiltered_png, 1)
                    item = {
                        "source": str(source), "skia": skia, "chromium": chromium,
                        "skia_median_ms": median(row["total_ms"] for row in skia),
                        "chromium_median_ms": median(row["total_ms"] for row in chromium),
                        "quality": quality, "removed_filter_uses": filters,
                        "filter_control_difference": self.pixels(skia_png, unfiltered_png),
                    }
                    print(f"[svg-comparison] {name}: Skia {item['skia_median_ms']:.1f}ms, Chromium {item['chromium_median_ms']:.1f}ms", flush=True)
                    report["sources"].append(item)
                    (self.output / "comparison.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
                    print(f"[svg-comparison] Saved {name}", flush=True)
            finally:
                browser.close()


def main() -> None:
    """Run the configured experiment using uv-managed Python dependencies."""
    Comparison(Settings()).run()


if __name__ == "__main__":
    main()
