from invoke import task
from pathlib import Path


PACKAGES_DIR = Path("packages")


def get_packages() -> list[Path]:
    return [p for p in PACKAGES_DIR.iterdir() if p.is_dir()]


@task
def typecheck(c):
    """Run mypy on all packages."""
    for pkg in get_packages():
        print(f"\n=== {pkg.name} ===")
        c.run(f"cd {pkg} && uv run --extra dev mypy src")


@task
def lint(c):
    """Run ruff on all packages."""
    for pkg in get_packages():
        print(f"\n=== {pkg.name} ===")
        c.run(f"cd {pkg} && uv run --extra dev ruff check src")


@task
def fmt(c):
    """Format all packages with ruff."""
    for pkg in get_packages():
        print(f"\n=== {pkg.name} ===")
        c.run(f"cd {pkg} && uv run --extra dev ruff format src")


@task(pre=[typecheck, lint])
def check(c):
    """Run all checks (typecheck + lint)."""
    pass
