"""msmtp wrapper that captures outgoing emails for MCP.

Stores a copy of the email before passing it to msmtp for delivery.
This allows the email MCP tool to report what was actually sent
(after user edits in Mutt) rather than just the draft content.
"""

import os
import secrets
import subprocess
import sys
from datetime import datetime
from pathlib import Path


def main():
    """Tee stdin to a capture file and pipe to msmtp."""
    cap_dir = (
        Path(os.environ.get("XDG_STATE_HOME", os.path.expanduser("~/.local/state")))
        / "mcp-email"
        / "captured"
    )
    cap_dir.mkdir(parents=True, exist_ok=True)
    os.chmod(cap_dir, 0o700)

    timestamp = datetime.now().strftime("%Y%m%dT%H%M%S.%f")
    capture_path = cap_dir / f"{timestamp}.{os.getpid()}.{secrets.token_hex(3)}.eml"

    email_data = sys.stdin.buffer.read()
    capture_path.write_bytes(email_data)
    capture_path.chmod(0o600)

    result = subprocess.run(["msmtp", *sys.argv[1:]], input=email_data, check=False)
    sys.exit(result.returncode)


if __name__ == "__main__":
    main()
