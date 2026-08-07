"""The TLDR newsletter catalog — the single place an edition is ever declared.

tldr.tech publishes 14 newsletters, all on the same `/{slug}/{date}` archive layout, so the
parser (which keys on structure, not on a section list) handles every one of them unchanged.
Adding a future edition means one entry here plus its two colour tokens in
`static/styles/design-tokens.css` — nothing else.

Each edition carries two distinct strings, and they answer different questions:

  - `tagline` — the edition's own first three section names, verified by fetching and parsing a
    real issue of each (2026-08-06). That is how the original three were written, and it says
    what an episode will actually contain, in running order.
  - `description` — tldr.tech's own one-line summary of the newsletter, lightly trimmed. It says
    who the newsletter is *for*, which is what you need when deciding whether to switch it on.

Order matches tldr.tech's own newsletter listing.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass


@dataclass(frozen=True)
class Edition:
    slug: str
    name: str
    tagline: str
    description: str


EDITIONS: dict[str, Edition] = {
    e.slug: e
    for e in (
        Edition(
            "tech", "TLDR Tech", "Big Tech · Science · Programming",
            "The most interesting stories in tech, startups, and programming",
        ),
        Edition(
            "dev", "TLDR Dev", "Tutorials · Opinions · Tools",
            "Deep dives, tools, and trends across frontend, backend, and full stack",
        ),
        Edition(
            "ai", "TLDR AI", "Headlines · Deep Dives · Research",
            "Launches, innovations, and research for AI and machine learning",
        ),
        Edition(
            "infosec", "TLDR Infosec", "Attacks · Tactics · Tools",
            "News, research, and tools for information security professionals",
        ),
        Edition(
            "product", "TLDR Product", "News · Tutorials · Resources",
            "Deep dives, trends, and resources for product managers",
        ),
        Edition(
            "devops", "TLDR DevOps", "News · Tutorials · Tools",
            "Tools, trends, and insights for DevOps engineers",
        ),
        Edition(
            "founders", "TLDR Founders", "Headlines · Tactics · Tools",
            "Tactics, trends, and tools for startup founders and entrepreneurs",
        ),
        Edition(
            "design", "TLDR Design", "News · Opinions · Launches",
            "Tools, trends, and inspiration for designers",
        ),
        Edition(
            "marketing", "TLDR Marketing", "News · Tactics · Tools",
            "Tactics, trends, and tools for cutting edge marketers",
        ),
        Edition(
            "crypto", "TLDR Crypto", "Markets · Launches · Guides",
            "Launches, innovations, and market moves in crypto and Web3",
        ),
        Edition(
            "fintech", "TLDR Fintech", "News · Deep Dives · Launches",
            "Innovations and trends in financial markets and technology",
        ),
        Edition(
            "it", "TLDR IT", "News · Analysis · Launches",
            "News and trends in IT strategy, security, and cloud computing",
        ),
        Edition(
            "data", "TLDR Data", "Deep Dives · Opinions · Tools",
            "Big data, data science, and data engineering",
        ),
        Edition(
            "hardware", "TLDR Hardware", "Headlines · Engineering · Research",
            "The latest in robotics, semiconductors, and hardware engineering",
        ),
    )
}

#: What ships enabled when there is nothing in the settings table and no env override —
#: exactly the three editions the app carried before the catalog existed.
DEFAULT_EDITIONS: tuple[str, ...] = ("tech", "ai", "infosec")

#: Back-compat view for scripting/worker titles.
EDITION_NAMES: dict[str, str] = {slug: e.name for slug, e in EDITIONS.items()}


def name_for(slug: str) -> str:
    """Display name for a slug, falling back to a title-cased guess for an unknown one."""
    edition = EDITIONS.get(slug)
    return edition.name if edition else f"TLDR {slug.title()}"


def known(slugs: Iterable[str]) -> tuple[str, ...]:
    """Keep only catalog slugs, de-duplicated and returned in catalog order.

    Every list that reaches the app from settings, env, or an API body goes through here, so a
    typo or a retired edition can never queue a job against a URL that will never exist.
    """
    wanted = set(slugs)
    return tuple(slug for slug in EDITIONS if slug in wanted)
