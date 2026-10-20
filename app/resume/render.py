"""Render tailored resume text without replacing the candidate's template.

Two paths:

* a DOCX upload is used as the template itself; paragraph text is replaced in
  place, so fonts, sizes, margins, alignment and page setup stay exactly as the
  candidate supplied them;
* PDF/text uploads cannot be edited in place, so their extracted page size,
  margins and typography are applied to a generated DOCX/PDF. This matches the
  visual system closely, but the source is not a reusable template.

The renderer also reports whether the original template was preserved so the UI
can be honest about it.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Any

from app.config import get_settings

log = logging.getLogger(__name__)

FONT = "Calibri"
FONT_PT = 11
NAME_PT = 16
HEADING_PT = 12

DEFAULT_TEMPLATE = {
    "file_type": "",
    "pages": 0,
    "page_width_pt": 595.0,
    "page_height_pt": 842.0,
    "margin_top_pt": 40.0,
    "margin_left_pt": 50.0,
    "margin_right_pt": 50.0,
    "margin_bottom_pt": 40.0,
    "body_font": FONT,
    "body_size_pt": FONT_PT,
    "name_font": FONT,
    "name_size_pt": NAME_PT,
    "heading_font": FONT,
    "heading_size_pt": HEADING_PT,
    "name_align": "left",
}

_SECTION_WORDS = {
    "summary", "professional summary", "profile", "objective", "about",
    "experience", "work experience", "professional experience", "employment",
    "employment history", "work history", "education", "skills",
    "technical skills", "core competencies", "projects", "certifications",
    "publications", "awards", "languages", "interests", "volunteering",
}

_BULLET = re.compile(r"^\s*[-*•‣▪●·]\s+")


def slugify(value: str, *, limit: int = 40) -> str:
    out = re.sub(r"[^a-zA-Z0-9]+", "_", value or "").strip("_").lower()
    return (out or "job")[:limit]


def _is_heading(line: str) -> bool:
    stripped = line.strip().rstrip(":")
    if not stripped or len(stripped) > 40 or _BULLET.match(line):
        return False
    if stripped.lower() in _SECTION_WORDS:
        return True
    return stripped.isupper() and len(stripped.split()) <= 4


def _template(value: dict | None) -> dict:
    merged = dict(DEFAULT_TEMPLATE)
    if isinstance(value, dict):
        merged.update({
            k: v for k, v in value.items()
            if k in DEFAULT_TEMPLATE and isinstance(v, (int, float, str))
        })
    return merged


def _num(template: dict, key: str) -> float:
    try:
        return float(template.get(key) or DEFAULT_TEMPLATE[key])
    except (TypeError, ValueError):
        return float(DEFAULT_TEMPLATE[key])


def _font_name(value: object, fallback: str = FONT) -> str:
    return str(value or fallback)


def _pdf_font(value: object) -> str:
    low = str(value or "").lower()
    if "times" in low or "roman" in low:
        return "Times"
    if "courier" in low:
        return "Courier"
    return "Helvetica"


def _set_paragraph_text(paragraph, text: str) -> None:  # noqa: ANN001
    """Replace text while keeping the paragraph's formatting and first run."""
    runs = paragraph.runs
    if not runs:
        paragraph.add_run(text)
        return
    runs[0].text = text
    for run in runs[1:]:
        run.text = ""


def write_docx_from_template(original: Path, text: str, path: Path) -> bool:
    """Replace paragraph text inside a copy of the original DOCX.

    Returns False when the line structure cannot be mapped one-to-one, in which
    case the caller falls back to the metadata renderer.
    """
    from docx import Document

    doc = Document(str(original))
    paragraphs = [p for p in doc.paragraphs if p.text.strip()]
    new_lines = [line.strip() for line in (text or "").splitlines() if line.strip()]
    if not paragraphs or len(paragraphs) != len(new_lines):
        return False

    for paragraph, new_line in zip(paragraphs, new_lines, strict=True):
        if paragraph.text.strip() != new_line:
            _set_paragraph_text(paragraph, new_line)

    path.parent.mkdir(parents=True, exist_ok=True)
    doc.save(str(path))
    return True


def write_docx(text: str, path: Path, template: dict | None = None) -> Path:
    from docx import Document
    from docx.enum.text import WD_ALIGN_PARAGRAPH
    from docx.shared import Pt

    meta = _template(template)
    doc = Document()

    style = doc.styles["Normal"]
    body_font = _font_name(meta["body_font"])
    body_size = Pt(_num(meta, "body_size_pt"))
    style.font.name = body_font
    style.font.size = body_size
    rpr = style.element.get_or_add_rPr()
    rfonts = rpr.get_or_add_rFonts()
    rfonts.set(
        "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}eastAsia", body_font
    )

    for section in doc.sections:
        section.page_width = Pt(_num(meta, "page_width_pt"))
        section.page_height = Pt(_num(meta, "page_height_pt"))
        section.top_margin = Pt(_num(meta, "margin_top_pt"))
        section.bottom_margin = Pt(_num(meta, "margin_bottom_pt"))
        section.left_margin = Pt(_num(meta, "margin_left_pt"))
        section.right_margin = Pt(_num(meta, "margin_right_pt"))

    lines = (text or "").splitlines()
    first_written = False

    for raw in lines:
        line = raw.rstrip()
        if not line.strip():
            continue

        if not first_written:
            para = doc.add_paragraph()
            if meta.get("name_align") == "center":
                para.alignment = WD_ALIGN_PARAGRAPH.CENTER
            run = para.add_run(line.strip())
            run.bold = True
            run.font.name = _font_name(meta["name_font"], body_font)
            run.font.size = Pt(_num(meta, "name_size_pt"))
            first_written = True
            continue

        if _is_heading(line):
            para = doc.add_paragraph()
            run = para.add_run(line.strip().rstrip(":"))
            run.bold = True
            run.font.name = _font_name(meta["heading_font"], body_font)
            run.font.size = Pt(_num(meta, "heading_size_pt"))
            para.paragraph_format.space_before = Pt(8)
            para.paragraph_format.space_after = Pt(2)
            continue

        if _BULLET.match(line):
            para = doc.add_paragraph(
                _BULLET.sub("", line).strip(), style="List Bullet"
            )
            para.paragraph_format.space_after = Pt(2)
            for run in para.runs:
                run.font.name = body_font
                run.font.size = body_size
            continue

        para = doc.add_paragraph(line.strip())
        para.paragraph_format.space_after = Pt(2)
        for run in para.runs:
            run.font.name = body_font
            run.font.size = body_size

    path.parent.mkdir(parents=True, exist_ok=True)
    doc.save(str(path))
    return path


