"""Export the canonical 12-chapter Technical Deep Dive as one polished PDF."""

from __future__ import annotations

import argparse
import base64
import hashlib
import io
import json
import os
import re
import shutil
import subprocess
import threading
import time
from contextlib import contextmanager
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Iterator
from urllib.parse import quote, urlsplit
from urllib.request import Request, urlopen


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "docs" / "blog" / "technical-deep-dive.md"
FRONTEND = ROOT / "frontend"
DIST = FRONTEND / "dist"
OUTPUT = ROOT / "output" / "pdf" / "verifierforge-technical-deep-dive.pdf"
CHECKSUM = OUTPUT.with_suffix(".sha256")
EXPECTED_CHAPTERS = 12
EXPECTED_FIGURES = 6


def chapter_titles(markdown: str) -> list[str]:
    return re.findall(r"^## (.+)$", markdown, flags=re.MULTILINE)


def figure_sources(markdown: str) -> list[str]:
    return re.findall(r"!\[[^]]*\]\((\./figures/[^)]+\.svg)\)", markdown)


def validate_source() -> list[str]:
    markdown = SOURCE.read_text(encoding="utf-8")
    chapters = chapter_titles(markdown)
    figures = figure_sources(markdown)
    if len(chapters) != EXPECTED_CHAPTERS:
        raise RuntimeError(f"expected {EXPECTED_CHAPTERS} chapters, found {len(chapters)}")
    if len(figures) != EXPECTED_FIGURES or len(set(figures)) != EXPECTED_FIGURES:
        raise RuntimeError(
            f"expected {EXPECTED_FIGURES} unique figure references, found {len(set(figures))}"
        )
    for relative in figures:
        path = SOURCE.parent / relative.removeprefix("./")
        if not path.is_file():
            raise RuntimeError(f"missing figure: {path.relative_to(ROOT)}")
    return chapters


def find_chrome() -> Path:
    configured = os.environ.get("VF_CHROME_BIN")
    candidates = [
        Path(configured).expanduser() if configured else None,
        Path("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"),
        Path("/Applications/Chromium.app/Contents/MacOS/Chromium"),
    ]
    for candidate in candidates:
        if candidate and candidate.is_file():
            return candidate
    for name in ("google-chrome", "chromium", "chromium-browser"):
        resolved = shutil.which(name)
        if resolved:
            return Path(resolved)
    raise RuntimeError("Chrome/Chromium not found; set VF_CHROME_BIN")


class _SpaHandler(SimpleHTTPRequestHandler):
    def __init__(self, *args: object, **kwargs: object) -> None:
        super().__init__(*args, directory=str(DIST), **kwargs)

    def do_GET(self) -> None:  # noqa: N802 - stdlib handler API
        if urlsplit(self.path).path.rstrip("/") == "/tech":
            self.path = "/index.html"
        super().do_GET()

    def log_message(self, format: str, *args: object) -> None:
        return


