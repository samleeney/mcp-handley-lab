"""mbsync (isync) email synchronization provider."""

from typing import Literal

from pydantic import Field

from mcp_handley_lab.email.common import mcp
from mcp_handley_lab.shared.models import OperationResult


@mcp.tool(
    description="""Sync emails from the server (via mbsync/isync) before using read/send. Modes: 'full'/'quick' (incremental sync -- mbsync is always incremental), 'preview'/'status' (list mailboxes without transferring; mbsync has no dry-run), 'info' (list configured channels). Use 'account' (an mbsync channel) and optionally 'folders' to limit scope."""
)
def sync(
    mode: Literal["full", "quick", "preview", "status", "info"] = Field(
        default="full",
        description="Sync mode: 'full'/'quick' (incremental sync), 'preview'/'status' (list mailboxes, no transfer), 'info' (list channels).",
    ),
    account: str = Field(
        default="",
        description="Optional mbsync channel name to sync. If omitted, all channels are synced (-a).",
    ),
    folders: str = Field(
        default="",
        description="Comma-separated mailbox names to sync (e.g., 'INBOX,Sent'). Only applied together with 'account' (mbsync channel:box syntax).",
    ),
    config_file: str = Field(
        default="",
        description="Optional path to the mbsync config. Defaults to ~/.mbsyncrc.",
    ),
    timeout_seconds: int = Field(
        default=0,
        description="Timeout in seconds (0 uses mode defaults: full=300, quick/preview=180, status=60, info=120).",
        ge=0,
    ),
) -> OperationResult:
    """Unified email synchronization with multiple modes, backed by mbsync."""
    from mcp_handley_lab.email.mbsync.shared import sync as _sync

    return _sync(
        mode=mode,
        account=account,
        folders=folders,
        config_file=config_file,
        timeout_seconds=timeout_seconds,
    )