def write_pdf(text: str, path: Path, template: dict | None = None) -> Path:
    from fpdf import FPDF

    meta = _template(template)
    width = _num(meta, "page_width_pt")
    height = _num(meta, "page_height_pt")
    left = _num(meta, "margin_left_pt")
    top = _num(meta, "margin_top_pt")
    right = _num(meta, "margin_right_pt")
    bottom = _num(meta, "margin_bottom_pt")
    body_size = _num(meta, "body_size_pt")

    pdf = FPDF(unit="pt", format=(width, height))
    pdf.set_auto_page_break(auto=True, margin=bottom)
    pdf.add_page()
    pdf.set_margins(left, top, right)

    usable = width - left - right
    body_font = _pdf_font(meta["body_font"])
    name_font = _pdf_font(meta["name_font"])
    heading_font = _pdf_font(meta["heading_font"])
    first_written = False

    for raw in (text or "").splitlines():
        line = raw.rstrip()
        if not line.strip():
            pdf.ln(body_size * 0.5)
            continue

        safe = line.encode("latin-1", "replace").decode("latin-1")

        if not first_written:
            pdf.set_font(name_font, "B", _num(meta, "name_size_pt"))
            pdf.multi_cell(
                usable, _num(meta, "name_size_pt") + 4, safe.strip(),
                align="C" if meta.get("name_align") == "center" else "L",
                new_x="LMARGIN", new_y="NEXT",
            )
            first_written = True
            continue

        if _is_heading(line):
            pdf.ln(body_size * 0.5)
            pdf.set_font(heading_font, "B", _num(meta, "heading_size_pt"))
            pdf.multi_cell(
                usable, _num(meta, "heading_size_pt") + 3,
                safe.strip().rstrip(":"),
                new_x="LMARGIN", new_y="NEXT",
            )
            continue

        pdf.set_font(body_font, "", body_size)
        line_height = body_size * 1.2
        if _BULLET.match(line):
            body = _BULLET.sub("", safe).strip()
            pdf.multi_cell(
                usable, line_height,
                f"•  {body}".encode("latin-1", "replace").decode("latin-1"),
                new_x="LMARGIN", new_y="NEXT",
            )
        else:
            pdf.multi_cell(
                usable, line_height, safe.strip(),
                new_x="LMARGIN", new_y="NEXT",
            )

    path.parent.mkdir(parents=True, exist_ok=True)
    pdf.output(str(path))
    return path


def render_for_job(
    text: str,
    *,
    company: str,
    title: str,
    full_name: str = "",
    profile: Any = None,
) -> tuple[Path, Path, bool, str]:
    """Write both formats. Returns (docx, pdf, template_preserved, note)."""
    settings = get_settings()
    settings.ensure_dirs()

    stem = "_".join(
        p for p in (slugify(full_name or "resume", limit=24),
                    slugify(company, limit=24),
                    slugify(title, limit=32)) if p
    )
    base = settings.artifact_dir / stem
    docx_path = base.with_suffix(".docx")
    pdf_path = base.with_suffix(".pdf")

    meta = _template(getattr(profile, "resume_meta", None))
    original = Path(getattr(profile, "base_resume_path", "") or "")
    preserved = False
    note = ""

    if original.suffix.lower() == ".docx" and original.is_file():
        try:
            preserved = write_docx_from_template(original, text, docx_path)
        except Exception as exc:  # noqa: BLE001 - never lose the artifact entirely
            log.warning("could not reuse original DOCX template: %s", exc)
        if preserved:
            note = "Original DOCX template reused: fonts, margins, alignment and page setup preserved."
        else:
            write_docx(text, docx_path, template=meta)
            note = "Original DOCX could not be mapped line-for-line; extracted fonts and margins were matched."
    else:
        write_docx(text, docx_path, template=meta)
        if meta.get("file_type") == "pdf":
            note = (
                "Original was a PDF, so the tailored copy reuses its page size, "
                "margins and fonts. Check the page count against the original before submitting."
            )

    try:
        write_pdf(text, pdf_path, template=meta)
    except Exception as exc:  # noqa: BLE001
        log.warning("PDF render failed for %s (docx is unaffected): %s", stem, exc)
        pdf_path = Path()

    return docx_path, pdf_path, preserved, note
