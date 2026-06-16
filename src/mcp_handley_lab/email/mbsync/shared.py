"""Core mbsync (isync) email sync functions for direct Python use.

Identical interface to the MCP ``sync`` tool, usable without the MCP server.
mbsync replaces offlineimap as the synchronization backend: it is faster,
lighter and tracks state more reliably. Note that, unlike offlineimap, mbsync
selects work by *channel* (not account/folder flags), has no ``--dry-run``
(``-l`` lists mailboxes instead), and returns a non-zero exit code on benign
per-folder warnings -- so this module runs it tolerantly via subprocess rather
than the strict shared ``run_command`` helper.
"""

import subprocess
from pathlib import Path
from typing import Literal

from mcp_handley_lab.shared.models import OperationResult


def _run(cmd: list[str], timeout: int) -> tuple[str, int]:
    """Run a command, tolerating a non-zero exit (mbsync warns per-folder).

    Returns the combined stdout+stderr text and the return code. Raises only on
    the command being missing or timing out, matching ``run_command`` for those.
    """
    result = subprocess.run(  # noqa: S603
        cmd,
        capture_output=True,
        timeout=timeout,
        check=False,
    )
    out = (
        result.stdout.decode(errors="replace") + result.stderr.decode(errors="replace")
    ).strip()
    return out, result.returncode


def _target_args(account: str, folders: str) -> list[str]:
    """Map account/folders onto mbsync's channel selection.

    - account + folders -> ``["channel:box1,box2"]`` (mbsync channel:box syntax)
    - account only       -> ``["channel"]``
    - neither            -> ``["-a"]`` (all channels)

    mbsync has no cross-account folder selection, so ``folders`` only takes
    effect together with an ``account``.
    """
    if account and folders:
        return [f"{account}:{folders}"]
    if account:
        return [account]
    return ["-a"]


def _channels(config_file: str) -> list[str]:
    """Parse configured channel names from the mbsync config (no network)."""
    path = Path(config_file) if config_file else Path.home() / ".mbsyncrc"
    names: list[str] = []
    if path.is_file():
        for line in path.read_text().splitlines():
            stripped = line.strip()
            if stripped.startswith("Channel") and stripped[7:8].isspace():
                names.append(stripped.split(None, 1)[1].strip())
    return names


def sync(
    mode: Literal["full", "quick", "preview", "status", "info"] = "full",
    account: str = "",
    folders: str = "",
    config_file: str = "",
    timeout_seconds: int = 0,
) -> OperationResult:
    """Unified email synchronization with multiple modes, backed by mbsync.

    Args:
        mode: 'full'/'quick' (incremental sync -- mbsync is always incremental),
            'preview'/'status' (list mailboxes without transferring messages;
            mbsync has no dry-run), 'info' (list configured channels).
        account: Optional mbsync channel to sync. If omitted, all channels (-a).
        folders: Comma-separated mailbox names; only applied together with
            ``account`` (mbsync ``channel:box1,box2`` syntax).
        config_file: Optional path to the mbsync config. Defaults to ~/.mbsyncrc.
        timeout_seconds: Timeout in seconds (0 uses mode defaults:
            full=300, quick/preview=180, status=60, info=120).

    Returns:
        OperationResult with sync status and details.
    """
    config_args = ["-c", config_file] if config_file else []

    if mode == "info":
        channels = _channels(config_file)
        path = config_file or str(Path.home() / ".mbsyncrc")
        listed = "\n".join(channels) if channels else "(no channels found)"
        return OperationResult(
            status="success",
            message=f"Configured mbsync channels:\n{listed}\n\nConfig: {path}",
            # 'accounts' kept for compatibility with the previous offlineimap tool
            data={"channels": channels, "accounts": channels, "config": path},
        )

    if mode in ("status", "preview"):
        # mbsync has no dry-run; '-l' lists mailboxes without moving messages.
        timeout = timeout_seconds or (60 if mode == "status" else 180)
        cmd = ["mbsync", *config_args, "-l", *_target_args(account, folders)]
        output, code = _run(cmd, timeout)
        label = (
            "Configuration valid" if mode == "status" else "Preview (mailbox listing)"
        )
        return OperationResult(
            status="success",
            message=f"{label}:\n{output}",
            data={
                "raw": output,
                "returncode": code,
                "valid": code == 0,
                "mode": mode,
                "dry_run": True,
            },
        )

    # full / quick -> incremental sync (mbsync is always incremental).
    timeout = timeout_seconds or (300 if mode == "full" else 180)
    cmd = ["mbsync", *config_args, *_target_args(account, folders)]
    output, code = _run(cmd, timeout)

    # Index new messages after an actual sync (a non-zero mbsync exit is a
    # per-folder warning, not a fatal error, so always reindex).
    notmuch_output, _ = _run(["notmuch", "new"], timeout=120)

    mode_desc = {"full": "Full", "quick": "Quick"}
    message = f"{mode_desc.get(mode, mode)} sync completed:\n{output}"
    if notmuch_output:
        message += f"\n\nIndexing:\n{notmuch_output}"

    return OperationResult(
        status="success",
        message=message,
        data={
            "raw": output,
            "returncode": code,
            "mode": mode,
            "indexed": notmuch_output,
        },
    )
