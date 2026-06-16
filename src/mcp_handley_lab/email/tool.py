"""Unified email client MCP tool integrating all email providers."""

# Import the shared mcp instance (re-exported for tests)
from mcp_handley_lab.email.common import mcp  # noqa: F401

# Import tool modules to register their @mcp.tool decorators.
# The `sync` tool is provided by mbsync (isync); the legacy offlineimap
# provider remains in the package but is no longer registered.
from mcp_handley_lab.email.mbsync import tool as _mbsync  # noqa: F401
from mcp_handley_lab.email.msmtp import tool as _msmtp  # noqa: F401
from mcp_handley_lab.email.mutt import tool as _mutt  # noqa: F401
from mcp_handley_lab.email.notmuch import tool as _notmuch  # noqa: F401
