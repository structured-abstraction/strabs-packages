"""
Kapp deployment utilities with automatic cleanup.
"""

import shutil
from contextlib import contextmanager
from pathlib import Path
from typing import Generator

from invoke import Context

from strabs.doit import doit, run, RunConfig


def _confirm(c: Context, kapp_cmd: str, action: str, force: bool) -> bool:
    """Run kapp command with confirmation prompt."""
    if force:
        results = doit([run(action, f"{kapp_cmd} -y")], RunConfig(raise_on_failure=False))
        return all(r.ok for r in results)

    result = c.run(f"yes n 2>/dev/null | {kapp_cmd}", pty=True, hide=True, warn=True)
    if result is None:
        return False

    lines = [
        line
        for line in result.stdout.splitlines()
        if "kapp: Error: Stopped" not in line and "Continue? [yN]" not in line
    ]

    for line in lines:
        print(line)

    if input(f"\n{action}? [y/N] ").strip().lower() != "y":
        print("Aborted.")
        return False

    # Clear the diff output (lines + blank line + prompt line)
    print(f"\x1b[{len(lines) + 2}A\x1b[J", end="")

    results = doit([run(action, f"{kapp_cmd} -y")], RunConfig(raise_on_failure=False))
    return all(r.ok for r in results)


def deploy(
    c: Context,
    app_name: str,
    manifests_dir: Path,
    namespace: str | None = None,
    force: bool = False,
) -> bool:
    """Deploy manifests using kapp with confirmation.

    Args:
        c: Invoke context
        app_name: Kapp app name
        manifests_dir: Directory containing manifests to deploy
        namespace: Optional namespace (uses -n flag)
        force: Skip confirmation if True

    Returns:
        True if deployed successfully, False if aborted
    """
    ns_flag = f"-n {namespace} " if namespace else ""
    cmd = f"kapp deploy -a {app_name} {ns_flag}-f {manifests_dir}"
    return _confirm(c, cmd, "Deploy", force)


def delete(
    c: Context,
    app_name: str,
    namespace: str | None = None,
    force: bool = False,
) -> bool:
    """Delete kapp app with confirmation.

    Args:
        c: Invoke context
        app_name: Kapp app name
        namespace: Optional namespace (uses -n flag)
        force: Skip confirmation if True

    Returns:
        True if deleted successfully, False if aborted
    """
    ns_flag = f"-n {namespace} " if namespace else ""
    cmd = f"kapp delete -a {app_name} {ns_flag}"
    return _confirm(c, cmd, "Delete", force)


@contextmanager
def tmpdir(path: Path) -> Generator[Path, None, None]:
    """Context manager for a temporary directory with automatic cleanup.

    Cleans the directory before use and after the block exits (success or failure).
    Useful for rendering manifests that should be cleaned up after deployment.

    Example:
        with kapp.tmpdir(work_dir) as tmp:
            doit([run("Render", f"kcl run app.k -o {tmp}/manifests.yaml")])
            kapp.deploy(c, "my-app", tmp)
        # tmp directory is automatically cleaned up

    Args:
        path: Directory path to use

    Yields:
        The path for convenience
    """
    # Clean before
    shutil.rmtree(path, ignore_errors=True)
    path.mkdir(parents=True, exist_ok=True)

    try:
        yield path
    finally:
        # Clean after
        shutil.rmtree(path, ignore_errors=True)


@contextmanager
def secrets(encrypted_file: Path, decrypted_file: Path) -> Generator[Path, None, None]:
    """Context manager for decrypted secrets with automatic cleanup.

    Decrypts the encrypted file and removes the decrypted file after use.

    Example:
        with kapp.secrets(enc_file, dec_file) as secrets_path:
            doit([run("Render", f"kcl run app.k {secrets_path} -o out.yaml")])
        # secrets_path is automatically deleted

    Args:
        encrypted_file: Path to sops-encrypted file
        decrypted_file: Path where decrypted file will be written

    Yields:
        Path to the decrypted file

    Raises:
        SystemExit: If encrypted file doesn't exist or decryption fails
    """
    if not encrypted_file.exists():
        raise SystemExit(f"Missing encrypted secrets: {encrypted_file}")

    # Ensure parent directory exists
    decrypted_file.parent.mkdir(parents=True, exist_ok=True)

    # Decrypt
    doit([run("Decrypting secrets", f"sops -d {encrypted_file} > {decrypted_file}")])

    try:
        yield decrypted_file
    finally:
        # Always clean up decrypted secrets
        decrypted_file.unlink(missing_ok=True)
