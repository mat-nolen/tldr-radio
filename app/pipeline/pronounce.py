"""Pronunciation dictionary — plain, editable, expected to grow (spec §6 stage 4).

This is the main audio-quality tuning knob. Add entries as real mispronunciations
surface on first listen. Kokoro's own normalizer already handles numbers/currency/%.
"""

from __future__ import annotations

import re

# Token as it appears in text → spoken form. Case-sensitive keys.
PRONUNCIATIONS: dict[str, str] = {
    "GPT-4o": "G P T four oh",
    "LLMs": "L L Ms",
    "LLM": "L L M",
    "APIs": "A P Is",
    "API": "A P I",
    "SDK": "S D K",
    "GPU": "G P U",
    "CPU": "C P U",
    "K8s": "Kubernetes",
    "PostgreSQL": "Postgres Q L",
    "PyTorch": "Pie Torch",
    "macOS": "Mac O S",
    "iOS": "i O S",
    "SaaS": "sass",
    "IPO": "I P O",
    "YC": "Y Combinator",
}


def apply_pronunciations(text: str) -> str:
    """Replace known jargon with a spoken form (word-boundary, longest token first)."""
    for token in sorted(PRONUNCIATIONS, key=len, reverse=True):
        text = re.sub(rf"(?<!\w){re.escape(token)}(?!\w)", PRONUNCIATIONS[token], text)
    # CVE ids: CVE-2026-1234 → "C V E 2026 1234"
    text = re.sub(r"\bCVE-(\d{4})-(\d{4,7})\b", r"C V E \1 \2", text)
    return text
