"""Tests for interactive terminal launcher selection."""

import subprocess

import pytest

from mcp_handley_lab.common.terminal import launch_interactive


def test_launch_interactive_uses_tmux_popup_without_tmux_env(monkeypatch):
    """A background MCP process may not inherit TMUX but can still target a client."""
    run_calls = []

    def fake_run(cmd, **kwargs):
        run_calls.append((cmd, kwargs))
        if cmd[:3] == ["tmux", "display-message", "-p"]:
            return subprocess.CompletedProcess(cmd, 0, stdout="/dev/pts/2\n")
        if cmd[:2] == ["tmux", "display-popup"]:
            return subprocess.CompletedProcess(cmd, 0)
        raise AssertionError(f"unexpected command: {cmd}")

    monkeypatch.delenv("TMUX", raising=False)
    monkeypatch.setattr(subprocess, "run", fake_run)

    status, exit_code = launch_interactive(
        "mutt -s Test user@example.com",
        window_title="Mutt",
        wait=True,
    )

    assert status == "Completed in tmux popup: mutt -s Test user@example.com"
    assert exit_code == 0
    assert run_calls[1][0] == [
        "tmux",
        "display-popup",
        "-w",
        "90%",
        "-h",
        "90%",
        "-T",
        "Mutt",
        "-E",
        "mutt -s Test user@example.com",
    ]


def test_launch_interactive_requires_tmux_client(monkeypatch):
    """Do not fall back to an external terminal when tmux is unavailable."""

    def fake_run(cmd, **kwargs):
        if cmd[:3] == ["tmux", "display-message", "-p"]:
            raise subprocess.CalledProcessError(1, cmd)
        raise AssertionError(f"unexpected command: {cmd}")

    def fake_popen(cmd, **kwargs):
        raise AssertionError(f"unexpected external launch: {cmd}")

    monkeypatch.setattr(subprocess, "run", fake_run)
    monkeypatch.setattr(subprocess, "Popen", fake_popen)

    with pytest.raises(RuntimeError, match="requires a reachable tmux client"):
        launch_interactive("mutt -s Test user@example.com", wait=True)


def test_launch_interactive_rejects_external_terminal_preference():
    """The legacy external-terminal path is intentionally disabled."""
    with pytest.raises(RuntimeError, match="External terminal launch is disabled"):
        launch_interactive(
            "mutt -s Test user@example.com",
            prefer_tmux=False,
            wait=True,
        )
