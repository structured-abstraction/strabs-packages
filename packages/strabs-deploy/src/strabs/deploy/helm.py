"""
Helm chart operations.
"""

import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path


class HelmError(Exception):
    """Base exception for helm operations."""

    pass


class RepoAddError(HelmError):
    """Failed to add helm repository."""

    pass


class RepoUpdateError(HelmError):
    """Failed to update helm repository."""

    pass


class TemplateError(HelmError):
    """Failed to render helm template."""

    pass


@dataclass(frozen=True)
class HelmRepo:
    """A helm repository."""

    name: str
    url: str

    def add(self, force: bool = False) -> None:
        """Add this repository to helm.

        Args:
            force: If True, update if already exists

        Raises:
            RepoAddError: If helm repo add fails
        """
        cmd = ["helm", "repo", "add", self.name, self.url]
        if force:
            cmd.append("--force-update")

        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode != 0 and "already exists" not in result.stderr:
            raise RepoAddError(f"Failed to add repo {self.name}: {result.stderr}")

    def update(self) -> None:
        """Update this repository.

        Raises:
            RepoUpdateError: If helm repo update fails
        """
        result = subprocess.run(
            ["helm", "repo", "update", self.name],
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            raise RepoUpdateError(f"Failed to update repo {self.name}: {result.stderr}")


@dataclass
class HelmChart:
    """A helm chart with templating capabilities.

    Example:
        chart = HelmChart(
            repo=HelmRepo("project-zot", "http://zotregistry.dev/helm-charts"),
            chart="zot",
            release_name="zot",
            namespace="zot",
        )

        # Ensure repo is available
        chart.repo.add()
        chart.repo.update()

        # Render templates
        output_dir = chart.template(
            values_file=Path("values.yaml"),
            output_dir=Path("_rendered/zot"),
        )

        # Apply patches
        chart.patch_file(
            output_dir / "zot/templates/deployment.yaml",
            find="serviceAccountName: zot",
            replace="serviceAccountName: zot\\n      hostNetwork: true",
        )
    """

    repo: HelmRepo
    chart: str
    release_name: str
    namespace: str
    version: str | None = None

    @property
    def chart_ref(self) -> str:
        """Full chart reference (repo/chart)."""
        return f"{self.repo.name}/{self.chart}"

    def template(
        self,
        values_file: Path | None = None,
        output_dir: Path | None = None,
        skip_tests: bool = True,
        extra_args: list[str] | None = None,
    ) -> Path:
        """Render helm templates to a directory.

        Args:
            values_file: Path to values YAML file
            output_dir: Directory to write rendered templates (default: temp dir)
            skip_tests: Whether to skip test templates
            extra_args: Additional arguments to pass to helm template

        Returns:
            Path to the output directory containing rendered templates

        Raises:
            TemplateError: If helm template fails
        """
        if output_dir is None:
            output_dir = Path(f"/tmp/helm-{self.release_name}")

        # Clean output directory
        if output_dir.exists():
            shutil.rmtree(output_dir)
        output_dir.mkdir(parents=True)

        cmd = [
            "helm",
            "template",
            self.release_name,
            self.chart_ref,
            "--namespace",
            self.namespace,
            "--output-dir",
            str(output_dir),
        ]

        if self.version:
            cmd.extend(["--version", self.version])

        if values_file:
            cmd.extend(["-f", str(values_file)])

        if skip_tests:
            cmd.append("--skip-tests")

        if extra_args:
            cmd.extend(extra_args)

        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode != 0:
            raise TemplateError(f"Failed to template {self.chart_ref}: {result.stderr}")

        return output_dir

    def patch_file(
        self,
        file_path: Path,
        find: str,
        replace: str,
    ) -> None:
        """Apply a string replacement patch to a rendered file.

        Args:
            file_path: Path to the file to patch
            find: String to find
            replace: String to replace with

        Raises:
            FileNotFoundError: If file doesn't exist
            ValueError: If find string not found in file
        """
        if not file_path.exists():
            raise FileNotFoundError(f"Cannot patch: {file_path} not found")

        content = file_path.read_text()
        if find not in content:
            raise ValueError(f"Pattern not found in {file_path}: {find}")

        content = content.replace(find, replace)
        file_path.write_text(content)


def prepare_chart(
    repo_name: str,
    repo_url: str,
    chart: str,
    release_name: str,
    namespace: str,
    version: str | None = None,
) -> HelmChart:
    """Create a HelmChart and ensure its repo is ready.

    Convenience function that creates the chart, adds the repo,
    and updates it in one call.

    Example:
        chart = prepare_chart(
            repo_name="project-zot",
            repo_url="http://zotregistry.dev/helm-charts",
            chart="zot",
            release_name="zot",
            namespace="zot",
        )
        output = chart.template(values_file=my_values)
    """
    repo = HelmRepo(repo_name, repo_url)
    repo.add(force=True)
    repo.update()

    return HelmChart(
        repo=repo,
        chart=chart,
        release_name=release_name,
        namespace=namespace,
        version=version,
    )
