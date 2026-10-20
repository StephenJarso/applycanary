"""Resume extraction.

Pulls out both text *and* layout signals, because real ATS parsers fail on
structure far more often than on wording: a two-column layout gets read in the
wrong order, table cells get flattened or dropped, and text in headers/footers is
frequently ignored entirely. The rule engine in `app/pipeline/ats_rules.py`
consumes these signals.
"""

from __future__ import annotations

import logging
import re
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

log = logging.getLogger(__name__)

SUPPORTED = {".pdf", ".docx", ".txt", ".md"}

# Headings ATS parsers reliably recognise. Creative substitutes ("Where I've
# Made An Impact") often cause the whole block to be misfiled.
CANONICAL_SECTIONS = {
    "experience": (
        "experience", "work experience", "professional experience",
        "employment", "employment history", "work history", "career history",
    ),
    "education": ("education", "academic background", "qualifications"),
    "skills": ("skills", "technical skills", "core competencies", "competencies"),
    "projects": ("projects", "personal projects", "selected projects"),
    "summary": ("summary", "professional summary", "profile", "objective", "about"),
    "certifications": ("certifications", "certificates", "licenses"),
}

_EMAIL = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")
_PHONE = re.compile(r"(?:\+?\d{1,3}[\s.-]?)?(?:\(?\d{2,4}\)?[\s.-]?){2,4}\d{2,4}")
_URL = re.compile(r"https?://[^\s)>\]]+|(?:www\.|linkedin\.com/|github\.com/)[^\s)>\]]+")


@dataclass(slots=True)
class ParsedResume:
    path: str = ""
    file_type: str = ""
    text: str = ""
    pages: int = 0

    # --- layout signals ---
    has_text_layer: bool = True          # False => scanned image, unreadable by ATS
    table_count: int = 0
    has_multi_column: bool = False
    header_footer_text: list[str] = field(default_factory=list)
    image_count: int = 0
    fonts: set[str] = field(default_factory=set)
    bullet_glyphs: set[str] = field(default_factory=set)
    template: dict = field(default_factory=dict)

    # --- content signals ---
    sections_found: dict[str, str] = field(default_factory=dict)
    unknown_headings: list[str] = field(default_factory=list)
    emails: list[str] = field(default_factory=list)
    phones: list[str] = field(default_factory=list)
    urls: list[str] = field(default_factory=list)
    parse_errors: list[str] = field(default_factory=list)

    @property
    def word_count(self) -> int:
        return len(self.text.split())

    @property
    def words(self) -> list[str]:
        return re.findall(r"[a-zA-Z][a-zA-Z0-9+#.\-]*", self.text.lower())


def parse_resume(path: str | Path) -> ParsedResume:
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"resume not found: {p}")
    ext = p.suffix.lower()
    if ext not in SUPPORTED:
        raise ValueError(f"unsupported resume type {ext!r}; use one of {sorted(SUPPORTED)}")

    if ext == ".pdf":
        parsed = _parse_pdf(p)
    elif ext == ".docx":
        parsed = _parse_docx(p)
    else:
        parsed = _parse_text(p)

    _extract_content_signals(parsed)
    return parsed


# ---------------------------------------------------------------- pdf


def _parse_pdf(p: Path) -> ParsedResume:
    import pdfplumber

    out = ParsedResume(path=str(p), file_type="pdf")
    chunks: list[str] = []

    with pdfplumber.open(str(p)) as pdf:
        out.pages = len(pdf.pages)
        for page in pdf.pages:
            try:
                chunks.append(page.extract_text() or "")
                words = page.extract_words() or []
                try:
                    out.table_count += len(page.find_tables() or [])
                except Exception:  # noqa: BLE001 - table finder is best-effort
                    pass
                out.image_count += len(page.images or [])
                for ch in page.chars or []:
                    if fn := ch.get("fontname"):
                        out.fonts.add(str(fn).split("+")[-1])
                if _detect_pdf_columns(words, float(page.width), float(page.height)):
                    out.has_multi_column = True
                out.header_footer_text.extend(
                    _pdf_edge_text(words, float(page.height))
                )
                if not out.template:
                    out.template = _pdf_template(page)
            except Exception as exc:  # noqa: BLE001
                out.parse_errors.append(f"page parse failed: {exc}")

    out.text = "\n".join(chunks).strip()
    # A PDF with pages but effectively no extractable text is a scanned image.
    out.has_text_layer = len(out.text) >= 50
    return out


