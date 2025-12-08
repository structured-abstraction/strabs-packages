"""Git utilities."""

from pathlib import Path

from invoke import Context

from .confirm import random_char_confirm


def has_uncommitted_changes(c: Context, path: Path | None = None) -> bool:
    cmd = "git status --porcelain"
    if path:
        cmd = f"git -C {path} status --porcelain"
    result = c.run(cmd, hide=True, warn=True)
    if result is None:
        return False
    return bool(result.stdout.strip())


def confirm_clean(c: Context, path: Path | None = None) -> bool:
    if not has_uncommitted_changes(c, path):
        return True

    return random_char_confirm(
        "WARNING: You have uncommitted changes in the repository."
    )
