"""
Juggernaut deployment helpers.

Helpers for working with juggernaut KCL configs and prereqs.
"""

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml
from invoke import Context

from strabs.doit import doit, run, TaskBuilder


def kcl_json(c: Context, *kcl_files: Path, output: Path) -> dict[str, Any]:
    """Run KCL and return parsed JSON output.

    Args:
        c: Invoke context
        *kcl_files: KCL files to run (e.g., render file, params file)
        output: Path to write JSON output

    Returns:
        Parsed JSON as dict

    Raises:
        SystemExit: If KCL fails
    """
    files = " ".join(str(f) for f in kcl_files)
    result = c.run(
        f"kcl run {files} -o {output} --format json 2>&1",
        hide=True,
        warn=True,
    )
    if result is None or not result.ok:
        exit_code = result.return_code if result else 1
        output_text = result.stdout if result else ""
        raise SystemExit(f"KCL failed (exit {exit_code}):\n{output_text}")
    return json.loads(output.read_text())


def kcl_yaml(c: Context, *kcl_files: Path, output: Path) -> None:
    """Run KCL and write YAML output.

    Args:
        c: Invoke context
        *kcl_files: KCL files to run
        output: Path to write YAML output

    Raises:
        SystemExit: If KCL fails
    """
    files = " ".join(str(f) for f in kcl_files)
    result = c.run(f"kcl run {files} -o {output}", hide=True, warn=True)
    if result is None or not result.ok:
        exit_code = result.return_code if result else 1
        output_text = result.stdout if result else ""
        raise SystemExit(f"KCL failed (exit {exit_code}):\n{output_text}")


@dataclass
class PrereqsResult:
    """Result of rendering prereqs."""

    manifests_dir: Path
    privileged_namespaces: list[str]


def render_prereqs(
    c: Context,
    prereqs_list_file: Path,
    prereqs_manifests_file: Path,
    params_file: Path,
    work_dir: Path,
) -> PrereqsResult:
    """Render juggernaut prereqs to a directory.

    Runs KCL to get prereq definitions, then renders:
    - URL prereqs via curl
    - Helm prereqs via helm template
    - Additional KCL manifests (clusterissuers, etc.)

    Args:
        c: Invoke context
        prereqs_list_file: KCL file that outputs prereqs list
        prereqs_manifests_file: KCL file that outputs additional manifests
        params_file: KCL params file for the environment
        work_dir: Working directory (should already exist)

    Returns:
        PrereqsResult with manifests_dir and privileged_namespaces
    """
    manifests_dir = work_dir / "manifests"
    manifests_dir.mkdir(parents=True, exist_ok=True)

    # Get prereq list, helm values from KCL
    data = kcl_json(c, prereqs_list_file, params_file, output=work_dir / "prereqs.json")
    prereqs = data.get("prereqs", [])
    helm_values = data.get("helmValues", {})
    privileged_namespaces = data.get("privilegedNamespaces", [])

    # Render additional KCL manifests (clusterissuers, etc.)
    kcl_yaml(
        c,
        prereqs_manifests_file,
        params_file,
        output=manifests_dir / "kcl-manifests.yaml",
    )

    # Write helm values files
    for name, values in helm_values.items():
        (work_dir / f"{name}-values.yaml").write_text(yaml.dump(values))

    # Build render tasks
    render_tasks: list[TaskBuilder] = []
    for prereq in prereqs:
        name = prereq["name"]
        if prereq["type"] == "url":
            out_file = manifests_dir / f"{name}.yaml"
            render_tasks.append(
                run(name, f"curl -fsSL -o {out_file} '{prereq['url']}'")
            )
        elif prereq["type"] == "helm":
            values_file = work_dir / f"{name}-values.yaml"
            cmd = (
                f"helm repo add {name} {prereq['repo']} 2>/dev/null || true && "
                f"helm repo update {name} && "
                f"helm template {name} {name}/{prereq['chart']} "
                f"--version {prereq['version']} "
                f"--namespace {prereq['namespace']} "
                f"--skip-tests "
                f"-f {values_file} "
                f"--output-dir {manifests_dir}"
            )
            render_tasks.append(run(name, cmd))
        elif prereq["type"] == "oci-helm":
            values_file = work_dir / f"{name}-values.yaml"
            cmd = (
                f"helm template {name} {prereq['ociUrl']} "
                f"--version {prereq['version']} "
                f"--namespace {prereq['namespace']} "
                f"--skip-tests "
                f"-f {values_file} "
                f"--output-dir {manifests_dir}"
            )
            render_tasks.append(run(name, cmd))

    doit(render_tasks)

    return PrereqsResult(
        manifests_dir=manifests_dir,
        privileged_namespaces=privileged_namespaces,
    )
