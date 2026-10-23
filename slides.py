"""Slide titles from an open-slide deck (slides/<id>/index.tsx), so answers can say which page to show."""

import re
from pathlib import Path


def page_titles(deck_source: str) -> list[str]:
    """Each page's title="..." in `export default [...]` order; the component name when it has none."""
    order = re.search(r"export default \[(.*?)\] satisfies", deck_source, re.S)
    if order is None:
        return []
    titles = []
    for name in (x.strip() for x in order.group(1).split(",") if x.strip()):
        m = re.search(
            rf"const {name}: Page = \(\) =>(.*?)(?=\nconst [A-Za-z0-9]+(?:: Page)? = |\nexport )",
            deck_source,
            re.S,
        )
        title = re.search(r'title="([^"]*)"', m.group(1)) if m else None
        titles.append(title.group(1) if title else name)
    return titles


def deck_map(deck: Path, body_pages: int | None) -> str:
    """One line per page: "第 N 頁：title", marking pages after the body as Backup."""
    lines = []
    for n, title in enumerate(page_titles(deck.read_text(encoding="utf-8")), 1):
        backup = body_pages is not None and n > body_pages
        lines.append(f"第 {n} 頁：{title}" + ("（Backup）" if backup else ""))
    return "\n".join(lines)
