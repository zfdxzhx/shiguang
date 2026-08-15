"""Starter exercise: implement private PDF intake in Checkpoint 1."""

from __future__ import annotations

from pathlib import Path
from typing import BinaryIO


class IntakeError(ValueError):
    pass


class PdfIntake:
    """The runnable shell keeps the storage boundary but no upload logic yet."""

    def __init__(self, documents_root: Path, **_: object):
        self.documents_root = Path(documents_root).resolve()
        self.documents_root.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def safe_name(filename: str | None) -> str:
        return Path(filename or "drawing.pdf").name

    def ingest(self, stream: BinaryIO, filename: str | None) -> dict:
        del stream, filename
        raise IntakeError(
            "TODO CP1: validate the PDF, calculate SHA256, count pages, and render private PNG pages"
        )

    @staticmethod
    def page_path(document: dict, page: int) -> Path:
        del document, page
        raise IntakeError("TODO CP1: return one rendered page without exposing the source PDF")

    @staticmethod
    def all_page_paths(document: dict) -> list[Path]:
        del document
        raise IntakeError("TODO CP1: return the private rendered page list")
