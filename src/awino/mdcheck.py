"""Structural checks for the Markdown a model writes inside a staged session.

Shared by Brain mode and Deep Plan. The checks are structural on purpose: they
prove the required parts exist (a section, a bullet per item, a labeled field),
not that the thinking is good. The human's checkpoint answers judge that.
"""

from __future__ import annotations

import re

_TOP_ITEM_RE = re.compile(r"^(?:[-*+]|\d+[.)])\s+")


def _blocks(section: str) -> list[str]:
    """The items of a section: ``###`` sub-sections when it has them, else its
    top-level list items with their indented continuation lines."""
    lines = section.splitlines()
    if any(re.match(r"^#{3,6}\s", ln) for ln in lines):
        out: list[list[str]] = []
        for ln in lines:
            if re.match(r"^#{3,6}\s", ln):
                out.append([ln])
            elif out:
                out[-1].append(ln)
        return ["\n".join(b).strip() for b in out if "\n".join(b).strip()]
    items: list[list[str]] = []
    for ln in lines:
        if _TOP_ITEM_RE.match(ln):
            items.append([ln])
        elif items and ln.strip() and (ln[:1].isspace() or not _TOP_ITEM_RE.match(ln)):
            items[-1].append(ln)
    return ["\n".join(b).strip() for b in items]


def _head(block: str, width: int = 50) -> str:
    first = re.sub(r"^(#+|[-*+]|\d+[.)])\s+", "", block.splitlines()[0].strip())
    return first[:width]


_HEADING_RE = re.compile(r"^(#{1,6})\s+(.+?)\s*$", re.M)


def _section(text: str, *synonyms: str) -> str:
    """The body under the first heading containing a synonym, up to the next
    heading of the same or higher level. ``##`` headings win over others, so a
    document title like "# Acme's scheduling problem" never swallows the
    whole document as the "problem" section."""
    heads = list(_HEADING_RE.finditer(text))
    wanted = tuple(s.lower() for s in synonyms)
    for prefer in (2, None):
        for i, m in enumerate(heads):
            level = len(m.group(1))
            if prefer is not None and level != prefer:
                continue
            if not any(w in m.group(2).lower() for w in wanted):
                continue
            end = len(text)
            for nxt in heads[i + 1 :]:
                if len(nxt.group(1)) <= level:
                    end = nxt.start()
                    break
            return text[m.end() : end]
    return ""


def _need(text: str, problems: list[str], name: str, *synonyms: str) -> str:
    body = _section(text, *synonyms)
    if not body.strip():
        problems.append(f"missing section: '{name}'")
    return body


def _each_has(
    blocks: list[str], labels: tuple[str, ...], what: str, problems: list[str], skip_last: str = ""
) -> None:
    for i, block in enumerate(blocks):
        low = block.lower()
        for label in labels:
            if label == skip_last and i == len(blocks) - 1:
                continue
            if label not in low:
                problems.append(
                    f"{what} '{_head(block)}' has no '{label.rstrip(':').capitalize()}:'"
                )
