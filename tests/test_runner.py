"""Tests for the async subprocess wrapper."""
from __future__ import annotations

import asyncio
import os
import sys

import pytest

from klippbok_mcp import runner


def test_python_executable_honors_env(monkeypatch):
    monkeypatch.setenv("KLIPPBOK_PYTHON", "/fake/path/to/python")
    assert runner.python_executable() == "/fake/path/to/python"


def test_python_executable_falls_back_to_sys_executable(monkeypatch):
    monkeypatch.delenv("KLIPPBOK_PYTHON", raising=False)
    assert runner.python_executable() == sys.executable


def test_build_env_adds_utf8_flags():
    env = runner.build_env()
    assert env["PYTHONUTF8"] == "1"
    assert env["PYTHONIOENCODING"] == "utf-8"
    assert env["PYTHONUNBUFFERED"] == "1"
    # Should inherit host env
    assert "PATH" in env


def test_build_env_merges_extra_and_skips_empty():
    env = runner.build_env({"FOO": "bar", "EMPTY": ""})
    assert env["FOO"] == "bar"
    # Empty values are skipped so an un-set key doesn't clobber inherited ones.
    assert env.get("EMPTY") != ""


def test_command_result_succeeded_property():
    ok = runner.CommandResult(
        command=["x"], exit_code=0, stdout="", stderr="", duration_seconds=1.0
    )
    assert ok.succeeded is True
    fail = runner.CommandResult(
        command=["x"], exit_code=1, stdout="", stderr="boom", duration_seconds=0.1
    )
    assert fail.succeeded is False
    timed = runner.CommandResult(
        command=["x"], exit_code=0, stdout="", stderr="", duration_seconds=1.0, timed_out=True
    )
    # Exit code 0 but timed_out => not succeeded.
    assert timed.succeeded is False


def test_command_result_short_summary():
    r = runner.CommandResult(
        command=["x"], exit_code=0, stdout="", stderr="", duration_seconds=1.5
    )
    assert "ok" in r.short_summary()
    assert "1.5" in r.short_summary()


def test_command_result_to_dict_round_trip():
    r = runner.CommandResult(
        command=["a", "b"], exit_code=42, stdout="out", stderr="err",
        duration_seconds=3.14, timed_out=False,
    )
    d = r.to_dict()
    assert d["command"] == ["a", "b"]
    assert d["exit_code"] == 42
    assert d["stdout"] == "out"
    assert d["timed_out"] is False


@pytest.mark.asyncio
async def test_run_command_success():
    # Use the Python we're running under — guaranteed present.
    r = await runner.run_command(
        [sys.executable, "-c", "print('hello')"], timeout=10.0
    )
    assert r.exit_code == 0
    assert "hello" in r.stdout
    assert r.timed_out is False


@pytest.mark.asyncio
async def test_run_command_non_zero_exit_is_data_not_exception():
    r = await runner.run_command(
        [sys.executable, "-c", "import sys; sys.exit(7)"], timeout=5.0
    )
    assert r.exit_code == 7
    assert r.succeeded is False


@pytest.mark.asyncio
async def test_run_command_timeout_kills():
    r = await runner.run_command(
        [sys.executable, "-c", "import time; time.sleep(10)"], timeout=0.5
    )
    assert r.timed_out is True
    assert r.succeeded is False


@pytest.mark.asyncio
async def test_run_command_missing_executable_returns_error():
    r = await runner.run_command(["definitely_not_a_real_command_xyz_789"])
    assert r.exit_code == -1
    assert "not found" in r.stderr.lower() or "cannot find" in r.stderr.lower()


@pytest.mark.asyncio
async def test_run_klippbok_assembles_command_correctly(monkeypatch):
    # Verify argument composition without actually running klippbok.
    # We capture what runner.run_command would be called with.
    captured: dict = {}

    async def fake_run_command(cmd, **kwargs):
        captured["cmd"] = cmd
        captured["kwargs"] = kwargs
        return runner.CommandResult(
            command=cmd, exit_code=0, stdout="", stderr="", duration_seconds=0.0
        )

    monkeypatch.setattr(runner, "run_command", fake_run_command)
    await runner.run_klippbok(
        "klippbok.video", "scan", ["/some/dir", "--fps", "16"], timeout=42.0
    )
    assert captured["cmd"][0] == runner.python_executable()
    assert captured["cmd"][1:] == ["-m", "klippbok.video", "scan", "/some/dir", "--fps", "16"]
    assert captured["kwargs"]["timeout"] == 42.0
