"""Read motions from an OpenSlides PDF export with pymupdf.

Layout of the export (pdfmake): every motion starts with its title in the largest font
("Motion 05: <title>"), followed by a metadata table on grey cells (Submitters / State /
Category) and the text. List bullets are small filled shapes, not characters, so list items are
recognised from those shapes; paragraphs from vertical gaps; bold/italic from the font name;
headings from a font size between body and title size.
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

import pymupdf

TITLE_RE = re.compile(r"^\s*\S+\s+([^:\s]+):\s*(.*)$", re.S)  # "Motion 05: 6. Title"
ORDERED_MARKER_RE = re.compile(r"^\d+[.)]$")
PAGE_NUMBER_RE = re.compile(r"^\s*(page\s*)?\d+(\s*/\s*\d+)?\s*$", re.I)
BOLD_FONT_RE = re.compile(r"bold|semibold|medium|black|heavy", re.I)
ITALIC_FONT_RE = re.compile(r"italic|oblique", re.I)
MARGIN = 30  # pt from the top/bottom edge where page numbers would sit


@dataclass
class Span:
    text: str
    bold: bool = False
    italic: bool = False


@dataclass
class Line:
    page: int
    x0: float
    y0: float
    y1: float
    size: float
    spans: list[Span]
    bullet_x: float | None = None  # x of the list bullet in front of the line
    ordered: str | None = None  # "1." if the line starts an ordered list item

    @property
    def text(self) -> str:
        return "".join(s.text for s in self.spans)


@dataclass
class PdfMotion:
    number: str
    title: str  # as printed after "Motion NN: " (agenda prefix still included)
    meta: dict[str, str] = field(default_factory=dict)  # metadata table, labels in lower case
    markdown: str = ""
    repeated_line: str | None = None  # a line that appears twice: sign of colliding amendments


def parse_pdf(path: Path) -> list[PdfMotion]:
    pages = [_page_lines(page, pno) for pno, page in enumerate(pymupdf.open(path))]
    sizes = Counter()
    for lines, _ in pages:
        for line in lines:
            sizes[round(line.size, 1)] += len(line.text)
    if not sizes:
        return []
    body_size = sizes.most_common(1)[0][0]
    title_size = max(sizes)

    motions: list[PdfMotion] = []
    title: list[Line] = []
    meta: dict[str, str] = {}
    body: list[Line] = []
    for lines, table in pages:
        for line in lines:
            if line.size >= title_size - 0.5:
                if body or not title:  # a title after body text starts the next motion
                    if title:
                        motions.append(_build_motion(title, meta, body, body_size))
                    title, meta, body = [], {}, []
                title.append(line)
            else:
                body.append(line)
        meta.update(table)  # the table sits right under the title of the current motion
    if title:
        motions.append(_build_motion(title, meta, body, body_size))
    return motions


def _page_lines(page, pno: int) -> tuple[list[Line], dict[str, str]]:
    """Return the text lines of a page (without table rows and page numbers) and its metadata table."""
    drawings = [d for d in page.get_drawings() if d.get("fill") is not None]
    bullets = [d["rect"] for d in drawings if d["rect"].width <= 6 and d["rect"].height <= 6]
    cells = [d["rect"] for d in drawings if d["fill"] != (0.0, 0.0, 0.0) and d["rect"].width > 50 and 8 < d["rect"].height < 60]

    lines: list[Line] = []
    table_rows: dict[float, list[Line]] = {}
    for block in page.get_text("dict")["blocks"]:
        for ln in block.get("lines", []):
            spans = [
                Span(
                    s["text"].replace("\xa0", " "),
                    bool(s["flags"] & 16) or bool(BOLD_FONT_RE.search(s["font"])),
                    bool(s["flags"] & 2) or bool(ITALIC_FONT_RE.search(s["font"])),
                )
                for s in ln["spans"]
                if s["text"]
            ]
            if not "".join(s.text for s in spans).strip():
                continue
            x0, y0, _, y1 = ln["bbox"]
            line = Line(pno, x0, y0, y1, max(s["size"] for s in ln["spans"]), spans)
            cy = (y0 + y1) / 2
            cell = next((c for c in cells if c.y0 <= cy <= c.y1), None)
            if cell is not None:
                table_rows.setdefault(round(cell.y0, 1), []).append(line)
            elif (y1 < MARGIN or y0 > page.rect.height - MARGIN) and PAGE_NUMBER_RE.match(line.text):
                continue
            else:
                lines.append(line)

    table = {}
    for row in table_rows.values():
        label_x = min(ln.x0 for ln in row)
        row.sort(key=lambda ln: (ln.y0, ln.x0))
        label = " ".join(ln.text.strip() for ln in row if ln.x0 <= label_x + 5).rstrip(":").strip()
        value = " ".join(ln.text.strip() for ln in row if ln.x0 > label_x + 5).strip()
        if label:
            table[label.casefold()] = value

    # Merge pieces on the same baseline (e.g. an ordered-list marker and its text).
    lines.sort(key=lambda ln: (round(ln.y0), ln.x0))
    merged: list[Line] = []
    for line in lines:
        prev = merged[-1] if merged else None
        if prev is not None and abs(prev.y0 - line.y0) < 2:
            if prev.ordered is None and ORDERED_MARKER_RE.match(prev.text.strip()):
                line.ordered, line.x0 = prev.text.strip(), prev.x0
                merged[-1] = line
            else:
                prev.spans += [Span(" ")] + line.spans
            continue
        merged.append(line)

    for line in merged:
        if line.ordered is None:
            bullet = next((b for b in bullets if b.x1 <= line.x0 + 1 and line.y0 <= (b.y0 + b.y1) / 2 <= line.y1), None)
            if bullet is not None:
                line.bullet_x = round(bullet.x0)
    return merged, table


def _build_motion(title_lines: list[Line], meta: dict, body: list[Line], body_size: float) -> PdfMotion:
    title_text = _collapse(" ".join(ln.text for ln in title_lines))
    match = TITLE_RE.match(title_text)
    number, title = (match.group(1), match.group(2).strip()) if match else ("", title_text)
    return PdfMotion(number, title, meta, _to_markdown(body, body_size), _repeated_line(body))


def _to_markdown(lines: list[Line], body_size: float) -> str:
    """Group lines into blocks (paragraph, list item, list item continuation, heading) and render them."""
    if not lines:
        return ""
    plain = [ln.x0 for ln in lines if ln.bullet_x is None and ln.ordered is None]
    left = min(plain) if plain else min(ln.x0 for ln in lines)
    bullet_levels = sorted({ln.bullet_x for ln in lines if ln.bullet_x is not None})
    gaps = Counter(round(b.y0 - a.y0) for a, b in zip(lines, lines[1:]) if a.page == b.page and 0 < b.y0 - a.y0 < 3 * body_size)
    # Line pitch = the smallest gap that occurs repeatedly (the most common one may be the paragraph gap).
    pitch = min((gap for gap, count in gaps.items() if count >= 2), default=body_size * 1.5)

    blocks: list[dict] = []  # {"kind": "p" | "ul" | "ol" | "cont" | "h", "level": int, "marker": str, "lines": [...]}
    prev: Line | None = None
    for line in lines:
        heading = line.size > body_size + 1
        starts_item = line.bullet_x is not None or line.ordered is not None
        indented = line.x0 > left + 3
        last = blocks[-1]["kind"] if blocks else None
        if line.page != (prev.page if prev else None):
            # Page break: a plain line continues the previous paragraph only mid-sentence.
            gap_break = prev is None or (not indented and (last != "p" or re.search(r"[.:;!?]\s*$", prev.text)))
        else:
            gap_break = line.y0 - prev.y0 > pitch * 1.15
        if (
            starts_item
            or gap_break
            or heading != (last == "h")
            or (last == "p" and indented)
            or (last in ("ul", "ol", "cont") and not indented)
        ):
            if heading:
                block = {"kind": "h", "level": 0, "marker": ""}
            elif line.bullet_x is not None:
                block = {"kind": "ul", "level": bullet_levels.index(line.bullet_x), "marker": "-"}
            elif line.ordered is not None:
                block = {"kind": "ol", "level": 0, "marker": line.ordered}
            elif indented and last in ("ul", "ol", "cont"):
                block = {"kind": "cont", "level": blocks[-1]["level"], "marker": ""}
            else:
                block = {"kind": "p", "level": 0, "marker": ""}
            blocks.append({**block, "lines": []})
        blocks[-1]["lines"].append(line)
        prev = line

    out = ""
    for i, block in enumerate(blocks):
        text, indent = _render_inline(block["lines"]), "  " * block["level"]
        chunk = {
            "h": f"### {text}",
            "ul": f"{indent}- {text}",
            "ol": f"{indent}{block['marker']} {text}",
            "cont": f"{indent}  {text}",
            "p": text,
        }[block["kind"]]
        if i:
            tight = block["kind"] in ("ul", "ol") and blocks[i - 1]["kind"] in ("ul", "ol", "cont")
            out += "\n" if tight else "\n\n"
        out += chunk
    return out.strip() + "\n"


def _render_inline(lines: list[Line]) -> str:
    """Join the lines of one block into Markdown text with **bold** / *italic*."""
    segments: list[Span] = []
    for line in lines:
        spans = [Span(s.text, s.bold, s.italic) for s in line.spans]
        if segments:
            prev = segments[-1]
            prev.text = prev.text.rstrip()
            spans[0].text = spans[0].text.lstrip()
            if prev.text.endswith("\xad"):  # soft hyphen: join the word
                prev.text = prev.text[:-1]
            elif not (re.search(r"\w-$", prev.text) and spans[0].text[:1].isalpha()):
                segments.append(Span(" ", spans[0].bold, spans[0].italic))
            # "Bull-" + "breeding" stays "Bull-breeding": pdfmake only breaks after real hyphens
        segments.extend(spans)

    merged: list[Span] = []
    for seg in segments:
        if merged and (merged[-1].bold, merged[-1].italic) == (seg.bold, seg.italic):
            merged[-1].text += seg.text
        else:
            merged.append(Span(seg.text, seg.bold, seg.italic))
    parts = []
    for seg in merged:
        text = _escape(seg.text)
        core = text.strip()
        if core and (seg.bold or seg.italic):
            mark = "***" if seg.bold and seg.italic else "**" if seg.bold else "*"
            lead, trail = text[: len(text) - len(text.lstrip())], text[len(text.rstrip()) :]
            text = f"{lead}{mark}{core}{mark}{trail}"
        parts.append(text)
    text = _collapse("".join(parts))
    return re.sub(r"^(\d+)\.(\s)", r"\1\\.\2", text)  # "4. Strengthening …" must stay a paragraph


def _escape(text: str) -> str:
    return re.sub(r"([\\`*_\[\]<>])", r"\\\1", text)


def _collapse(text: str) -> str:
    return re.sub(r"[ \t]+", " ", text).strip()


def _repeated_line(lines: list[Line]) -> str | None:
    """Colliding amendments show up in the PDF as (nearly) repeated lines close to each other."""
    texts = [_collapse(ln.text) for ln in lines]
    for i, text in enumerate(texts):
        for other in texts[i + 1 : i + 7]:
            if len(text) >= 40 and len(other) >= 40 and text[:40] == other[:40]:
                return text
    return None
