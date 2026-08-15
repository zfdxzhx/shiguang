"""Private PDF intake and page rendering for user-selected documents."""

from __future__ import annotations

import hashlib
import re
import shutil
import subprocess
from pathlib import Path
from typing import BinaryIO
from uuid import uuid4


class IntakeError(ValueError):
    pass


class PdfIntake:
    def __init__(
        self,
        documents_root: Path,
        *,
        max_bytes: int = 50 * 1024 * 1024,
        max_pages: int = 40,
        dpi: int = 144,
    ):
        self.documents_root = Path(documents_root).resolve()
        self.documents_root.mkdir(parents=True, exist_ok=True)
        self.max_bytes = max_bytes
        self.max_pages = max_pages
        self.dpi = dpi

    @staticmethod
    def safe_name(filename: str | None) -> str:
        name = Path(filename or "drawing.pdf").name
        name = re.sub(r"[\x00-\x1f\x7f]", "", name).strip()
        if not name:
            name = "drawing.pdf"
        if len(name) > 180:
            stem = Path(name).stem[:160] or "drawing"
            name = stem + ".pdf"
        return name

    def _write_private_pdf(self, stream: BinaryIO, destination: Path) -> tuple[int, str]:
        digest = hashlib.sha256()
        total = 0
        with destination.open("wb") as output:
            while True:
                chunk = stream.read(1024 * 1024)
                if not chunk:
                    break
                total += len(chunk)
                if total > self.max_bytes:
                    raise IntakeError(f"PDF exceeds the {self.max_bytes // (1024 * 1024)} MB limit")
                digest.update(chunk)
                output.write(chunk)
        if total < 5:
            raise IntakeError("PDF is empty or truncated")
        with destination.open("rb") as source:
            if source.read(5) != b"%PDF-":
                raise IntakeError("uploaded content is not a PDF")
        return total, digest.hexdigest()

    @staticmethod
    def _page_count(path: Path) -> int:
        pdfinfo = shutil.which("pdfinfo")
        if pdfinfo:
            completed = subprocess.run(
                [pdfinfo, str(path)], capture_output=True, text=True, timeout=30, check=False
            )
            if completed.returncode == 0:
                match = re.search(r"^Pages:\s+(\d+)\s*$", completed.stdout, re.MULTILINE)
                if match:
                    return int(match.group(1))
            raise IntakeError("pdfinfo could not read this PDF")
        try:
            from pypdf import PdfReader  # type: ignore

            return len(PdfReader(str(path)).pages)
        except Exception as exc:  # pragma: no cover - classroom machines include pdfinfo
            raise IntakeError("cannot determine PDF page count; install pdfinfo") from exc

    def _render(self, source: Path, pages_dir: Path, page_count: int) -> list[Path]:
        renderer = shutil.which("pdftoppm")
        if not renderer:
            raise IntakeError("pdftoppm is required to render page images")
        pages_dir.mkdir(parents=True, exist_ok=True)
        completed = subprocess.run(
            [renderer, "-png", "-r", str(self.dpi), str(source), str(pages_dir / "page")],
            capture_output=True,
            text=True,
            timeout=120,
            check=False,
        )
        if completed.returncode != 0:
            raise IntakeError("PDF page rendering failed")
        pages = sorted(pages_dir.glob("page-*.png"))
        if len(pages) != page_count:
            raise IntakeError(f"rendered {len(pages)}/{page_count} pages")
        return pages

    def ingest(self, stream: BinaryIO, filename: str | None) -> dict:
        document_id = f"doc-{uuid4().hex}"
        private_dir = (self.documents_root / document_id).resolve()
        if private_dir.parent != self.documents_root:
            raise IntakeError("document storage path escaped its private root")
        private_dir.mkdir(parents=False, exist_ok=False)
        source = private_dir / "source.pdf"
        try:
            size_bytes, digest = self._write_private_pdf(stream, source)
            page_count = self._page_count(source)
            if page_count <= 0:
                raise IntakeError("PDF has no pages")
            if page_count > self.max_pages:
                raise IntakeError(f"PDF exceeds the {self.max_pages}-page limit")
            pages = self._render(source, private_dir / "pages", page_count)
            return {
                "id": document_id,
                "original_name": self.safe_name(filename),
                "sha256": digest,
                "size_bytes": size_bytes,
                "page_count": page_count,
                "status": "ready",
                "private_dir": str(private_dir),
                "page_paths": [str(path) for path in pages],
            }
        except Exception:
            shutil.rmtree(private_dir, ignore_errors=True)
            raise

    @staticmethod
    def page_path(document: dict, page: int) -> Path:
        if page < 1 or page > int(document["page_count"]):
            raise IntakeError("page number is outside this document")
        private_dir = Path(document["private_dir"]).resolve()
        candidate = (private_dir / "pages" / f"page-{page}.png").resolve()
        if private_dir not in candidate.parents or not candidate.is_file():
            raise IntakeError("rendered page is unavailable")
        return candidate

    @staticmethod
    def all_page_paths(document: dict) -> list[Path]:
        return [PdfIntake.page_path(document, page) for page in range(1, int(document["page_count"]) + 1)]

