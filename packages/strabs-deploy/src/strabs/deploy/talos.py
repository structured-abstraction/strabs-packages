"""
Talos cluster management.
"""

from dataclasses import dataclass
from pathlib import Path

from invoke import Context
from strabs.doit import doit, run, RunConfig


def _doit(tasks):
    doit(tasks, config=RunConfig(fail_fast=True))


@dataclass
class ClusterConfig:
    name: str
    project: str
    control_plane_nodes: int = 1
    worker_nodes: int = 1
    control_plane_cpus: str = "2"
    control_plane_memory: str = "2048"
    worker_cpus: str = "2"
    worker_memory: str = "2048"
    kubeconfigs_dir: Path = Path.home() / ".kube" / "configs"

    @property
    def context(self) -> str:
        return f"admin@{self.name}"

    @property
    def control_plane_container(self) -> str:
        return f"{self.name}-controlplane-1"

    @property
    def talosconfig(self) -> str:
        return f"~/.talos/clusters/{self.name}/talosconfig"

    def kubeconfig_path(self, env: str) -> Path:
        return self.kubeconfigs_dir / f"admin@{self.project}-{env}.yaml"

    def deployer_kubeconfig_path(self, env: str) -> Path:
        return self.kubeconfigs_dir / f"deploy@{self.project}-{env}.yaml"


class Cluster:
    def __init__(self, c: Context, config: ClusterConfig, config_patch: str | None = None):
        self.c = c
        self.cfg = config
        self.config_patch = config_patch

    def _get_control_plane_ip(self) -> str:
        result = self.c.run(
            f"docker inspect -f '{{{{range .NetworkSettings.Networks}}}}{{{{.IPAddress}}}}{{{{end}}}}' {self.cfg.control_plane_container}",
            hide=True, warn=True,
        )
        if result is None or not result.ok:
            return ""
        return result.stdout.strip()

    def require_control_plane_ip(self) -> str:
        ip = self._get_control_plane_ip()
        if not ip:
            raise SystemExit("Cluster not running. Run 'invoke cluster-setup' first.")
        return ip

    def setup(self, provider: str = "docker"):
        """Create the Talos cluster."""
        kubeconfig = self.cfg.kubeconfig_path("local")
        kubeconfig.parent.mkdir(parents=True, exist_ok=True)

        patch_arg = ""
        if self.config_patch:
            patch_file = Path("/tmp/talos-config-patch.yaml")
            patch_file.write_text(self.config_patch)
            patch_arg = f"--config-patch @{patch_file} "

        create_cmd = (
            f"talosctl cluster create "
            f"--name {self.cfg.name} "
            f"--controlplanes {self.cfg.control_plane_nodes} "
            f"--workers {self.cfg.worker_nodes} "
            f"--cpus {self.cfg.control_plane_cpus} "
            f"--cpus-workers {self.cfg.worker_cpus} "
            f"--memory {self.cfg.control_plane_memory} "
            f"--memory-workers {self.cfg.worker_memory} "
            f"--provisioner {provider} "
            f"--talosconfig {self.cfg.talosconfig} "
            f"--skip-kubeconfig "
            f"{patch_arg}"
            f"--wait"
        )

        _doit([
            run(f"Creating cluster '{self.cfg.name}'", create_cmd)
            .watching(f"docker logs -f {self.cfg.name}-controlplane-1 2>&1")
            .watching(f"docker logs -f {self.cfg.name}-worker-1 2>&1")
        ])

        cp_ip = self.require_control_plane_ip()
        self.cfg.kubeconfigs_dir.mkdir(parents=True, exist_ok=True)

        kubeconfig_cmd = f"""
            for i in $(seq 1 10); do
                talosctl kubeconfig {kubeconfig} -n {cp_ip} --force && exit 0
                sleep 2
            done
            exit 1
        """

        _doit([
            run("Merging talosconfig", f"talosctl config merge {self.cfg.talosconfig}"),
            run("Exporting kubeconfig", kubeconfig_cmd),
        ])

        print(f"\nCluster ready. Use: kubie ctx {self.cfg.context}")

    def teardown(self):
        """Destroy the Talos cluster."""
        _doit([
            run(f"Destroying cluster '{self.cfg.name}'", f"talosctl cluster destroy --name {self.cfg.name}"),
            run(
                "Cleaning kubectl config",
                f"kubectl config delete-context {self.cfg.context} 2>/dev/null; "
                f"kubectl config delete-cluster {self.cfg.name} 2>/dev/null; "
                f"kubectl config delete-user admin@{self.cfg.name} 2>/dev/null; true",
            ),
        ])
        self.cfg.kubeconfig_path("local").unlink(missing_ok=True)
        self.cfg.deployer_kubeconfig_path("local").unlink(missing_ok=True)

    def export_kubeconfig(self):
        """Export kubeconfig for existing cluster."""
        cp_ip = self.require_control_plane_ip()
        kubeconfig = self.cfg.kubeconfig_path("local")
        self.cfg.kubeconfigs_dir.mkdir(parents=True, exist_ok=True)
        _doit([run("Exporting kubeconfig", f"talosctl kubeconfig {kubeconfig} -n {cp_ip} --force")])
        print(f"Use: kubie ctx {self.cfg.context}")

    def status(self):
        """Show cluster status."""
        cp_ip = self.require_control_plane_ip()
        self.c.run(f"talosctl --talosconfig {self.cfg.talosconfig} --nodes {cp_ip} --endpoints {cp_ip} get members", pty=True, warn=True)
        self.c.run(f"kubectl --context {self.cfg.context} get nodes -o wide", pty=True, warn=True)
