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
    cmd = f"kcl run {files} -o {output} --format json"
    result = c.run(cmd, warn=True)
    if result is None or not result.ok:
        exit_code = result.return_code if result else 1
        raise SystemExit(f"KCL failed (exit {exit_code})")
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
    params_file: Path,
    work_dir: Path,
    secrets_file: Path | None = None,
) -> PrereqsResult:
    """Render juggernaut prereqs to a directory.

    Runs KCL to get prereq definitions, then renders:
    - URL prereqs via curl
    - Helm prereqs via helm template
    - Additional KCL manifests (clusterissuers, etc.)

    Args:
        c: Invoke context
        params_file: KCL cluster params file (must define clusterParams)
        work_dir: Working directory (should already exist)
        secrets_file: Optional KCL secrets file (decrypted)

    Returns:
        PrereqsResult with manifests_dir and privileged_namespaces
    """
    manifests_dir = work_dir / "manifests"
    manifests_dir.mkdir(parents=True, exist_ok=True)

    # Generate prereqs list render file
    prereqs_list_file = work_dir / "prereqs_list.k"
    if secrets_file:
        prereqs_list_file.write_text("""import juggernaut.prereqs.manifest

_secrets = manifest.ClusterSecrets {
    externalDnsApiToken = externalDnsApiToken
}
_output = manifest.make(clusterParams, _secrets)
prereqs = _output.prereqs
helmValues = _output.helmValues
privilegedNamespaces = _output.privilegedNamespaces
""")
    else:
        prereqs_list_file.write_text("""import juggernaut.prereqs.manifest

_output = manifest.make(clusterParams)
prereqs = _output.prereqs
helmValues = _output.helmValues
privilegedNamespaces = _output.privilegedNamespaces
""")

    kcl_files = [prereqs_list_file, params_file]
    if secrets_file:
        if not secrets_file.exists():
            raise SystemExit(f"Secrets file does not exist: {secrets_file}")
        kcl_files.append(secrets_file)

    # Get prereq list, helm values from KCL
    data = kcl_json(c, *kcl_files, output=work_dir / "prereqs.json")
    prereqs = data.get("prereqs", [])
    helm_values = data.get("helmValues", {})
    privileged_namespaces = data.get("privilegedNamespaces", [])

    # Generate manifests render file
    prereqs_manifests_file = work_dir / "prereqs_manifests.k"
    if secrets_file:
        prereqs_manifests_file.write_text("""import manifests
import juggernaut.prereqs.manifest

_secrets = manifest.ClusterSecrets {
    externalDnsApiToken = externalDnsApiToken
}
manifests.yaml_stream(manifest.make(clusterParams, _secrets).manifests)
""")
    else:
        prereqs_manifests_file.write_text("""import manifests
import juggernaut.prereqs.manifest

manifests.yaml_stream(manifest.make(clusterParams).manifests)
""")

    manifest_kcl_files = [prereqs_manifests_file, params_file]
    if secrets_file:
        manifest_kcl_files.append(secrets_file)
    kcl_yaml(c, *manifest_kcl_files, output=manifests_dir / "kcl-manifests.yaml")

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
                run(name, f"curl -fsSL -A 'Mozilla/5.0' -o {out_file} '{prereq['url']}'")
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


@dataclass
class AppExternalDnsResult:
    """Result of rendering app external-dns."""

    manifests_dir: Path


def render_app_externaldns(
    c: Context,
    params_file: Path,
    secrets_file: Path | None,
    manifests_dir: Path,
) -> bool:
    """Render external-dns helm chart + secret for app if externalDns is enabled.

    Outputs manifests directly to manifests_dir. Returns True if enabled and rendered.
    """
    import shutil

    # Use a temp dir inside manifests_dir (so kcl.mod is accessible)
    tmp_dir = manifests_dir / "_externaldns_tmp"
    tmp_dir.mkdir(parents=True, exist_ok=True)

    try:
        # Check if externalDns is enabled
        check_file = tmp_dir / "check.k"
        check_file.write_text("enabled = externalDns?.enabled or False\n")
        check_data = kcl_json(c, check_file, params_file, output=tmp_dir / "check.json")
        if not check_data.get("enabled", False):
            return False

        # Get helm values and namespace
        values_file = tmp_dir / "values.k"
        values_file.write_text("""import juggernaut.externaldns.app as externaldns

helmValues = externaldns.makeHelmValues(externalDns, params.namespace)
namespace = params.namespace
""")
        kcl_files = [values_file, params_file]
        if secrets_file:
            kcl_files.append(secrets_file)
        data = kcl_json(c, *kcl_files, output=tmp_dir / "values.json")
        helm_values = data.get("helmValues", {})
        namespace = data.get("namespace", "default")

        # Write helm values for helm template
        (tmp_dir / "helm-values.yaml").write_text(yaml.dump(helm_values))

        # Generate manifests directly via KCL
        manifests_file = tmp_dir / "manifests.k"
        manifests_file.write_text("""import manifests
import juggernaut.externaldns.app as externaldns

manifests.yaml_stream([
    externaldns.makeSecret(externalDnsApiToken, params.namespace)
    externaldns.makeRole(params.namespace)
    externaldns.makeRoleBinding(params.namespace)
])
""")
        manifest_kcl_files = [manifests_file, params_file]
        if secrets_file:
            manifest_kcl_files.append(secrets_file)
        kcl_yaml(c, *manifest_kcl_files, output=manifests_dir / "externaldns-manifests.yaml")

        helm_values_file = tmp_dir / "helm-values.yaml"
        cmd = (
            f"helm repo add external-dns https://kubernetes-sigs.github.io/external-dns 2>/dev/null || true && "
            f"helm repo update external-dns && "
            f"helm template external-dns external-dns/external-dns "
            f"--version 1.19.0 "
            f"--namespace {namespace} "
            f"--skip-tests "
            f"-f {helm_values_file} "
            f"--output-dir {manifests_dir}"
        )
        doit([run("external-dns", cmd)])

        return True
    finally:
        # Clean up temp files
        shutil.rmtree(tmp_dir, ignore_errors=True)
