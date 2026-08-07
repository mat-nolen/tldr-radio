"""Fallback ingest: pasted URL / text / HTML, or a dropped .eml / .html / .txt (spec §6).

For when the archive 404s, lags, or changes shape. Uploaded content is parsed in-memory,
never executed. Guards against <style>/<script> leakage, hidden preheader text,
&nbsp;/zero-width spacers, and quoted-printable soft line breaks.
"""

from __future__ import annotations


def ingest_pasted(content: str) -> str:
    """Normalize pasted text/HTML into clean plain text for the parser. Phase 3."""
    raise NotImplementedError("Phase 3 — fallback ingest (paste)")


def ingest_file(filename: str, data: bytes) -> str:
    """Normalize a dropped .eml / .html / .txt into clean plain text. Phase 3."""
    raise NotImplementedError("Phase 3 — fallback ingest (file)")