def _detect_pdf_columns(words: list[dict], width: float, height: float) -> bool:
    """Detect a genuine two-column layout via a persistent vertical gutter.

    Looks for a band of x-positions in the middle of the page that no word
    crosses, while substantial text sits on both sides. Checked against word
    spans rather than a naive left/right word count, which would flag ordinary
    right-aligned dates as a column.
    """
    if len(words) < 40:
        return False
    bins = 50
    occupied = [False] * bins
    for w in words:
        try:
            x0, x1 = float(w["x0"]), float(w["x1"])
        except (KeyError, TypeError, ValueError):
            continue
        lo = max(0, min(bins - 1, int(x0 / width * bins)))
        hi = max(0, min(bins - 1, int(x1 / width * bins)))
        for i in range(lo, hi + 1):
            occupied[i] = True

    # Only the middle 30-70% of the width can be a real gutter.
    lo_b, hi_b = int(bins * 0.30), int(bins * 0.70)
    run = best_run = 0
    for i in range(lo_b, hi_b):
        run = 0 if occupied[i] else run + 1
        best_run = max(best_run, run)
    if best_run < 3:
        return False

    left = sum(1 for w in words if float(w.get("x1", 0)) < width * 0.45)
    right = sum(1 for w in words if float(w.get("x0", width)) > width * 0.55)
    # Both sides must carry real content, not just a sidebar of icons.
    return left >= 15 and right >= 15


def _pdf_edge_text(words: list[dict], height: float) -> list[str]:
    """Text in the top/bottom 6% of the page, which many ATS parsers discard."""
    top_band, bottom_band = height * 0.06, height * 0.94
    found: list[str] = []
    for w in words:
        try:
            top, bottom = float(w["top"]), float(w["bottom"])
        except (KeyError, TypeError, ValueError):
            continue
        if top < top_band or bottom > bottom_band:
            if text := str(w.get("text", "")).strip():
                found.append(text)
    return found


# ---------------------------------------------------------------- docx


def _parse_docx(p: Path) -> ParsedResume:
    import docx

    out = ParsedResume(path=str(p), file_type="docx")
    doc = docx.Document(str(p))

    lines = [para.text for para in doc.paragraphs]

    for table in doc.tables:
        out.table_count += 1
        for row in table.rows:
            for cell in row.cells:
                if cell.text.strip():
                    lines.append(cell.text)

    for section in doc.sections:
        for part in (section.header, section.footer):
            if part is None:
                continue
            for para in part.paragraphs:
                if para.text.strip():
                    out.header_footer_text.append(para.text.strip())

    try:
        out.image_count = len(doc.inline_shapes)
    except Exception:  # noqa: BLE001
        pass

    for para in doc.paragraphs:
        for run in para.runs:
            if run.font is not None and run.font.name:
                out.fonts.add(run.font.name)

    # A docx has no page count without rendering; approximate for length checks.
    out.text = "\n".join(lines).strip()
    out.pages = max(1, round(out.word_count / 500)) if out.word_count else 0
    out.has_text_layer = bool(out.text)
    # Multi-column in docx is usually implemented as a borderless layout table.
    out.has_multi_column = _docx_layout_table(doc)
    out.template = _docx_template(doc)
    return out


def _friendly_font(name: str) -> str:
    raw = re.sub(r"^[A-Z]{6}\+", "", str(name or "")).strip()
    low = raw.lower().replace(" ", "")
    if "timesnewroman" in low or low.startswith("times") or "times-roman" in low:
        return "Times New Roman"
    if "arial" in low:
        return "Arial"
    if "calibri" in low:
        return "Calibri"
    if "georgia" in low:
        return "Georgia"
    if "cambria" in low:
        return "Cambria"
    if "garamond" in low:
        return "Garamond"
    if "courier" in low:
        return "Courier New"
    if "helvetica" in low:
        return "Helvetica"
    return raw or "Calibri"


