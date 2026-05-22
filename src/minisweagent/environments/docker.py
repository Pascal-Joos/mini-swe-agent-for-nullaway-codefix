import logging
import os
import shlex
import subprocess
import uuid
import hashlib
from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass
class DockerEnvironmentConfig:
    image: str
    cwd: str = "/"
    """Working directory in which to execute commands."""
    env: dict[str, str] = field(default_factory=dict)
    """Environment variables to set in the container."""
    forward_env: list[str] = field(default_factory=list)
    """Environment variables to forward to the container.
    Variables are only forwarded if they are set in the host environment.
    In case of conflict with `env`, the `env` variables take precedence.
    """
    timeout: int = 30
    """Timeout for executing commands in the container."""
    executable: str = os.getenv("MSWEA_DOCKER_EXECUTABLE", "docker")
    """Path to the docker/container executable."""
    run_args: list[str] = field(default_factory=lambda: ["--rm"])
    """Additional arguments to pass to the docker/container executable.
    Default is ["--rm"], which removes the container after it exits.
    """
    container_timeout: str = "2h"
    """Max duration to keep container running. Uses the same format as the sleep command."""
    pull_timeout: int = 120
    """Timeout in seconds for pulling images."""


class DockerEnvironment:
    def __init__(self, *, config_class: type = DockerEnvironmentConfig, logger: logging.Logger | None = None, **kwargs):
        """This class executes bash commands in a Docker container using direct docker commands.
        See `DockerEnvironmentConfig` for keyword arguments.
        """
        self.logger = logger or logging.getLogger("minisweagent.environment")
        self.container_id: str | None = None
        self.config = config_class(**kwargs)
        self._start_container()

    def get_template_vars(self) -> dict[str, Any]:
        # Get platform info from inside the container
        result = self.execute("python3 -c 'import platform; import json; print(json.dumps(platform.uname()._asdict()))'", timeout=5)
        platform_info = {}
        if result["returncode"] == 0:
            import json
            try:
                platform_info = json.loads(result["output"].strip())
            except json.JSONDecodeError:
                pass
        return asdict(self.config) | platform_info

    def __build_image(self):
        """Build the Docker image if it doesn't exist using docker build -t joos/minisweagent_for_nullrepair mini-swe-agent-for-nullaway-codefix/src/minisweagent/environments."""
        result = subprocess.run(
            [self.config.executable, "images", "-q", self.config.image],
            capture_output=True,
            text=True,
            timeout=30,
        )
        if result.returncode != 0:
            self.logger.error(f"Failed to check for existing image: {result.stderr}")
            raise RuntimeError("Failed to check for existing Docker image")
        if not result.stdout.strip():
            self.logger.info(f"Image {self.config.image} not found locally. Building image...")
            build_result = subprocess.run(
                [self.config.executable, "build", "-t", self.config.image, "mini-swe-agent-for-nullaway-codefix/src/minisweagent/environments/"],
                capture_output=True,
                text=True,
                timeout=300,
            )
            if build_result.returncode != 0:
                self.logger.error(f"Failed to build Docker image: {build_result.stderr}")
                raise RuntimeError("Failed to build Docker image")
            self.logger.info(f"Successfully built image {self.config.image}")
        else:
            self.logger.info(f"Image {self.config.image} found locally.")

    # Use volume mounts if running Docker in container. If running in Dev Container don't use volume mounts
    def _use_volume_mounts(self) -> bool:
        # Check for environment variable that indicates we're running in a Dev Container
        if os.getenv("DEVCONTAINER") == "true":
            return False
        
        return os.path.exists("/.dockerenv")
        

    def _get_self_container_id(self) -> str | None:
        if not self._use_volume_mounts():
            return None
        hostname = os.getenv("HOSTNAME")
        if hostname:
            return hostname.strip()
        try:
            with open("/etc/hostname", "r", encoding="utf-8") as handle:
                return handle.read().strip()
        except OSError:
            return None

    def _ensure_volume(self, name: str) -> None:
        subprocess.run(
            [self.config.executable, "volume", "create", name],
            capture_output=True,
            text=True,
            timeout=30,
            check=True,
        )

    def _seed_project_volume(self, volume_name: str) -> None:
        container_id = self._get_self_container_id()
        if not container_id:
            raise RuntimeError("Unable to resolve container ID for volume seeding")

        check_cmd = [
            self.config.executable,
            "run",
            "--rm",
            "-v",
            f"{volume_name}:/data",
            "alpine",
            "sh",
            "-c",
            "test -f /data/.minisweagent-seeded",
        ]
        check_result = subprocess.run(
            check_cmd,
            capture_output=True,
            text=True,
            timeout=30,
        )
        if check_result.returncode == 0:
            return

        tar_cmd = [
            self.config.executable,
            "exec",
            container_id,
            "tar",
            "-C",
            self.config.cwd,
            "-cf",
            "-",
            ".",
        ]
        untar_cmd = [
            self.config.executable,
            "run",
            "--rm",
            "-i",
            "-v",
            f"{volume_name}:/data",
            "alpine",
            "tar",
            "-C",
            "/data",
            "-xf",
            "-",
        ]

        tar_proc = subprocess.Popen(
            tar_cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=False,
        )
        untar_result = subprocess.run(
            untar_cmd,
            stdin=tar_proc.stdout,
            capture_output=True,
            text=True,
            timeout=self.config.pull_timeout,
        )
        if tar_proc.stdout:
            tar_proc.stdout.close()
        tar_stderr = tar_proc.stderr.read().decode("utf-8", "replace") if tar_proc.stderr else ""
        tar_returncode = tar_proc.wait(timeout=self.config.pull_timeout)

        if tar_returncode != 0 or untar_result.returncode != 0:
            self.logger.error(f"Failed to seed volume {volume_name}. tar: {tar_stderr} untar: {untar_result.stderr}")
            raise RuntimeError("Failed to seed project volume")

        mark_cmd = [
            self.config.executable,
            "run",
            "--rm",
            "-v",
            f"{volume_name}:/data",
            "alpine",
            "sh",
            "-c",
            "touch /data/.minisweagent-seeded",
        ]
        subprocess.run(
            mark_cmd,
            capture_output=True,
            text=True,
            timeout=30,
            check=True,
        )

    def _seed_cache_volume(self, volume_name: str, source_path: str, marker: str) -> None:
        if not os.path.exists(source_path):
            return
        container_id = self._get_self_container_id()
        if not container_id:
            raise RuntimeError("Unable to resolve container ID for cache seeding")

        check_cmd = [
            self.config.executable,
            "run",
            "--rm",
            "-v",
            f"{volume_name}:/data",
            "alpine",
            "sh",
            "-c",
            f"test -f /data/{marker}",
        ]
        check_result = subprocess.run(
            check_cmd,
            capture_output=True,
            text=True,
            timeout=30,
        )
        if check_result.returncode == 0:
            return

        tar_cmd = [
            self.config.executable,
            "exec",
            container_id,
            "tar",
            "-C",
            source_path,
            "-cf",
            "-",
            ".",
        ]
        untar_cmd = [
            self.config.executable,
            "run",
            "--rm",
            "-i",
            "-v",
            f"{volume_name}:/data",
            "alpine",
            "tar",
            "-C",
            "/data",
            "-xf",
            "-",
        ]

        tar_proc = subprocess.Popen(
            tar_cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=False,
        )
        untar_result = subprocess.run(
            untar_cmd,
            stdin=tar_proc.stdout,
            capture_output=True,
            text=True,
            timeout=self.config.pull_timeout,
        )
        if tar_proc.stdout:
            tar_proc.stdout.close()
        tar_stderr = tar_proc.stderr.read().decode("utf-8", "replace") if tar_proc.stderr else ""
        tar_returncode = tar_proc.wait(timeout=self.config.pull_timeout)

        if tar_returncode != 0 or untar_result.returncode != 0:
            self.logger.error(f"Failed to seed volume {volume_name}. tar: {tar_stderr} untar: {untar_result.stderr}")
            raise RuntimeError("Failed to seed cache volume")

        mark_cmd = [
            self.config.executable,
            "run",
            "--rm",
            "-v",
            f"{volume_name}:/data",
            "alpine",
            "sh",
            "-c",
            f"touch /data/{marker}",
        ]
        subprocess.run(
            mark_cmd,
            capture_output=True,
            text=True,
            timeout=30,
            check=True,
        )

    def _get_project_volume_name(self) -> str:
        digest = hashlib.sha256(self.config.cwd.encode("utf-8")).hexdigest()[:12]
        return f"minisweagent-project-{digest}"

    def _ensure_project_volume(self) -> str:
        volume_name = self._get_project_volume_name()
        self._ensure_volume(volume_name)
        self._seed_project_volume(volume_name)
        return volume_name

    def _ensure_cache_volume(self, name: str) -> str:
        self._ensure_volume(name)
        return name


    def _start_container(self):
        """Start the Docker container and return the container ID."""

        # Ensure the image is built before starting the container
        self.__build_image()

        container_name = f"joos_minisweagent-{uuid.uuid4().hex[:8]}"
        cmd = [
            self.config.executable,
            "run",
            "-d",
            "--name",
            container_name,
            "-w",
            self.config.cwd,
            *self.config.run_args,
        ]
        if self._use_volume_mounts():
            project_volume = self._ensure_project_volume()
            gradle_volume = self._ensure_cache_volume("minisweagent-gradle-cache")
            m2_volume = self._ensure_cache_volume("minisweagent-m2-cache")
            home_dir = os.getenv("HOME")
            if home_dir:
                self._seed_cache_volume(gradle_volume, os.path.join(home_dir, ".gradle"), ".minisweagent-gradle-seeded")
                self._seed_cache_volume(m2_volume, os.path.join(home_dir, ".m2"), ".minisweagent-m2-seeded")
            cmd.extend([
                "--mount",
                f"type=volume,src={project_volume},target={self.config.cwd}",
                "--mount",
                f"type=volume,src={gradle_volume},target={os.getenv('HOME')}/.gradle",
                "--mount",
                f"type=volume,src={m2_volume},target={os.getenv('HOME')}/.m2",
            ])
        else:
            cmd.extend([
                "--mount",
                f"type=bind,source={self.config.cwd},target={self.config.cwd}",
                "-v",
                f"{self.config.cwd}/.gradle-cache:{os.getenv('HOME')}/.gradle",
                "-v",
                f"{os.getenv('HOME')}/.m2:{os.getenv('HOME')}/.m2",
            ])
        cmd.extend([
            "-e", 
            f"HOST_UID={os.getuid()}",
            "-e",
            f"HOST_GID={os.getgid()}",
            "-e", 
            f"HOST_USER={os.getenv('USER')}",
            "-e",
            f"HOST_HOME={os.getenv('HOME')}",
            self.config.image,
            "sleep",
            self.config.container_timeout,
        ])
        self.logger.debug(f"Starting container with command: {shlex.join(cmd)}")
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=self.config.pull_timeout,  # docker pull might take a while
            check=True,
        )
        self.logger.info(f"Started container {container_name} with ID {result.stdout.strip()}")
        self.container_id = result.stdout.strip()

    def execute(self, command: str, cwd: str = "", *, timeout: int | None = None) -> dict[str, Any]:
        """Execute a command in the Docker container and return the result as a dict."""
        cwd = cwd or self.config.cwd
        assert self.container_id, "Container not started"

        cmd = [self.config.executable, "exec", "-w", cwd]
        for key in self.config.forward_env:
            if (value := os.getenv(key)) is not None:
                cmd.extend(["-e", f"{key}={value}"])
        for key, value in self.config.env.items():
            cmd.extend(["-e", f"{key}={value}"])

        # Append user config to command
        cmd.extend(["--user", f"{os.getenv('USER')}"])

        cmd.extend([self.container_id, "bash", "-lc", command])

        result = subprocess.run(
            cmd,
            text=True,
            timeout=timeout or self.config.timeout,
            encoding="utf-8",
            errors="replace",
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
        )
        return {"output": result.stdout, "returncode": result.returncode}

    def cleanup(self):
        """Stop and remove the Docker container."""
        if getattr(self, "container_id", None) is not None:  # if init fails early, container_id might not be set
            cmd = f"(timeout 60 {self.config.executable} stop {self.container_id} || {self.config.executable} rm -f {self.container_id}) >/dev/null 2>&1 &"
            subprocess.Popen(cmd, shell=True)

    def __del__(self):
        """Cleanup container when object is destroyed."""
        self.cleanup()
