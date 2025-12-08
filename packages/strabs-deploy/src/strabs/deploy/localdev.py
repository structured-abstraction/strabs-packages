"""
Local development helpers.
"""

from pathlib import Path

from strabs.doit import doit, run, RunConfig


def setup_mkcert(domain: str, certs_dir: Path):
    """Set up mkcert CA and generate TLS certificates. Raises on failure."""
    certs_dir.mkdir(parents=True, exist_ok=True)
    doit(
        [
            run("Installing mkcert CA", "mkcert -install"),
            run(
                f"Generating cert for {domain}",
                f"mkcert -cert-file {certs_dir}/tls.crt -key-file {certs_dir}/tls.key {domain}",
            ),
        ],
        config=RunConfig(fail_fast=True),
    )
