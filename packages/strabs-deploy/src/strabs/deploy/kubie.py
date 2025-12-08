"""Kubie context detection utilities."""

import re
from dataclasses import dataclass

from invoke import Context


@dataclass
class KubieContext:
    """Parsed kubie context with all components."""

    role: str  # "admin" or "deploy"
    project: str
    env: str

    @property
    def is_deploy(self) -> bool:
        return self.role == "deploy"

    @property
    def is_admin(self) -> bool:
        return self.role == "admin"

    @property
    def full_name(self) -> str:
        return f"{self.role}@{self.project}-{self.env}"


def parse_context(c: Context) -> KubieContext | None:
    """Parse the kubie context into components.

    Returns None if not in a kubie context or not a recognized format.
    """
    result = c.run("kubie info ctx", hide=True, warn=True)
    if result is None or not result.ok:
        return None

    ctx = result.stdout.strip()
    match = re.match(r"^(admin|deploy)@(\w+)-(\w+)$", ctx)
    if not match:
        return None

    return KubieContext(
        role=match.group(1),
        project=match.group(2),
        env=match.group(3),
    )
