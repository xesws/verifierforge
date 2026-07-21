"""Export the canonical VerifierForge wordmark as a submission-ready JPEG."""

from __future__ import annotations

import shutil
import subprocess
import tempfile
from pathlib import Path

from PIL import Image, ImageChops


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "assets" / "brand" / "verifierforge-wordmark.svg"
DESTINATION = ROOT / "docs" / "submission" / "verifierforge-logo.jpg"
THUMBNAIL_SIZE = 2400
OUTPUT_WIDTH = 2400
PADDING = 80


def export_logo() -> Path:
    """Render, tightly crop, and export the official wordmark on white."""
    qlmanage = shutil.which("qlmanage")
    if not qlmanage:
        raise RuntimeError("qlmanage is required to render the canonical SVG")

    with tempfile.TemporaryDirectory(prefix="vf-logo-") as temp_dir:
        temp = Path(temp_dir)
        subprocess.run(
            [
                qlmanage,
                "-t",
                "-s",
                str(THUMBNAIL_SIZE),
                "-o",
                str(temp),
                str(SOURCE),
            ],
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        rendered = temp / f"{SOURCE.name}.png"
        image = Image.open(rendered).convert("RGB")

        difference = ImageChops.difference(
            image, Image.new("RGB", image.size, "white")
        ).convert("L")
        content_box = difference.point(lambda value: 255 if value > 3 else 0).getbbox()
        if content_box is None:
            raise RuntimeError("rendered wordmark contains no visible content")

        content = image.crop(content_box)
        canvas = Image.new(
            "RGB",
            (content.width + 2 * PADDING, content.height + 2 * PADDING),
            "white",
        )
        canvas.paste(content, (PADDING, PADDING))
        output_height = round(canvas.height * OUTPUT_WIDTH / canvas.width)
        canvas = canvas.resize((OUTPUT_WIDTH, output_height), Image.Resampling.LANCZOS)

        DESTINATION.parent.mkdir(parents=True, exist_ok=True)
        canvas.save(
            DESTINATION,
            format="JPEG",
            quality=95,
            subsampling=0,
            optimize=True,
        )
    return DESTINATION


if __name__ == "__main__":
    print(export_logo().relative_to(ROOT))