def _pdf_template(page) -> dict:  # noqa: ANN001
    """Recover the visual template of a PDF page from its glyphs."""
    chars = page.chars or []
    words = page.extract_words() or []
    sizes = Counter(
        round(float(c.get("size") or 0), 1)
        for c in chars
        if c.get("size")
    )
    fonts = Counter(
        _friendly_font(str(c.get("fontname") or ""))
        for c in chars
        if c.get("fontname")
    )
    body_size = sizes.most_common(1)[0][0] if sizes else 10.5
    body_font = fonts.most_common(1)[0][0] if fonts else "Times New Roman"
    larger = [s for s in sizes if s > body_size + 0.4]
    name_size = max(larger) if larger else body_size + 6
    heading = [s for s in larger if s < name_size]
    heading_size = Counter(heading).most_common(1)[0][0] if heading else body_size + 1

    left = min((float(w["x0"]) for w in words), default=50.0)
    top = min((float(w["top"]) for w in words), default=40.0)
    right = page.width - max((float(w["x1"]) for w in words), default=page.width - 50)
    first_top = top
    first_line = [
        w for w in words
        if float(w.get("top", 0)) <= first_top + max(body_size * 1.6, 12)
    ]
    centered = False
    if first_line:
        cx = sum((float(w["x0"]) + float(w["x1"])) / 2 for w in first_line) / len(first_line)
        centered = abs(cx - float(page.width) / 2) < float(page.width) * 0.06

    return {
        "file_type": "pdf",
        "page_width_pt": round(float(page.width), 1),
        "page_height_pt": round(float(page.height), 1),
        "margin_top_pt": round(max(18.0, top), 1),
        "margin_left_pt": round(max(18.0, left), 1),
        "margin_right_pt": round(max(18.0, right), 1),
        "margin_bottom_pt": round(max(18.0, top), 1),
        "body_font": body_font,
        "body_size_pt": round(body_size, 1),
        "name_font": body_font,
        "name_size_pt": round(name_size, 1),
        "heading_font": body_font,
        "heading_size_pt": round(heading_size, 1),
        "name_align": "center" if centered else "left",
    }


def _docx_template(doc) -> dict:  # noqa: ANN001
    """Read page setup and typography from the uploaded DOCX."""
    sec = doc.sections[0] if doc.sections else None
    width = getattr(getattr(sec, "page_width", None), "pt", 595.0)
    height = getattr(getattr(sec, "page_height", None), "pt", 842.0)
    top = getattr(getattr(sec, "top_margin", None), "pt", 40.0)
    left = getattr(getattr(sec, "left_margin", None), "pt", 50.0)
    right = getattr(getattr(sec, "right_margin", None), "pt", 50.0)
    bottom = getattr(getattr(sec, "bottom_margin", None), "pt", 40.0)

    normal = doc.styles.get("Normal", None)
    normal_font = getattr(getattr(normal, "font", None), "name", None)
    normal_size = getattr(getattr(getattr(normal, "font", None), "size", None), "pt", None)
    body_font = _friendly_font(normal_font or "Calibri")
    body_size = float(normal_size or 11.0)

    paras = [p for p in doc.paragraphs if p.text.strip()]
    name_font, name_size, name_align = body_font, body_size + 5, "left"
    if paras:
        first = paras[0]
        first_run = next((r for r in first.runs if r.text.strip()), first.runs[0] if first.runs else None)
        if first_run is not None:
            name_font = _friendly_font(first_run.font.name or body_font)
            name_size = float(getattr(first_run.font.size, "pt", None) or name_size)
        align = str(first.paragraph_format.alignment or "").lower()
        name_align = "center" if "center" in align else "left"

    heading_font, heading_size = body_font, body_size + 1
    for para in paras[1:]:
        text = para.text.strip()
        if len(text) <= 40 and (text.isupper() or text.lower().rstrip(":") in {
            "experience", "work experience", "education", "skills",
            "projects", "summary", "profile", "certifications",
        }):
            run = next((r for r in para.runs if r.text.strip()), None)
            if run is not None:
                heading_font = _friendly_font(run.font.name or body_font)
                heading_size = float(getattr(run.font.size, "pt", None) or heading_size)
            break

    return {
        "file_type": "docx",
        "page_width_pt": round(float(width), 1),
        "page_height_pt": round(float(height), 1),
        "margin_top_pt": round(float(top), 1),
        "margin_left_pt": round(float(left), 1),
        "margin_right_pt": round(float(right), 1),
        "margin_bottom_pt": round(float(bottom), 1),
        "body_font": body_font,
        "body_size_pt": round(body_size, 1),
        "name_font": name_font,
        "name_size_pt": round(name_size, 1),
        "heading_font": heading_font,
        "heading_size_pt": round(heading_size, 1),
        "name_align": name_align,
    }


