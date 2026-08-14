"""Every env var `config.py` reads must actually reach the container.

This exists because of a real deploy failure. v0.9.0 added `INCLUDE_SPONSORS` and
`SPONSOR_VOICE`, the deploy notes told the production box to set `INCLUDE_SPONSORS=false` in
`.env` to keep it ad-free — and neither variable was in `docker-compose.yml`'s `environment:`
block, so the value never reached the app. It fell through to the code default (`True`) and the
box came up serving sponsor reads, silently and contrary to the written instruction. Nothing
failed; it just did the opposite of what the docs promised.

A Compose service only sees what it is handed. `os.environ.get` in `config.py` and a line in
`docker-compose.yml` are two halves of one contract that nothing was checking, so adding a
setting in Python looked complete while being half-done.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
CONFIG = ROOT / "app" / "config.py"
COMPOSE = ROOT / "docker-compose.yml"
ENV_EXAMPLE = ROOT / ".env.example"

#: Fixed by the container, and deliberately NOT user-overridable. `DATA_DIR` is the mount point
#: and `KOKORO_URL` is the compose service name — letting `.env` change either would break the
#: stack rather than configure it. `APP_PORT` is unused inside the container: the Dockerfile's
#: CMD hardcodes `--port 7777`, and the host-side mapping is compose's `ports:` line.
CONTAINER_FIXED = {"DATA_DIR", "KOKORO_URL", "APP_PORT"}


def config_env_vars() -> set[str]:
    """Names read from the environment by config.py, however they are read.

    Matches both `os.environ.get("X"` and the `_env_bool("X"` helper — the sponsor bug came in
    through the second form, so a pattern that only knew the first would have missed it.
    """
    src = CONFIG.read_text()
    return set(re.findall(r'(?:os\.environ\.get|_env_bool)\(\s*"([A-Z][A-Z0-9_]*)"', src))


def app_environment() -> dict[str, str]:
    """The app service's `environment:` mapping, parsed without a YAML dependency.

    PyYAML is only present transitively (via uvicorn[standard]) and adding a declared dep for a
    guard test is not worth it — this file is ours and its shape is stable. `test_config_reads_
    something` and the emptiness check below fail loudly if this parse ever drifts, so a silent
    "found nothing, everything passes" is not possible.
    """
    lines = COMPOSE.read_text().splitlines()
    out: dict[str, str] = {}
    in_app = in_env = False
    for line in lines:
        if re.match(r"^  \S+:", line):            # a service header
            in_app = line.strip().startswith("app:")
            in_env = False
            continue
        if in_app and re.match(r"^    \S+:", line):   # a key within the service
            in_env = line.strip().startswith("environment:")
            continue
        if in_app and in_env:
            m = re.match(r'^      ([A-Z][A-Z0-9_]*):\s*(.*?)\s*$', line)
            if m:
                out[m.group(1)] = m.group(2).strip('"').strip("'")
    assert out, "parsed no environment entries from docker-compose.yml — parser has drifted"
    return out


def test_config_reads_something():
    """Guard the guard: a regex that silently matches nothing would make every test below pass."""
    found = config_env_vars()
    assert len(found) >= 10, f"only found {found} — the pattern has probably drifted"
    assert "INCLUDE_SPONSORS" in found, "the _env_bool form is not being matched"


@pytest.mark.parametrize("var", sorted(config_env_vars() - CONTAINER_FIXED))
def test_every_setting_reaches_the_container(var):
    env = app_environment()
    assert var in env, (
        f"{var} is read by config.py but missing from docker-compose.yml's app environment. "
        f"In Docker it will silently fall back to the code default, so anything documented "
        f"about setting it in .env would be a lie."
    )


@pytest.mark.parametrize("var", sorted(config_env_vars() - CONTAINER_FIXED))
def test_settings_are_passed_through_not_hardcoded(var):
    """`KEY: "value"` pins the value; only `${KEY:-default}` lets `.env` actually win."""
    value = app_environment()[var]
    assert "${" + var in value, (
        f'docker-compose.yml pins {var} to {value!r}, so setting it in .env does nothing. '
        f'Use "${{{var}:-<default>}}".'
    )


@pytest.mark.parametrize("var", sorted(config_env_vars() - CONTAINER_FIXED))
def test_documented_for_whoever_has_to_configure_this(var):
    """.env.example claims to list every setting — an undocumented one cannot be found."""
    assert var in ENV_EXAMPLE.read_text(), f"{var} is configurable but absent from .env.example"


def test_container_fixed_vars_really_are_fixed():
    """The allowlist must stay a statement about the container, not a place to hide omissions."""
    env = app_environment()
    for var in CONTAINER_FIXED & set(env):
        assert "${" not in env[var], (
            f"{var} is on the container-fixed allowlist but compose passes it through — "
            f"decide which it is."
        )
