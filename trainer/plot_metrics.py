"""Dependency-free PNG output for the D2 training curve artifact."""

from __future__ import annotations

import json
import math
import struct
import zlib
from pathlib import Path
from typing import Any, Iterable, Mapping


RGB = tuple[int, int, int]
_WHITE: RGB = (255, 255, 255)
_AXIS: RGB = (130, 140, 150)
_REWARD: RGB = (47, 111, 235)
_PASS: RGB = (18, 157, 102)
_ENTROPY: RGB = (235, 133, 47)


def load_metric_rows(path: Path) -> list[dict[str, float]]:
    """Read usable numeric rows from append-only ``metrics.jsonl``."""
    rows: list[dict[str, float]] = []
    try:
        lines = Path(path).read_text(encoding="utf-8").splitlines()
    except FileNotFoundError:
        return rows

    for line in lines:
        try:
            raw = json.loads(line)
            row = {
                key: float(raw[key])
                for key in ("step", "reward_mean", "pass_at_1", "entropy")
            }
        except (KeyError, TypeError, ValueError, json.JSONDecodeError):
            continue
        if all(math.isfinite(value) for value in row.values()):
            rows.append(row)
    return sorted(rows, key=lambda row: row["step"])


def render_curve(metrics_path: Path, destination: Path) -> Path:
    """Render reward, pass@1, and entropy to a compact valid PNG artifact."""
    return render_rows(load_metric_rows(metrics_path), destination)


def render_rows(rows: Iterable[Mapping[str, float]], destination: Path) -> Path:
    """Render supplied rows without importing matplotlib on a GPU worker."""
    width, height = 960, 540
    margin_left, margin_right, margin_top, margin_bottom = 62, 24, 32, 54
    pixels = bytearray(_WHITE * (width * height))

    left, right = margin_left, width - margin_right
    top, bottom = margin_top, height - margin_bottom
    _line(pixels, width, height, left, top, left, bottom, _AXIS)
    _line(pixels, width, height, left, bottom, right, bottom, _AXIS)

    materialized = list(rows)
    series = (
        ("reward_mean", _REWARD),
        ("pass_at_1", _PASS),
        ("entropy", _ENTROPY),
    )
    values = [float(row[key]) for row in materialized for key, _ in series if key in row]
    if materialized and values:
        minimum, maximum = min(values), max(values)
        if math.isclose(minimum, maximum):
            minimum -= 0.5
            maximum += 0.5

        steps = [float(row["step"]) for row in materialized]
        first_step, last_step = min(steps), max(steps)
        if math.isclose(first_step, last_step):
            last_step = first_step + 1

        for key, color in series:
            points = [
                (
                    round(left + (float(row["step"]) - first_step) / (last_step - first_step) * (right - left)),
                    round(bottom - (float(row[key]) - minimum) / (maximum - minimum) * (bottom - top)),
                )
                for row in materialized
                if key in row
            ]
            for start, end in zip(points, points[1:]):
                _line(pixels, width, height, *start, *end, color)
            for x, y in points:
                _dot(pixels, width, height, x, y, color)

    # A small color key avoids a heavyweight plotting dependency while keeping
    # the screenshot useful to the frontend teammate.
    for index, (_, color) in enumerate(series):
        x = left + index * 105
        for offset in range(28):
            _set_pixel(pixels, width, height, x + offset, 16, color)
            _set_pixel(pixels, width, height, x + offset, 17, color)

    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(_encode_png(width, height, pixels))
    return destination


def _set_pixel(pixels: bytearray, width: int, height: int, x: int, y: int, color: RGB) -> None:
    if 0 <= x < width and 0 <= y < height:
        offset = (y * width + x) * 3
        pixels[offset : offset + 3] = bytes(color)


def _dot(pixels: bytearray, width: int, height: int, x: int, y: int, color: RGB) -> None:
    for dx in range(-2, 3):
        for dy in range(-2, 3):
            if dx * dx + dy * dy <= 4:
                _set_pixel(pixels, width, height, x + dx, y + dy, color)


def _line(
    pixels: bytearray,
    width: int,
    height: int,
    start_x: int,
    start_y: int,
    end_x: int,
    end_y: int,
    color: RGB,
) -> None:
    """Draw a Bresenham line without needing a graphics dependency."""
    delta_x, delta_y = abs(end_x - start_x), -abs(end_y - start_y)
    step_x = 1 if start_x < end_x else -1
    step_y = 1 if start_y < end_y else -1
    error = delta_x + delta_y
    while True:
        _set_pixel(pixels, width, height, start_x, start_y, color)
        if start_x == end_x and start_y == end_y:
            return
        doubled_error = 2 * error
        if doubled_error >= delta_y:
            error += delta_y
            start_x += step_x
        if doubled_error <= delta_x:
            error += delta_x
            start_y += step_y


def _chunk(kind: bytes, payload: bytes) -> bytes:
    checksum = struct.pack(">I", zlib.crc32(kind + payload) & 0xFFFFFFFF)
    return struct.pack(">I", len(payload)) + kind + payload + checksum


def _encode_png(width: int, height: int, pixels: bytearray) -> bytes:
    rows = b"".join(b"\x00" + pixels[row * width * 3 : (row + 1) * width * 3] for row in range(height))
    return b"".join(
        (
            b"\x89PNG\r\n\x1a\n",
            _chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)),
            _chunk(b"IDAT", zlib.compress(rows, level=9)),
            _chunk(b"IEND", b""),
        )
    )