def _docx_layout_table(doc) -> bool:  # noqa: ANN001
    """A wide 2-3 column table holding most of the document is a layout hack."""
    for table in doc.tables:
        cols = len(table.columns) if table.columns else 0
        if 2 <= cols <= 3:
            chars = sum(len(c.text) for r in table.rows for c in r.cells)
            if chars > 400:
                return True
    return False


# ---------------------------------------------------------------- plain text


def _parse_text(p: Path) -> ParsedResume:
    text = p.read_text(encoding="utf-8", errors="replace").strip()
    return ParsedResume(
        path=str(p),
        file_type=p.suffix.lower().lstrip("."),
        text=text,
        pages=max(1, round(len(text.split()) / 500)) if text else 0,
        has_text_layer=bool(text),
    )


# ---------------------------------------------------------------- content


def _title_like(line: str) -> bool:
    """True when every word starts uppercase, ignoring short joining words.

    `str.istitle()` is too strict for real headings: an apostrophe splits a word
    internally, so "Where I've Made An Impact" reports False and a genuinely
    creative heading slips past the check.
    """
    if line.isupper():
        return True
    joiners = {"a", "an", "the", "and", "or", "of", "in", "at", "to", "for", "my", "i"}
    words = [w for w in re.split(r"[\s/&-]+", line) if w]
    if not words:
        return False
    significant = 0
    for word in words:
        letters = [c for c in word if c.isalpha()]
        if not letters:
            continue
        if word.lower().strip(".,'") in joiners:
            continue
        significant += 1
        if not letters[0].isupper():
            return False
    return significant > 0


def _extract_content_signals(out: ParsedResume) -> None:
    out.emails = list(dict.fromkeys(_EMAIL.findall(out.text)))
    out.urls = list(dict.fromkeys(_URL.findall(out.text)))
    out.phones = [
        m.group(0).strip()
        for m in _PHONE.finditer(out.text)
        if sum(c.isdigit() for c in m.group(0)) >= 9
    ]

    alias_to_key = {
        alias: key for key, aliases in CANONICAL_SECTIONS.items() for alias in aliases
    }

    for index, raw_line in enumerate(out.text.splitlines()):
        line = raw_line.strip()
        if not line or len(line) > 60:
            continue
        probe = re.sub(r"[^a-z\s&/]", "", line.lower()).strip()
        if not probe:
            continue
        if key := alias_to_key.get(probe):
            out.sections_found.setdefault(key, line)
            continue
        # Heading-shaped (short, title/upper case, no sentence punctuation) but
        # not recognisable: a likely ATS misfile.
        words = line.split()
        looks_heading = (
            1 <= len(words) <= 5
            and not line.endswith((".", ",", ";", ":"))
            # A comma almost always means a role/employer or city line
            # ("Backend Engineer, Bitwise"), not a section heading.
            and "," not in line
            # The first non-empty line is conventionally the candidate's name.
            # Reporting it as a bad heading is confusing and wrong.
            and index > 0
            and _title_like(line)
            and not _EMAIL.search(line)
            and not any(ch.isdigit() for ch in line)
        )
        if looks_heading and probe not in alias_to_key:
            out.unknown_headings.append(line)

    for glyph in ("•", "▪", "◦", "‣", "·", "»", "→", "✦", "★", "-", "*"):
        if glyph in out.text:
            out.bullet_glyphs.add(glyph)
