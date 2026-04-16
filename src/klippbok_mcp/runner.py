"""Async subprocess wrapper for Klippbok CLI invocations.

MCP tool handlers are async, so we use ``asyncio.create_subprocess_exec``
rather than ``subprocess.run``. Every tool gathers the full stdout/stderr
and returns a ``CommandResult`` dataclass — no streaming back to the MCP
client yet (can be added later via ``ctx.info()`` / ``ctx.report_progress``
if a caller needs it).

Why a dedicated module:
  - One place for the env contract (PYTHONUTF8, Klippbok Python path).
  - Testable in isolation against mock subprocess behaviour.
  - Keeps ``server.py`` focused on the MCP tool surface.
"""
from __future__ import annotations

import asyncio
import os
import sys
import time
from dataclasses import asdict, dataclass
from typing import Any, Iterable, Optional


_DEFAULT_TIMEOUT_SECONDS = 600.0


@dataclass
class CommandResult:
    """Structured outcome of a subprocess call.

    Exit code ``-1`` is reserved for failures where the process never
    started (e.g. executable not found) or was killed by timeout. Check
    ``timed_out`` to disambiguate.
    """

    command: list[str]
    exit_code: int
    stdout: str
    stderr: str
    duration_seconds: float
    timed_out: bool = False

    @property
    def succeeded(self) -> bool:
        return self.exit_code == 0 and not self.timed_out

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def short_summary(self) -> str:
        """One-line rendering for log/error surfacing."""
        status = "ok" if self.succeeded else (
            "timeout" if self.timed_out else f"exit={self.exit_code}"
        )
        return f"[{status}  elapsed={self.duration_seconds:.1f}s]"


def python_executable() -> str:
    """Return the Python interpreter used for Klippbok invocations.

    Reads ``KLIPPBOK_PYTHON`` each time so an updated environment takes
    effect on the next tool call (no module-level caching). Falls back to
    ``sys.executable`` — which works when the MCP server is running in the
    same venv as Klippbok.
    """
    # CLAUDE-NOTE: Defaulting to sys.executable (not crashing on missing env
    # var) keeps the zero-config path alive when a user installs both MCP
    # server and Klippbok in the same venv. Config blocks in the README
    # point KLIPPBOK_PYTHON at the common separate-venv case.
    return os.environ.get("KLIPPBOK_PYTHON") or sys.executable


def build_env(extra: Optional[dict[str, str]] = None) -> dict[str, str]:
    """Assemble the env dict passed to every subprocess.

    - ``PYTHONUTF8=1``: forces Python's UTF-8 mode for the child and any
      subprocess it spawns. Klippbok shells out to ffprobe internally;
      without this, a non-ASCII byte in a filename crashes its reader
      thread with ``UnicodeDecodeError: 'charmap' codec`` on Windows.
    - ``PYTHONIOENCODING=utf-8``: belt-and-suspenders for older code paths
      that don't consult PYTHONUTF8.
    - ``PYTHONUNBUFFERED=1``: flushes stdout/stderr immediately so output
      is available as it arrives (useful if we later add streaming).
    - ``extra``: caller-supplied keys (API keys, typically). Empty values
      are skipped so an un-set key doesn't clobber an inherited one.
    """
    env = dict(os.environ)
    env["PYTHONUTF8"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUNBUFFERED"] = "1"
    if extra:
        for key, value in extra.items():
            if value:
                env[key] = value
    return env


async def run_command(
    cmd: list[str],
    *,
    timeout: float = _DEFAULT_TIMEOUT_SECONDS,
    extra_env: Optional[dict[str, str]] = None,
    cwd: Optional[str] = None,
) -> CommandResult:
    """Run an arbitrary command list asynchronously.

    Used by ``run_klippbok`` for pipeline commands and by the install-check
    tool for raw ``ffmpeg -version`` probes.
    """
    start = time.monotonic()
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=build_env(extra_env),
            cwd=cwd,
        )
    except FileNotFoundError as exc:
        return CommandResult(
            command=cmd,
            exit_code=-1,
            stdout="",
            stderr=f"executable not found: {exc}",
            duration_seconds=0.0,
        )

    timed_out = False
    try:
        stdout_bytes, stderr_bytes = await asyncio.wait_for(
            proc.communicate(), timeout=timeout
        )
    except asyncio.TimeoutError:
        proc.kill()
        stdout_bytes, stderr_bytes = await proc.communicate()
        timed_out = True

    elapsed = time.monotonic() - start
    stdout = stdout_bytes.decode("utf-8", errors="replace")
    stderr = stderr_bytes.decode("utf-8", errors="replace")
    exit_code = proc.returncode if proc.returncode is not None else -1
    return CommandResult(
        command=cmd,
        exit_code=exit_code,
        stdout=stdout,
        stderr=stderr,
        duration_seconds=elapsed,
        timed_out=timed_out,
    )


async def run_klippbok(
    module: str,
    subcommand: str,
    args: Iterable[str] = (),
    *,
    timeout: float = _DEFAULT_TIMEOUT_SECONDS,
    extra_env: Optional[dict[str, str]] = None,
) -> CommandResult:
    """Run ``python -m <module> <subcommand> <args>`` asynchronously.

    ``module`` is ``"klippbok.video"`` or ``"klippbok.dataset"``.
    ``subcommand`` is the Klippbok verb (``scan``, ``triage``, …).
    ``args`` is the list of positional + flag args assembled by the caller
    — they are passed through verbatim as a list (never shell-joined), so
    spaces in paths are safe.
    """
    cmd = [python_executable(), "-m", module, subcommand, *args]
    return await run_command(
        cmd, timeout=timeout, extra_env=extra_env
    )
