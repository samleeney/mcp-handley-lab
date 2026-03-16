"""Shared MCP instance for unified email tool with module-level description injection."""

import logging
import re
from collections.abc import Callable
from pathlib import Path
from typing import Any, TypedDict

from mcp.server.fastmcp import FastMCP

logger = logging.getLogger(__name__)


class ToolConfig(TypedDict):
    fn: Callable[..., Any]
    description: str


# Tool configs for module-level description injection.
# Populated by provider modules (notmuch, mutt, offlineimap) before mcp.run().
_TOOL_CONFIGS: dict[str, ToolConfig] = {}


def _msmtprc_path(config_file: str = "") -> Path | None:
    """Resolve msmtp config path: explicit override, XDG, or legacy."""
    if config_file:
        p = Path(config_file)
        return p if p.exists() else None
    for p in [Path.home() / ".config" / "msmtp" / "config", Path.home() / ".msmtprc"]:
        if p.exists():
            return p
    return None


def _list_accounts(config_file: str = "") -> list[str]:
    """List msmtp accounts that have an explicit 'from' address.

    Only includes accounts with a directly-set 'from' field (no inheritance).
    Returns empty list if msmtprc doesn't exist (msmtp is optional).
    """
    path = _msmtprc_path(config_file)
    if not path:
        return []
    accounts = []
    current = None
    has_from = False
    try:
        for line in path.read_text().splitlines():
            stripped = line.split("#", 1)[0].strip()
            if not stripped:
                continue
            if stripped.startswith("account ") and not stripped.startswith(
                "account default"
            ):
                if current and has_from:
                    accounts.append(current)
                current = stripped.split()[1]
                has_from = False
            elif stripped.startswith("from ") and current:
                has_from = True
        if current and has_from:
            accounts.append(current)
    except FileNotFoundError:
        pass
    return accounts


def _get_account_from_addr(account: str = "", config_file: str = "") -> str:
    """Get From address for an msmtp account. Resolves default account alias.

    Does not follow account inheritance (account X : Y) — only reads
    'from' directly set in the named account block.
    """
    path = _msmtprc_path(config_file)
    if not path:
        return ""
    text = path.read_text()
    if not account:
        m = re.search(r"^account\s+default\s*:\s*(\S+)", text, re.MULTILINE)
        if m:
            account = m.group(1)
        else:
            return ""
    current = None
    for line in text.splitlines():
        stripped = line.split("#", 1)[0].strip()
        if not stripped:
            continue
        if stripped.startswith("account ") and not stripped.startswith(
            "account default"
        ):
            current = stripped.split()[1]
        elif stripped.startswith("from ") and current == account:
            return stripped.split(None, 1)[1].strip()
    return ""


def _get_account_config(account: str = "", config_file: str = "") -> dict[str, str]:
    """Get msmtp account configuration (account name and from address).

    Parses ~/.msmtprc for account blocks and their 'from' field.
    Handles 'account default : <name>' alias resolution.

    Args:
        account: Account name. If empty, uses default account (or first if no default).
        config_file: Override config path (for testing).

    Returns:
        {"account": resolved_name, "from": from_address}

    Raises:
        ValueError: If config file missing, account not found, or no 'from' field.
    """
    msmtprc_path = Path(config_file) if config_file else Path.home() / ".msmtprc"

    if not msmtprc_path.exists():
        raise ValueError(
            f"msmtp config not found at {msmtprc_path}. "
            "Configure msmtp to use programmatic email sending."
        )

    # Parse accounts: {name: {"from": addr, ...}}
    accounts: dict[str, dict[str, str]] = {}
    default_alias = ""
    current_account = ""
    first_account = ""

    with open(msmtprc_path) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue

            if line.startswith("account default :"):
                # Alias: account default : <name>
                default_alias = line.split(":", 1)[1].strip()
            elif line.startswith("account "):
                current_account = line.split()[1].rstrip(":")
                if not first_account:
                    first_account = current_account
                accounts.setdefault(current_account, {})
            elif current_account and " " in line:
                key, _, value = line.partition(" ")
                accounts[current_account][key.strip()] = value.strip()

    if not accounts:
        raise ValueError(f"No accounts found in {msmtprc_path}")

    # Resolve which account to use
    if account:
        if account not in accounts:
            available = ", ".join(accounts.keys())
            raise ValueError(
                f"Account '{account}' not found in {msmtprc_path}. "
                f"Available accounts: {available}"
            )
        resolved = account
    elif default_alias:
        if default_alias not in accounts:
            available = ", ".join(accounts.keys())
            raise ValueError(
                f"Default alias '{default_alias}' not found. "
                f"Available accounts: {available}"
            )
        resolved = default_alias
    else:
        resolved = first_account

    from_addr = accounts[resolved].get("from", "")
    if not from_addr:
        raise ValueError(
            f"Account '{resolved}' has no 'from' field configured in {msmtprc_path}"
        )

    return {"account": resolved, "from": from_addr}


# Single, shared MCP instance for the entire email tool.
# All provider modules will import and use this instance.
mcp = FastMCP("Email")
