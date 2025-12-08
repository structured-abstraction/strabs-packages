"""Environment discovery and resolution."""

from pathlib import Path
from typing import NoReturn

from invoke import Context

from .kubie import parse_context


def _exit(msg: str) -> NoReturn:
    raise SystemExit(msg)


def discover_envs(envs_dir: Path) -> list[str]:
    """Discover available environments from the envs directory."""
    if not envs_dir.exists():
        return []
    return sorted(
        d.name for d in envs_dir.iterdir() if d.is_dir() and (d / "params.k").exists()
    )


def resolve(
    c: Context,
    env: str | None,
    project: str,
    envs_dir: Path,
    require_deploy_context: bool = False,
) -> str:
    """Resolve environment from argument or kubie context, with safety checks."""
    envs = discover_envs(envs_dir)
    kubie_ctx = parse_context(c)

    # Safety check: refuse if kubie context is for a different project
    if kubie_ctx and kubie_ctx.project != project:
        _exit(
            f"Project mismatch: you're in context '{kubie_ctx.full_name}' but this is the '{project}' project.\n"
            f"Switch to a {project} context first: kubie ctx admin@{project}-<env>"
        )

    if env is None:
        env = kubie_ctx.env if kubie_ctx and kubie_ctx.env in envs else None
        if env:
            print(f"Detected env: {env} (from kubie context)")

    if env is None:
        all_contexts = [f"admin@{project}-{e}" for e in envs] + [
            f"deploy@{project}-{e}" for e in envs
        ]
        _exit(
            f"Could not detect environment from kubie context.\n"
            f"Either enter a kubie context first (kubie ctx <context>) or pass --env\n"
            f"Valid contexts: {', '.join(all_contexts)}"
        )

    if env not in envs:
        _exit(f"Invalid environment: {env}. Choose from: {', '.join(envs)}")

    if kubie_ctx and kubie_ctx.env != env:
        _exit(
            f"Environment mismatch: requested '{env}' but kubie context is '{kubie_ctx.env}'.\n"
            f"Switch context first: kubie ctx admin@{project}-{env}"
        )

    if require_deploy_context and kubie_ctx and kubie_ctx.is_admin:
        _exit(
            f"This operation requires a deploy@ context, but you're in an admin@ context.\n"
            f"Switch context first: kubie ctx deploy@{project}-{env}"
        )

    return env
