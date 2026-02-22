"""Container-based sandbox for running skill code safely."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from pathlib import Path

import docker
import docker.errors

SKILL_IMAGE = "openforgyn-skill:latest"
DEFAULT_TIMEOUT = 60
DEFAULT_MEM_LIMIT = "256m"


def _get_docker_client() -> docker.DockerClient:
    """Create a Docker client, checking common socket paths on macOS."""
    # Try default first (DOCKER_HOST env var or /var/run/docker.sock)
    try:
        client = docker.from_env()
        client.ping()
        return client
    except Exception:
        pass

    # macOS Docker Desktop often uses alternate socket paths
    home = Path.home()
    alt_sockets = [
        home / ".docker" / "run" / "docker.sock",
        home / ".docker" / "desktop" / "docker.sock",
        Path("/var/run/docker.sock"),
    ]
    for sock in alt_sockets:
        if sock.exists():
            try:
                client = docker.DockerClient(base_url=f"unix://{sock}")
                client.ping()
                return client
            except Exception:
                continue

    raise docker.errors.DockerException("Cannot connect to Docker daemon")


@dataclass
class SandboxResult:
    exit_code: int = 0
    stdout: str = ""
    stderr: str = ""
    timed_out: bool = False


async def is_docker_available() -> bool:
    """Check if Docker daemon is reachable."""
    try:
        _get_docker_client()
        return True
    except Exception:
        return False


async def build_skill_image(dockerfile_dir: Path | None = None) -> str:
    """Build the skill base image. Returns the image tag."""
    client = _get_docker_client()

    # Use default Dockerfile location if not specified
    if dockerfile_dir is None:
        dockerfile_dir = Path(__file__).parent.parent / "docker"

    dockerfile_path = dockerfile_dir / "skill.Dockerfile"
    if not dockerfile_path.exists():
        raise FileNotFoundError(f"Skill Dockerfile not found at {dockerfile_path}")

    image, _ = client.images.build(
        path=str(dockerfile_dir),
        dockerfile="skill.Dockerfile",
        tag=SKILL_IMAGE,
        rm=True,
    )
    return SKILL_IMAGE


def _ensure_skill_image(client: docker.DockerClient) -> str:
    """Ensure the skill image exists, building it if needed."""
    try:
        client.images.get(SKILL_IMAGE)
        return SKILL_IMAGE
    except docker.errors.ImageNotFound:
        # Try to build from the project's Dockerfile
        dockerfile_dir = Path(__file__).parent.parent / "docker"
        if (dockerfile_dir / "skill.Dockerfile").exists():
            client.images.build(
                path=str(dockerfile_dir),
                dockerfile="skill.Dockerfile",
                tag=SKILL_IMAGE,
                rm=True,
            )
            return SKILL_IMAGE
        return "python:3.12-slim"


async def run_in_sandbox(
    skill_dir: Path,
    command: list[str] | None = None,
    timeout: int = DEFAULT_TIMEOUT,
    network: bool = False,
    env: dict[str, str] | None = None,
    install_deps: list[str] | None = None,
) -> SandboxResult:
    """Run a command inside a Docker container with the skill directory mounted.

    Args:
        skill_dir: Directory containing skill files (mounted at /skill).
        command: Command to run. Defaults to pytest.
        timeout: Max seconds before killing the container.
        network: Whether to allow network access.
        env: Environment variables to pass into the container.
        install_deps: pip packages to install before running command.
    """
    if command is None:
        command = ["python", "-m", "pytest", "-v", "-s", "."]

    try:
        client = _get_docker_client()
    except docker.errors.DockerException as e:
        return SandboxResult(exit_code=1, stderr=f"Docker not available: {e}")

    image = _ensure_skill_image(client)

    # Build the full command: optionally install deps, then run the actual command
    if install_deps:
        pip_install = f"pip install --quiet {' '.join(install_deps)}"
        shell_cmd = f"{pip_install} && {' '.join(command)}"
        full_command = ["sh", "-c", shell_cmd]
    else:
        full_command = command

    container = None
    try:
        container = client.containers.run(
            image=image,
            command=full_command,
            volumes={str(skill_dir.resolve()): {"bind": "/skill", "mode": "rw"}},
            working_dir="/skill",
            network_mode="none" if not network else "bridge",
            mem_limit=DEFAULT_MEM_LIMIT,
            nano_cpus=1_000_000_000,  # 1 CPU
            read_only=False,  # Need writable for pip install / pytest cache
            user="root" if install_deps else "nobody",
            environment=env or {},
            detach=True,
            stdout=True,
            stderr=True,
        )

        # Wait for container with timeout
        result = await asyncio.to_thread(container.wait, timeout=timeout)
        exit_code = result.get("StatusCode", 1)

        stdout = container.logs(stdout=True, stderr=False).decode("utf-8", errors="replace")
        stderr = container.logs(stdout=False, stderr=True).decode("utf-8", errors="replace")

        return SandboxResult(exit_code=exit_code, stdout=stdout, stderr=stderr)

    except Exception as e:
        err_msg = str(e)
        timed_out = "timed out" in err_msg.lower() or "read timeout" in err_msg.lower()

        # Try to capture any output before cleanup
        stdout = ""
        stderr = err_msg
        if container:
            try:
                stdout = container.logs(stdout=True, stderr=False).decode("utf-8", errors="replace")
                stderr = container.logs(stdout=False, stderr=True).decode("utf-8", errors="replace")
            except Exception:
                pass

        return SandboxResult(exit_code=1, stdout=stdout, stderr=stderr, timed_out=timed_out)

    finally:
        if container:
            try:
                container.remove(force=True)
            except Exception:
                pass


async def run_skill_handler(
    skill_dir: Path,
    args_json: str,
    network: bool = False,
    env: dict[str, str] | None = None,
    install_deps: list[str] | None = None,
    timeout: int = DEFAULT_TIMEOUT,
) -> SandboxResult:
    """Run a skill's handler.py with JSON args, capturing its output."""
    runner_code = (
        "import json, sys, asyncio, importlib.util\n"
        "spec = importlib.util.spec_from_file_location('handler', '/skill/handler.py')\n"
        "mod = importlib.util.module_from_spec(spec)\n"
        "spec.loader.exec_module(mod)\n"
        f"result = asyncio.run(mod.run(json.loads({args_json!r})))\n"
        "print(json.dumps(result))\n"
    )
    return await run_in_sandbox(
        skill_dir=skill_dir,
        command=["python", "-c", runner_code],
        timeout=timeout,
        network=network,
        env=env,
        install_deps=install_deps,
    )
