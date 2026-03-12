"""
src/reports/html_converter.py
──────────────────────────────
Mode B paste helper: converts report.md → report.html for pasting
into Substack's HTML editor.

⚠️  No automated posting. Manual copy-paste into Substack editor only.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path

logger = logging.getLogger(__name__)


def convert_to_html(report_md_path: Path) -> Path:
    """
    Convert report.md to report.html.
    Uses a minimal regex-based converter (no external deps beyond stdlib).
    Returns path to the generated .html file.
    """
    md = report_md_path.read_text(encoding="utf-8")
    html = _md_to_html(md)

    out_path = report_md_path.parent / "report.html"
    out_path.write_text(html, encoding="utf-8")
    logger.info("[html_converter] report.html → %s", out_path)
    return out_path


def _md_to_html(md: str) -> str:
    """Minimal Markdown → HTML converter for Substack paste compatibility."""
    lines = md.split("\n")
    output: list[str] = ["<article>"]
    in_table = False

    for line in lines:
        # ── Headings ──────────────────────────────────────────────────────────
        if line.startswith("### "):
            output.append(f"<h3>{_inline(line[4:])}</h3>")
        elif line.startswith("## "):
            output.append(f"<h2>{_inline(line[3:])}</h2>")
        elif line.startswith("# "):
            output.append(f"<h1>{_inline(line[2:])}</h1>")

        # ── Horizontal rule ───────────────────────────────────────────────────
        elif line.strip() == "---":
            output.append("<hr>")

        # ── Tables ────────────────────────────────────────────────────────────
        elif "|" in line and line.strip().startswith("|"):
            cells = [c.strip() for c in line.strip().strip("|").split("|")]
            if re.match(r"^[-:| ]+$", line.strip()):
                continue   # separator row
            if not in_table:
                output.append("<table><thead><tr>")
                output.extend(f"<th>{_inline(c)}</th>" for c in cells)
                output.append("</tr></thead><tbody>")
                in_table = True
            else:
                output.append("<tr>")
                output.extend(f"<td>{_inline(c)}</td>" for c in cells)
                output.append("</tr>")
        else:
            if in_table:
                output.append("</tbody></table>")
                in_table = False

            # ── Bullet list ───────────────────────────────────────────────────
            if line.strip().startswith("- "):
                output.append(f"<li>{_inline(line.strip()[2:])}</li>")
            elif line.strip().startswith("* "):
                output.append(f"<li>{_inline(line.strip()[2:])}</li>")
            # ── Italic block (starts with *)  ─────────────────────────────────
            elif line.strip():
                output.append(f"<p>{_inline(line.strip())}</p>")
            else:
                output.append("<br>")

    if in_table:
        output.append("</tbody></table>")

    output.append("</article>")
    return "\n".join(output)


def _inline(text: str) -> str:
    """Apply inline formatting: bold, italic, code."""
    # Bold
    text = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", text)
    # Italic
    text = re.sub(r"\*(.+?)\*", r"<em>\1</em>", text)
    # Inline code
    text = re.sub(r"`(.+?)`", r"<code>\1</code>", text)
    return text
