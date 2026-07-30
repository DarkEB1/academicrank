"""Shared dataclasses. LayoutLine is the hermetic-test currency: fixtures commit
lists of these as JSON, so splitter/scorer tests never touch pdfminer."""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class LayoutLine:
    """One text line with enough geometry to segment a bibliography.

    Coordinates are pdfminer's (origin bottom-left, y increases upward).
    `column` is assigned per page: 0 = left, 1 = right, None = single-column
    page or full-width box on a two-column page.
    """
    text: str
    x0: float
    y0: float
    x1: float
    y1: float
    font_size: float
    page: int
    column: int | None = None

    def to_json(self) -> dict:
        return {
            "text": self.text, "x0": self.x0, "y0": self.y0,
            "x1": self.x1, "y1": self.y1, "font_size": self.font_size,
            "page": self.page, "column": self.column,
        }

    @classmethod
    def from_json(cls, d: dict) -> "LayoutLine":
        return cls(**d)


@dataclass
class BibEntry:
    raw: str                      # normalised entry text
    key: str | None = None        # [17] / [Har77] / "17." key if the split was keyed
    doi: str | None = None
    arxiv_id: str | None = None
    year: int | None = None
    title_guess: str | None = None

    def to_json(self) -> dict:
        return {
            "raw": self.raw, "key": self.key, "doi": self.doi,
            "arxiv_id": self.arxiv_id, "year": self.year,
            "title_guess": self.title_guess,
        }

    @classmethod
    def from_json(cls, d: dict) -> "BibEntry":
        return cls(**d)


@dataclass
class BibResult:
    entries: list[BibEntry] = field(default_factory=list)
    method: str = ""              # bracket_numeric | alpha_key | ordinal | indent
    confidence: float = 0.0      # 1.0 structural; composite score otherwise
    structural: bool = False
    refused: bool = False
    refusal_reason: str | None = None
    n_pages: int = 0
    heading_text: str | None = None
    paper_title: str | None = None   # the uploaded paper's own title (editable)

    @classmethod
    def refusal(cls, reason: str, n_pages: int = 0) -> "BibResult":
        return cls(refused=True, refusal_reason=reason, n_pages=n_pages)

    def to_json(self) -> dict:
        return {
            "entries": [e.to_json() for e in self.entries],
            "method": self.method, "confidence": self.confidence,
            "structural": self.structural, "refused": self.refused,
            "refusal_reason": self.refusal_reason, "n_pages": self.n_pages,
            "heading_text": self.heading_text, "paper_title": self.paper_title,
        }

    @classmethod
    def from_json(cls, d: dict) -> "BibResult":
        d = dict(d)
        d["entries"] = [BibEntry.from_json(e) for e in d.get("entries", [])]
        return cls(**d)
