"""Self-contained HTML views: shared CSS + view runtime + per-view markup."""

from __future__ import annotations

import html
from functools import cache
from importlib.resources import files


@cache
def asset(name: str) -> str:
    return (files(__package__) / name).read_text(encoding="utf-8")


def render_page(title: str, view_file: str) -> str:
    """Assemble one self-contained document from ``views/<view_file>``.

    The view file holds body markup and a ``<script>`` that uses the global
    ``FoyelleView`` defined by ``runtime.js``.
    """
    return render_body(title, asset(f"views/{view_file}"))


def render_body(title: str, body: str) -> str:
    """Wrap body markup (and its ``<script>``) with the shared CSS and the view runtime."""
    return (
        "<!doctype html>\n"
        '<html lang="en">\n<head>\n<meta charset="utf-8">\n'
        '<meta name="viewport" content="width=device-width, initial-scale=1">\n'
        f"<title>{html.escape(title)}</title>\n"
        f"<style>\n{asset('base.css')}</style>\n"
        f"<script>\n{asset('runtime.js')}</script>\n"
        "</head>\n<body>\n"
        f"{body}"
        "</body>\n</html>\n"
    )