@contextmanager
def serve_dist() -> Iterator[str]:
    server = ThreadingHTTPServer(("127.0.0.1", 0), _SpaHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        host, port = server.server_address
        yield f"http://{host}:{port}/tech?print=1"
    finally:
        server.shutdown()
        thread.join(timeout=5)
        server.server_close()


def build_frontend() -> None:
    subprocess.run(["npm", "run", "build"], cwd=FRONTEND, check=True)


class _CdpSession:
    def __init__(self, websocket_url: str) -> None:
        try:
            import websocket
        except ImportError as exc:
            raise RuntimeError(
                "PDF dependencies missing; run `python -m pip install -r requirements-docs.txt`"
            ) from exc
        self._socket = websocket.create_connection(
            websocket_url,
            timeout=15,
            origin="http://127.0.0.1",
            suppress_origin=True,
        )
        self._next_id = 1

    def call(self, method: str, params: dict[str, object] | None = None) -> dict[str, object]:
        request_id = self._next_id
        self._next_id += 1
        self._socket.send(json.dumps({"id": request_id, "method": method, "params": params or {}}))
        while True:
            message = json.loads(self._socket.recv())
            if message.get("id") != request_id:
                continue
            if "error" in message:
                raise RuntimeError(f"Chrome DevTools {method} failed: {message['error']}")
            return message.get("result", {})

    def close(self) -> None:
        self._socket.close()


def _wait_for_devtools(profile: Path, process: subprocess.Popen[bytes]) -> int:
    active_port = profile / "DevToolsActivePort"
    deadline = time.monotonic() + 20
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError(f"Chrome exited before DevTools started ({process.returncode})")
        if active_port.is_file():
            return int(active_port.read_text(encoding="utf-8").splitlines()[0])
        time.sleep(0.1)
    raise RuntimeError("Chrome DevTools did not start within 20 seconds")


def _new_page(port: int, url: str) -> str:
    request = Request(
        f"http://127.0.0.1:{port}/json/new?{quote(url, safe=':/?=&')}",
        method="PUT",
    )
    with urlopen(request, timeout=10) as response:
        payload = json.load(response)
    return str(payload["webSocketDebuggerUrl"])


def _wait_for_print_ready(session: _CdpSession) -> None:
    expression = """
      document.readyState === 'complete' &&
      document.querySelectorAll('.tech-chapter').length === 12 &&
      [...document.querySelectorAll('.tech-chapter')].every(node => node.open) &&
      [...document.images].every(image => image.complete && image.naturalWidth > 0) &&
      document.fonts.status === 'loaded'
    """
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        result = session.call(
            "Runtime.evaluate",
            {"expression": expression, "returnByValue": True},
        )
        value = result.get("result", {})
        if isinstance(value, dict) and value.get("value") is True:
            return
        time.sleep(0.25)
    raise RuntimeError("print page did not load all 12 chapters, figures, and fonts")


def print_raw_pdf(chrome: Path, url: str, destination: Path, profile: Path) -> None:
    command = [
        str(chrome),
        "--headless=new",
        "--disable-background-networking",
        "--disable-default-apps",
        "--disable-extensions",
        "--disable-gpu",
        "--hide-scrollbars",
        "--no-first-run",
        "--remote-allow-origins=*",
        "--remote-debugging-port=0",
        f"--user-data-dir={profile}",
        "about:blank",
    ]
    process = subprocess.Popen(command, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    session: _CdpSession | None = None
    try:
        port = _wait_for_devtools(profile, process)
        session = _CdpSession(_new_page(port, url))
        session.call("Page.enable")
        session.call("Runtime.enable")
        _wait_for_print_ready(session)
        session.call("Emulation.setEmulatedMedia", {"media": "print"})
        result = session.call(
            "Page.printToPDF",
            {
                "displayHeaderFooter": False,
                "generateDocumentOutline": True,
                "generateTaggedPDF": True,
                "preferCSSPageSize": True,
                "printBackground": True,
            },
        )
        encoded = result.get("data")
        if not isinstance(encoded, str):
            raise RuntimeError("Chrome DevTools returned no PDF data")
        destination.write_bytes(base64.b64decode(encoded))
    finally:
        if session is not None:
            session.close()
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)


def add_page_furniture(raw_pdf: Path, destination: Path) -> None:
    try:
        from pypdf import PdfReader, PdfWriter
        from reportlab.lib.colors import HexColor
        from reportlab.pdfgen import canvas
    except ImportError as exc:
        raise RuntimeError(
            "PDF dependencies missing; run `python -m pip install -r requirements-docs.txt`"
        ) from exc

    reader = PdfReader(raw_pdf)
    writer = PdfWriter()
    total = len(reader.pages)
    for number, page in enumerate(reader.pages, start=1):
        width = float(page.mediabox.width)
        height = float(page.mediabox.height)
        layer = io.BytesIO()
        overlay = canvas.Canvas(layer, pagesize=(width, height))
        overlay.setStrokeColor(HexColor("#DCE7E8"))
        overlay.setFillColor(HexColor("#46606F"))
        if number > 1:
            overlay.setFont("Helvetica-Bold", 7)
            overlay.drawString(40, height - 25, "VERIFIERFORGE / TECHNICAL DEEP DIVE")
            overlay.line(40, height - 31, width - 40, height - 31)
        overlay.setFont("Helvetica", 7)
        overlay.drawString(40, 20, "12 CHAPTERS / EVIDENCE SNAPSHOT / 2026-07-21")
        overlay.drawRightString(width - 40, 20, f"{number} / {total}")
        overlay.save()
        layer.seek(0)
        page.merge_page(PdfReader(layer).pages[0])
        writer.add_page(page)

    writer.add_metadata(
        {
            "/Title": "VerifierForge Technical Deep Dive",
            "/Author": "VerifierForge",
            "/Subject": "Complete 12-chapter engineering evidence attachment",
            "/Keywords": "VerifierForge, GRPO, verifier, Forge Agent, held-out evaluation",
            "/Creator": "VerifierForge v0.40.0 export pipeline",
        }
    )
    destination.parent.mkdir(parents=True, exist_ok=True)
    with destination.open("wb") as handle:
        writer.write(handle)


def normalize_text(value: str) -> str:
    return " ".join(value.split())


def verify_pdf(path: Path, chapters: list[str]) -> tuple[int, str]:
    try:
        from pypdf import PdfReader
    except ImportError as exc:
        raise RuntimeError(
            "PDF dependencies missing; run `python -m pip install -r requirements-docs.txt`"
        ) from exc

    reader = PdfReader(path)
    if len(reader.pages) < EXPECTED_CHAPTERS + 2:
        raise RuntimeError(f"expected at least 14 pages, found {len(reader.pages)}")
    metadata = reader.metadata or {}
    if metadata.get("/Title") != "VerifierForge Technical Deep Dive":
        raise RuntimeError("PDF title metadata is missing")
    extracted = normalize_text("\n".join(page.extract_text() or "" for page in reader.pages))
    compact = "".join(extracted.split())
    missing = [title for title in chapters if normalize_text(title) not in extracted]
    if missing:
        raise RuntimeError(f"PDF is missing chapter text: {missing}")
    # Chromium's embedded Manrope font may expose the percent glyph as a
    # separate text run to pypdf, so verify the exact evidence numbers and
    # labels independently rather than weakening the content gate.
    for required in ("58.3", "78.3", "Gate C", "References"):
        if "".join(required.split()) not in compact:
            raise RuntimeError(f"PDF is missing required evidence text: {required}")
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    return len(reader.pages), digest


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--skip-build", action="store_true", help="reuse frontend/dist")
    parser.add_argument("--verify-only", action="store_true", help="verify the existing PDF")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    chapters = validate_source()
    if not args.verify_only:
        if not args.skip_build:
            build_frontend()
        if not (DIST / "index.html").is_file():
            raise RuntimeError("frontend/dist is missing; export without --skip-build")
        scratch_root = ROOT / "tmp" / "pdfs"
        scratch_root.mkdir(parents=True, exist_ok=True)
        import tempfile

        with tempfile.TemporaryDirectory(prefix="tech-export-", dir=scratch_root) as scratch:
            scratch_path = Path(scratch)
            raw_pdf = scratch_path / "technical-deep-dive.raw.pdf"
            with serve_dist() as url:
                print_raw_pdf(find_chrome(), url, raw_pdf, scratch_path / "chrome-profile")
            add_page_furniture(raw_pdf, OUTPUT)
    pages, digest = verify_pdf(OUTPUT, chapters)
    CHECKSUM.write_text(f"{digest}  {OUTPUT.name}\n", encoding="utf-8")
    print(f"PDF verified: {pages} pages, {len(chapters)} chapters, sha256={digest}")
    print(OUTPUT.relative_to(ROOT))


if __name__ == "__main__":
    main()
