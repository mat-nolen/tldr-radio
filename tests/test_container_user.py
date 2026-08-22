"""The container must not run as root, and a permission mismatch must not be silent.

Opened as a security report on the public repo the day after the project was featured
(`#2`/`#3`, found by an automated scanner): the Dockerfile had no `USER` directive, so uvicorn ran
as root inside the container — CWE-250.

The directive alone is not the whole fix. `/data` is a host bind mount, so the moment the app
stops being root the HOST directory's owner decides whether episodes can be written at all. On
macOS Docker Desktop remaps mount permissions and it works regardless; on a Linux box it does not,
which is precisely the dev/prod asymmetry that let a missing compose variable reach production in
v0.10.1. So the second half of the fix is that the app refuses to start, with the chown command in
the message, instead of failing halfway through a broadcast.
"""

from __future__ import annotations

import os
import re
from pathlib import Path

import pytest

from app.main import DataDirNotWritableError, ensure_data_dir_writable

ROOT = Path(__file__).resolve().parent.parent
DOCKERFILE = ROOT / "Dockerfile"
COMPOSE = ROOT / "docker-compose.yml"


def test_dockerfile_declares_a_user():
    directives = re.findall(r"^USER\s+(\S+)", DOCKERFILE.read_text(), re.M)
    assert directives, (
        "Dockerfile has no USER directive, so the app runs as root in the container (CWE-250)."
    )
    assert directives[-1] not in {"root", "0"}, f"Dockerfile switches to {directives[-1]!r}"


def test_the_user_it_switches_to_is_actually_created():
    """`USER 1000` on its own leaves an account that exists only as a number.

    It technically runs, which is why a scanner is satisfied by it — but nothing owns the home,
    the shell or the group, and `ls -l` inside the container shows a bare uid. The reported patch
    did exactly this; creating the account costs one layer and makes the image legible.
    """
    src = DOCKERFILE.read_text()
    user = re.findall(r"^USER\s+(\S+)", src, re.M)[-1]
    assert re.search(rf"useradd[^\n]*{re.escape(user)}", src), (
        f"Dockerfile switches to {user!r} without creating it"
    )


def test_compose_lets_a_host_override_the_uid():
    """A fixed uid in the image is a guess about the host; .env has to be able to win."""
    m = re.search(r'^\s*user:\s*"([^"]+)"', COMPOSE.read_text(), re.M)
    assert m, "docker-compose.yml does not set `user:` for the app service"
    assert "${APP_UID" in m.group(1) and "${APP_GID" in m.group(1), (
        f"compose pins the container user to {m.group(1)!r}, so a host whose ./data belongs to "
        f"someone else cannot fix it without rebuilding the image"
    )


def test_writable_data_dir_is_accepted(tmp_path):
    ensure_data_dir_writable(tmp_path)


def test_the_probe_file_is_cleaned_up(tmp_path):
    ensure_data_dir_writable(tmp_path)
    assert list(tmp_path.iterdir()) == [], "the writability probe left a file behind"


def test_a_missing_data_dir_is_created(tmp_path):
    fresh = tmp_path / "data"
    ensure_data_dir_writable(fresh)
    assert fresh.is_dir()


@pytest.mark.skipif(
    getattr(os, "getuid", lambda: -1)() == 0,
    reason="root can write anywhere, so there is nothing to detect",
)
def test_an_unwritable_data_dir_fails_loudly_and_names_the_fix(tmp_path):
    locked = tmp_path / "locked"
    locked.mkdir()
    locked.chmod(0o500)
    try:
        with pytest.raises(DataDirNotWritableError) as caught:
            ensure_data_dir_writable(locked)
    finally:
        locked.chmod(0o700)

    message = str(caught.value)
    assert "chown" in message, "the error does not tell anyone how to fix it"
    assert "APP_UID" in message, "the error does not mention the .env escape hatch"
    assert str(locked) in message, "the error does not say which directory"
