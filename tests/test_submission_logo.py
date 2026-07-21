from pathlib import Path

from PIL import Image


ROOT = Path(__file__).resolve().parents[1]
LOGO = ROOT / "docs" / "submission" / "verifierforge-logo.jpg"


def test_submission_logo_is_high_resolution_landscape_jpeg() -> None:
    with Image.open(LOGO) as image:
        assert image.format == "JPEG"
        assert image.mode == "RGB"
        assert image.width == 2400
        assert image.width / image.height > 3.5
        assert image.getpixel((0, 0)) == (255, 255, 255)


def test_submission_logo_contains_brand_colors() -> None:
    with Image.open(LOGO) as image:
        colors = image.resize((240, 60)).getdata()

    assert any(blue > 150 and red < 80 for red, _green, blue in colors)
    assert any(green > 90 and blue < 180 for _red, green, blue in colors)
    assert any(max(red, green, blue) < 80 for red, green, blue in colors)
