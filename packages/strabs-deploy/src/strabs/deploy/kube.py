"""
Kubernetes helpers using kubectl.
"""

from dataclasses import dataclass
from pathlib import Path

from invoke import Context

from strabs.doit import doit, run


@dataclass
class ClusterInfo:
    """Current cluster connection info."""

    server: str
    ca_data: str


def get_cluster_info(c: Context) -> ClusterInfo:
    """Get current cluster server URL and CA data.

    Returns:
        ClusterInfo with server URL and base64 CA data
    """
    server_result = c.run(
        "kubectl config view --minify -o jsonpath='{.clusters[0].cluster.server}'",
        hide=True,
    )
    assert server_result is not None
    server = server_result.stdout.strip()
    ca_result = c.run(
        "kubectl config view --minify --raw -o jsonpath='{.clusters[0].cluster.certificate-authority-data}'",
        hide=True,
    )
    assert ca_result is not None
    ca_data = ca_result.stdout.strip()
    return ClusterInfo(server=server, ca_data=ca_data)


def create_service_account_token(
    c: Context, service_account: str, namespace: str, duration: str = "8760h"
) -> str:
    """Create a token for a service account.

    Args:
        c: Invoke context
        service_account: Name of the service account
        namespace: Namespace of the service account
        duration: Token duration (default: 1 year)

    Returns:
        The token string
    """
    result = c.run(
        f"kubectl create token {service_account} -n {namespace} --duration={duration}",
        hide=True,
    )
    assert result is not None
    return result.stdout.strip()


def create_kubeconfig(
    c: Context,
    output_path: Path,
    cluster_name: str,
    context_name: str,
    user_name: str,
    namespace: str,
    service_account: str,
    token_duration: str = "8760h",
) -> None:
    """Create a kubeconfig file for a service account.

    Args:
        c: Invoke context
        output_path: Path to write kubeconfig
        cluster_name: Name for the cluster in kubeconfig
        context_name: Name for the context in kubeconfig
        user_name: Name for the user in kubeconfig
        namespace: Default namespace for the context
        service_account: Service account to create token for
        token_duration: Token duration (default: 1 year)
    """
    info = get_cluster_info(c)
    token = create_service_account_token(c, service_account, namespace, token_duration)

    kubeconfig = f"""apiVersion: v1
kind: Config
clusters:
- cluster:
    certificate-authority-data: {info.ca_data}
    server: {info.server}
  name: {cluster_name}
contexts:
- context:
    cluster: {cluster_name}
    namespace: {namespace}
    user: {user_name}
  name: {context_name}
current-context: {context_name}
users:
- name: {user_name}
  user:
    token: {token}
"""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(kubeconfig)


def ensure_namespace(name: str, privileged: bool = False) -> None:
    """Create namespace if it doesn't exist, optionally with privileged PSA."""
    doit([run(f"Creating namespace '{name}'", f"kubectl create namespace {name} 2>/dev/null || true")])
    if privileged:
        patch_namespace_privileged(name)


def patch_namespace_privileged(name: str) -> None:
    """Patch namespace with privileged pod security level."""
    doit([run(f"Patching namespace '{name}'", f"kubectl label namespace {name} pod-security.kubernetes.io/enforce=privileged --overwrite")])


def wait_for_deployment(name: str, namespace: str, timeout: int = 300) -> None:
    """Wait for deployment to be available."""
    doit([run(f"Waiting for {name}", f"kubectl wait --for=condition=Available deployment/{name} -n {namespace} --timeout={timeout}s")])


def create_tls_secret(name: str, namespace: str, cert_path: str, key_path: str) -> None:
    """Create or update a TLS secret."""
    doit([
        run(f"Deleting old secret '{name}'", f"kubectl delete secret {name} -n {namespace} 2>/dev/null || true"),
        run(f"Creating secret '{name}'", f"kubectl create secret tls {name} -n {namespace} --cert={cert_path} --key={key_path}"),
    ])
