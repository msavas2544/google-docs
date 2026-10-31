"""Render a document body as GitHub-flavoured Markdown."""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Tuple

from .models import Body, Chip, InlineImage, Marker, Paragraph, Table, TableOfContents, TextRun

_ESCAPE = re.compile(r"([\\`*_\[\]<>])")
_LINE_START = re.compile(r"^(\s*)([#>+-]|\d+[.)])(?=\s|$)")


def render(body: Body) -> str:
    out: List[str] = []
    widths: List[int] = []  # marker width of each open list level, for child indentation
    numbers: Dict[Tuple[str, int], int] = {}
    code: List[str] = []
    list_id: Optional[str] = None
    footnotes: List[Tuple[str, str]] = []

    def gap() -> None:
        if out and out[-1] != "":
            out.append("")

    def end_code() -> None:
        if code:
            while code and not code[-1].strip():
                code.pop()
            gap()
            out.extend(["```", *code, "```"])
            code.clear()

    for block in body.content:
        if isinstance(block, Table):
            end_code()
            gap()
            out.extend(_table(block, body))
            list_id = None
            continue
        if isinstance(block, TableOfContents) or not isinstance(block, Paragraph):
            continue
        p = block
        if _is_code(p) or (code and _shaded(p) and p.is_empty):
            code.append(_plain(p))
            continue
        end_code()
        for el in p.elements:
            if isinstance(el, Chip) and el.kind == "footnote" and el.url in body.footnotes:
                note = body.footnotes[el.url or ""]
                footnotes.append((el.text, " ".join(x.text for x in note.walk()).strip()))

        if p.is_list_item:
            level = p.nesting_level
            if p.list_id != list_id:
                gap()
                widths.clear()
                list_id = p.list_id
            del widths[level:]
            while len(widths) < level:
                widths.append(2)
            for key in [k for k in numbers if k[0] == p.list_id and k[1] > level]:
                del numbers[key]
            if p.is_ordered:
                n = numbers[(p.list_id or "", level)] = numbers.get((p.list_id or "", level), 0) + 1
                marker = f"{n}."
            else:
                marker = "-"
            widths.append(len(marker) + 1)
            out.append(" " * sum(widths[:level]) + marker + " " + _inline(p, body))
            continue
        list_id = None
        style = p.raw.get("paragraphStyle") or {}
        if _border(style, "borderBottom") and p.is_empty or _only_rule(p):
            gap()
            out.append("---")
            continue
        text = _inline(p, body)
        if not text.strip():
            continue
        gap()
        heading = p.heading_level
        if heading is not None:
            out.append("#" * max(heading, 1) + " " + text.replace("  \n", " "))
        elif p.style_name == "SUBTITLE":
            out.append(f"*{text}*")
        elif _border(style, "borderLeft"):
            out.append("\n".join("> " + line for line in text.split("\n")))
        else:
            out.append(_LINE_START.sub(lambda m: m.group(1) + "\\" + m.group(2), text, 1))
    end_code()
    if footnotes:
        gap()
        out.extend(f"[^{n}]: {t}" for n, t in footnotes)
    while out and out[-1] == "":
        out.pop()
    return "\n".join(out) + "\n" if out else ""


def _border(style: Dict[str, Any], side: str) -> bool:
    border = style.get(side) or {}
    return bool((border.get("width") or {}).get("magnitude")) and bool(border.get("color"))


def _shaded(p: Paragraph) -> bool:
    shading = (p.raw.get("paragraphStyle") or {}).get("shading") or {}
    return bool((shading.get("backgroundColor") or {}).get("color"))


def _only_rule(p: Paragraph) -> bool:
    rules = [e for e in p.elements if isinstance(e, Marker) and e.kind == "horizontal_rule"]
    rest = [e for e in p.elements if e not in rules]
    return bool(rules) and all(isinstance(e, TextRun) and not e.text.strip() for e in rest)


def _is_code(p: Paragraph) -> bool:
    runs = [e for e in p.elements if isinstance(e, TextRun) and e.text.strip("\n")]
    return bool(runs) and not p.is_list_item and not p.is_heading and all(
        r.is_code for r in runs) and len(p.elements) == len(
        [e for e in p.elements if isinstance(e, TextRun)])


def _plain(p: Paragraph) -> str:
    return "".join(e.text for e in p.elements).rstrip("\n").replace("\v", "\n")


def _table(t: Table, body: Body) -> List[str]:
    rows = [[" <br> ".join(_inline(p, body, in_table=True, plain_bold=(n == 0))
                           for p in c.paragraphs).strip()
             for c in row] for n, row in enumerate(t.rows)]
    if not rows:
        return []
    width = max(len(r) for r in rows)
    rows = [r + [""] * (width - len(r)) for r in rows]
    lines = ["| " + " | ".join(rows[0]) + " |", "|" + "---|" * width]
    lines.extend("| " + " | ".join(r) + " |" for r in rows[1:])
    return lines


def _inline(p: Paragraph, body: Body, in_table: bool = False, plain_bold: bool = False) -> str:
    pieces: List[Tuple[Tuple[bool, bool, bool, bool, Optional[str]], str]] = []
    for el in p.elements:
        if isinstance(el, TextRun):
            text = el.text.replace("\n", "")
            if not text:
                continue
            key = (el.bold and not plain_bold, el.italic, el.strikethrough, el.is_code, el.link)
            if pieces and pieces[-1][0] == key:
                pieces[-1] = (key, pieces[-1][1] + text)
            else:
                pieces.append((key, text))
        elif isinstance(el, InlineImage):
            alt = _escape(el.title or el.description or "image")
            pieces.append(((False,) * 4 + (None,), f"\0![{alt}]({el.uri or ''})"))  # type: ignore
        elif isinstance(el, Chip):
            if el.kind == "footnote":
                rendered = f"\0[^{el.text}]"
            elif el.url:
                rendered = f"\0[{_escape(el.text)}]({el.url})"
            else:
                rendered = "\0" + _escape(el.text)
            pieces.append(((False,) * 4 + (None,), rendered))  # type: ignore
    text = "".join(_fmt(key, t) for key, t in pieces)
    text = text.replace("\v", "<br>" if in_table else "  \n")
    return text.replace("|", "\\|") if in_table else text


def _fmt(key: Tuple[bool, bool, bool, bool, Optional[str]], text: str) -> str:
    if text.startswith("\0"):
        return text[1:]
    bold, italic, strike, code, link = key
    lead = text[: len(text) - len(text.lstrip())]
    trail = text[len(text.rstrip()):]
    core = text.strip()
    if not core:
        return text
    if code:
        fence = "``" if "`" in core else "`"
        core = f"{fence}{core}{fence}"
    else:
        core = _escape(core)
        if strike:
            core = f"~~{core}~~"
        if italic:
            core = f"*{core}*"
        if bold:
            core = f"**{core}**"
    if link:
        core = f"[{core}]({link})"
    return lead + core + trail


def _escape(text: str) -> str:
    return _ESCAPE.sub(r"\\\1", text)

