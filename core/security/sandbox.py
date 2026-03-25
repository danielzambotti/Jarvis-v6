"""
core/security/sandbox.py — Zero-Trust Ephemeral Code Execution Sandbox
=======================================================================
Executes untrusted LLM-generated code inside a disposable Docker container
with hard resource limits. The host filesystem is NEVER touched by the guest
process. Every execution spawns a fresh container and is forcibly killed after
a strict wall-clock timeout.

Security boundaries enforced:
  - Network:    --network none           (no inbound or outbound traffic)
  - Filesystem: read-only root + tmpfs   (no persistent writes, no host mounts)
  - CPU:        --cpus 0.5               (half a core max)
  - Memory:     --memory 128m            (128 MB hard limit)
  - PIDs:       --pids-limit 64          (prevents fork bombs)
  - Capabilities: --cap-drop ALL         (no Linux capabilities)
  - Privilege:  --no-new-privileges      (cannot escalate via setuid)
  - User:       --user nobody            (non-root inside container)
  - Image:      python:3.11-slim         (minimal attack surface)
  - Lifecycle:  --rm                     (auto-deleted on exit)
  - Timeout:    10 s wall-clock          (enforced externally via SIGKILL)

Usage:
    from core.security.sandbox import run_in_sandbox

    result = run_in_sandbox(code="print('hello')", timeout=10)
    print(result.stdout)
    print(result.stderr)
    print(result.exit_code)      # -1 = timeout / docker error
    print(result.timed_out)
"""

import logging
import subprocess
import textwrap
from dataclasses import dataclass, field
from typing import Optional

logger = logging.getLogger(__name__)

# ── Constants ─────────────────────────────────────────────────────────────────

SANDBOX_IMAGE = "python:3.11-slim"
DEFAULT_TIMEOUT = 10  # seconds — hard wall-clock limit

# Docker flags that define the security boundary.
# This list is the canonical record of every restriction applied.
_SECURITY_FLAGS: list[str] = [
    "--network", "none",          # No network access whatsoever
    "--memory", "128m",           # RAM cap
    "--cpus", "0.5",              # CPU cap (0.5 = half a core)
    "--pids-limit", "64",         # Prevent fork bombs
    "--cap-drop", "ALL",          # Drop ALL Linux capabilities
    "--security-opt", "no-new-privileges:true",  # Cannot gain privileges via exec
    "--user", "nobody",           # Run as unprivileged user
    "--read-only",                # Root filesystem is read-only
    "--tmpfs", "/tmp:size=32m",   # Allow writes only in ephemeral /tmp
    "--rm",                       # Auto-delete container on exit
]


# ── Result type ───────────────────────────────────────────────────────────────

@dataclass
class SandboxResult:
    stdout: str = ""
    stderr: str = ""
    exit_code: int = -1
    timed_out: bool = False
    docker_error: Optional[str] = None

    @property
    def success(self) -> bool:
        return self.exit_code == 0 and not self.timed_out and self.docker_error is None

    def summary(self) -> str:
        if self.timed_out:
            return f"[SANDBOX] TIMEOUT after {DEFAULT_TIMEOUT}s — process killed."
        if self.docker_error:
            return f"[SANDBOX] Docker error: {self.docker_error}"
        status = "PASS" if self.exit_code == 0 else f"FAIL (exit {self.exit_code})"
        lines = []
        if self.stdout.strip():
            lines.append(f"STDOUT:\n{self.stdout.strip()}")
        if self.stderr.strip():
            lines.append(f"STDERR:\n{self.stderr.strip()}")
        return f"[SANDBOX] {status}\n" + "\n".join(lines)


# ── Core execution function ───────────────────────────────────────────────────

def run_in_sandbox(
    code: str,
    timeout: int = DEFAULT_TIMEOUT,
    extra_packages: list[str] | None = None,
) -> SandboxResult:
    """
    Execute `code` inside an ephemeral Docker sandbox.

    The code string is passed via stdin to `python -` so no file is written
    to the host. The container is killed after `timeout` seconds regardless
    of its state.

    Args:
        code:           Python source code to execute.
        timeout:        Wall-clock seconds before SIGKILL. Default: 10.
        extra_packages: Optional list of pip packages to install before
                        running (e.g. ["pytest", "numpy"]). Each package
                        is installed with --no-cache-dir inside the container.

    Returns:
        SandboxResult with stdout, stderr, exit_code, and timed_out flag.
    """
    # If extra packages are needed, prepend a pip install step.
    if extra_packages:
        pkgs = " ".join(extra_packages)
        install_prefix = textwrap.dedent(f"""\
            import subprocess as _sp
            _sp.run(
                ["pip", "install", "--quiet", "--no-cache-dir", {pkgs!r}],
                check=True,
            )
        """)
        code = install_prefix + "\n" + code

    cmd: list[str] = [
        "docker", "run",
        *_SECURITY_FLAGS,
        "--interactive",          # Accept stdin
        SANDBOX_IMAGE,
        "python", "-",            # Read code from stdin — no host file needed
    ]

    logger.info("[SANDBOX] Spawning ephemeral container (timeout=%ds)...", timeout)
    logger.debug("[SANDBOX] CMD: %s", " ".join(cmd))

    try:
        proc = subprocess.run(
            cmd,
            input=code.encode("utf-8"),
            capture_output=True,
            timeout=timeout,
        )
        result = SandboxResult(
            stdout=proc.stdout.decode("utf-8", errors="replace"),
            stderr=proc.stderr.decode("utf-8", errors="replace"),
            exit_code=proc.returncode,
        )
        logger.info("[SANDBOX] Container exited with code %d.", proc.returncode)
        return result

    except subprocess.TimeoutExpired as exc:
        logger.warning("[SANDBOX] Timeout (%ds) — killing container.", timeout)
        # proc is not accessible here; Docker will clean up the --rm container
        # on its own once the parent process dies, but we force cleanup anyway.
        _force_cleanup(cmd)
        return SandboxResult(
            stdout=exc.stdout.decode("utf-8", errors="replace") if exc.stdout else "",
            stderr=exc.stderr.decode("utf-8", errors="replace") if exc.stderr else "",
            timed_out=True,
        )

    except FileNotFoundError:
        msg = "Docker not found. Is the Docker daemon running and in PATH?"
        logger.error("[SANDBOX] %s", msg)
        return SandboxResult(docker_error=msg)

    except Exception as exc:
        logger.error("[SANDBOX] Unexpected error: %s", exc)
        return SandboxResult(docker_error=str(exc))


# ── Helpers ───────────────────────────────────────────────────────────────────

def _force_cleanup(original_cmd: list[str]) -> None:
    """
    Best-effort: list and kill any dangling containers using the sandbox image.
    Called only on timeout. Since containers are --rm they usually self-clean,
    but this is a safety net.
    """
    try:
        dangling = subprocess.run(
            ["docker", "ps", "-q", "--filter", f"ancestor={SANDBOX_IMAGE}"],
            capture_output=True, timeout=5,
        )
        for cid in dangling.stdout.decode().splitlines():
            cid = cid.strip()
            if cid:
                subprocess.run(["docker", "kill", cid], capture_output=True, timeout=5)
                logger.warning("[SANDBOX] Force-killed container: %s", cid)
    except Exception as e:
        logger.error("[SANDBOX] Cleanup failed: %s", e)
