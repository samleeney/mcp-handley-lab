"""Terminal utilities for launching interactive applications."""

import os
import subprocess

TMUX_POPUP_WIDTH = "90%"
TMUX_POPUP_HEIGHT = "90%"


def _tmux_client_available() -> bool:
    """Return True when a tmux client can receive interactive UI."""
    try:
        subprocess.run(
            ["tmux", "display-message", "-p", "#{client_tty}"],
            capture_output=True,
            check=True,
            text=True,
        )
        return True
    except (FileNotFoundError, subprocess.CalledProcessError):
        return False


def _tmux_popup_command(command: str, window_title: str | None = None) -> list[str]:
    """Build a tmux popup command for an interactive process."""
    tmux_cmd = [
        "tmux",
        "display-popup",
        "-w",
        TMUX_POPUP_WIDTH,
        "-h",
        TMUX_POPUP_HEIGHT,
    ]

    if window_title:
        tmux_cmd.extend(["-T", window_title])

    tmux_cmd.extend(["-E", command])
    return tmux_cmd


def launch_interactive(
    command: str,
    window_title: str | None = None,
    prefer_tmux: bool = True,
    wait: bool = False,
) -> str | tuple[str, int]:
    """Launch an interactive command in a tmux popup.

    The launcher deliberately avoids external terminal fallbacks so interactive
    approval flows stay inside the current tmux session.

    Args:
        command: The command to execute
        window_title: Optional title for the window
        prefer_tmux: Retained for API compatibility; must be True
        wait: Whether to wait for the command to complete before returning

    Returns:
        If wait=True: tuple of (status_message, exit_code)
        If wait=False: status message string describing what was launched

    Raises:
        RuntimeError: If no tmux client is available
    """
    if not prefer_tmux:
        raise RuntimeError(
            "External terminal launch is disabled; interactive commands require "
            "a reachable tmux client."
        )

    if not _tmux_client_available():
        raise RuntimeError(
            "Interactive terminal launch requires a reachable tmux client."
        )

    tmux_cmd = _tmux_popup_command(command, window_title)
    if wait:
        print(f"Waiting for user input from {window_title or 'tmux popup'}...")
        subprocess.run(tmux_cmd, check=True)
        return f"Completed in tmux popup: {command}", 0
    else:
        subprocess.Popen(tmux_cmd)
        return f"Launched in tmux popup: {command}"


def check_interactive_support() -> dict:
    """Check what interactive terminal options are available.

    Returns:
        Dict with availability status of tmux popup support
    """
    result = {
        "tmux_session": bool(os.environ.get("TMUX")),
        "tmux_available": False,
        "tmux_popup_available": False,
        "tmux_error": None,
    }

    try:
        subprocess.run(["tmux", "list-sessions"], capture_output=True, check=True)
        result["tmux_available"] = True
    except FileNotFoundError:
        pass
    except subprocess.CalledProcessError as e:
        result["tmux_error"] = str(e)

    result["tmux_popup_available"] = _tmux_client_available()

    return result
