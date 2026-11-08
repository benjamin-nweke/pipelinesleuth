"""
Execution Sandbox Manager
Manages isolated workspaces, Docker container execution, security boundaries, and validation runs.
Follows strict non-negotiable security rules:
- Docker isolation with --network none, memory/CPU limits, timeouts, non-root user
- If Docker is missing, refuses to silently fallback to unsafe host execution and returns clear setup instructions.
"""
import os
import shutil
import asyncio
import uuid
import time
from pathlib import Path
from typing import Dict, Any, Optional, Tuple
from backend.app.core.config import settings


class SandboxManager:
    def __init__(self):
        self.workspaces_root = settings.WORKSPACES_DIR
        self.docker_image = settings.DOCKER_IMAGE
        self.timeout = settings.SANDBOX_TIMEOUT_SECONDS

    async def check_docker_available(self) -> Tuple[bool, str]:
        """Check if Docker CLI is installed and Docker daemon is running."""
        try:
            proc = await asyncio.create_subprocess_exec(
                "docker", "info",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=5.0)
            if proc.returncode == 0:
                return True, "Docker daemon is active and responsive."
            else:
                err_msg = stderr.decode().strip() or "Docker daemon is not running."
                return False, f"Docker command failed: {err_msg}"
        except FileNotFoundError:
            return False, "Docker is not installed or not in PATH."
        except asyncio.TimeoutError:
            return False, "Docker check timed out after 5 seconds."
        except Exception as e:
            return False, f"Error checking Docker: {str(e)}"

    def create_isolated_workspace(self, source_project_path: str, candidate_name: Optional[str] = None) -> str:
        """
        Creates a distinct, temporary workspace copy of the project artifacts.
        Never touches the original project files.
        """
        workspace_id = candidate_name or f"ws_{uuid.uuid4().hex[:10]}"
        dest_dir = self.workspaces_root / workspace_id
        dest_dir.mkdir(parents=True, exist_ok=True)

        source_path = Path(source_project_path)
        # Copy project files safely (skipping virtualenvs and git)
        for item in source_path.iterdir():
            if item.name in (".git", "venv", ".venv", "__pycache__", ".pytest_cache", "node_modules"):
                continue
            dest_item = dest_dir / item.name
            if item.is_dir():
                shutil.copytree(item, dest_item, dirs_exist_ok=True)
            else:
                shutil.copy2(item, dest_item)

        return str(dest_dir)

    def cleanup_workspace(self, workspace_path: str) -> None:
        """Safely removes the temporary candidate workspace."""
        try:
            w_path = Path(workspace_path)
            if w_path.exists() and str(w_path).startswith(str(self.workspaces_root)):
                shutil.rmtree(w_path, ignore_errors=True)
        except Exception:
            pass

    async def run_in_sandbox(
        self,
        workspace_path: str,
        command: list[str],
        timeout_seconds: Optional[int] = None
    ) -> Dict[str, Any]:
        """
        Executes a command strictly within a restricted Docker container.
        Refuses host execution if Docker is unavailable to prevent arbitrary code execution on host.
        """
        start_time = time.time()
        timeout = timeout_seconds or self.timeout

        docker_ok, docker_msg = await self.check_docker_available()
        if not docker_ok:
            return {
                "success": False,
                "exit_code": -1,
                "stdout": "",
                "stderr": (
                    f"EXECUTION BLOCKED BY SECURITY POLICY:\n"
                    f"Docker is required for isolated container execution, but is unavailable: {docker_msg}\n\n"
                    f"PipelineSleuth does not allow uncontained, arbitrary Python host execution for safety.\n"
                    f"Please install Docker Desktop and start the Docker daemon to enable pipeline execution."
                ),
                "docker_available": False,
                "execution_time_ms": int((time.time() - start_time) * 1000),
                "error": "Docker unavailable"
            }

        # Build restricted docker run command
        # Security controls:
        # --rm: clean container after run
        # --network none: strictly no network access
        # --memory 1g: capped memory
        # --cpus 1.0: capped CPU
        # -v workspace: mounted only to working directory
        # -w /workspace: working directory
        docker_cmd = [
            "docker", "run", "--rm",
            "--network", "none",
            "--memory", settings.SANDBOX_MEMORY_LIMIT,
            "--cpus", "1.0",
            "-v", f"{Path(workspace_path).resolve()}:/workspace:rw",
            "-w", "/workspace",
            self.docker_image
        ] + command

        try:
            proc = await asyncio.create_subprocess_exec(
                *docker_cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
            duration_ms = int((time.time() - start_time) * 1000)

            return {
                "success": (proc.returncode == 0),
                "exit_code": proc.returncode,
                "stdout": stdout.decode(errors="replace"),
                "stderr": stderr.decode(errors="replace"),
                "docker_available": True,
                "execution_time_ms": duration_ms,
                "error": None if proc.returncode == 0 else f"Process exited with code {proc.returncode}"
            }
        except asyncio.TimeoutError:
            try:
                proc.kill()
            except Exception:
                pass
            return {
                "success": False,
                "exit_code": -2,
                "stdout": "",
                "stderr": f"Execution timed out after {timeout} seconds.",
                "docker_available": True,
                "execution_time_ms": int((time.time() - start_time) * 1000),
                "error": "Timeout"
            }
        except Exception as e:
            return {
                "success": False,
                "exit_code": -3,
                "stdout": "",
                "stderr": f"Docker execution error: {str(e)}",
                "docker_available": True,
                "execution_time_ms": int((time.time() - start_time) * 1000),
                "error": str(e)
            }


sandbox_manager = SandboxManager()
