"""Capture and msmtp log utilities for email send verification.

Extracted from tool.py for reuse by direct.py (programmatic send) without
circular imports.
"""

import builtins
import os
import re
import time
from email import policy
from email.parser import BytesParser, HeaderParser
from email.utils import getaddresses
from pathlib import Path

# Capture directory for msmtp wrapper
CAPTURE_DIR = (
    Path(os.environ.get("XDG_STATE_HOME", os.path.expanduser("~/.local/state")))
    / "mcp-email"
    / "captured"
)
# Per plan: retain captured files for 5 min on failure for debugging,
# delete immediately on successful parsing
CAPTURE_MAX_AGE_SECONDS = 300  # 5 minutes (cleanup old/orphaned files)
CAPTURE_RETRY_SECONDS = 5  # Wait up to 5s for captured file
CAPTURE_RETRY_INTERVAL = 0.2  # Check every 200ms

# Warnings shown only when capture fails (keyed by status or reason)
CAPTURE_WARNINGS = {
    "not_configured": """WARNING: Capture not configured. The body shown is the DRAFT, not what was actually sent.
To capture actual sent content, configure mutt: set sendmail = "mcp-msmtp-capture -a <account>"
The mcp-msmtp-capture command is installed with this package.""",
    "ambiguous": "WARNING: Multiple captured messages match; cannot determine which was sent. Body shown is DRAFT.",
    "parse_error": "WARNING: Captured message found but failed to parse. Body shown is DRAFT.",
    "timeout": "WARNING: No matching captured message found. Body shown is DRAFT.",
}
CAPTURE_WARNING_DEFAULT = (
    "WARNING: Could not capture sent content. Body shown is DRAFT."
)


def _reject_header_injection(value: str, field_name: str) -> None:
    """Reject CR/LF in header field values to prevent header injection.

    Raises:
        ValueError: If value contains CR or LF characters.
    """
    if value and ("\r" in value or "\n" in value):
        raise ValueError(
            f"Invalid characters in {field_name}: header values must not contain CR or LF"
        )


def _build_smtp_dict(data: dict) -> dict:
    """Build normalized smtp structure from msmtp log data."""
    return {
        "message_id": data.get("message_id", ""),
        "mail_size_bytes": data.get("mail_size_bytes", 0),
        "status_code": data.get("smtp_status_code", ""),
    }


def _cleanup_old_captures() -> None:
    """Delete captured email files older than CAPTURE_MAX_AGE_SECONDS."""
    if not CAPTURE_DIR.exists():
        return
    now = time.time()
    for eml_file in CAPTURE_DIR.glob("*.eml"):
        try:
            if now - eml_file.stat().st_mtime > CAPTURE_MAX_AGE_SECONDS:
                eml_file.unlink()
        except OSError:
            pass  # File may have been deleted already


def _extract_addr_specs(header_value: str) -> list[str]:
    """Extract normalized email addresses from an RFC822 header value.

    Uses email.utils.getaddresses() for proper RFC822 parsing (handles
    quoted names, groups, encoded words). Returns lowercase addr-specs only.
    """
    if not header_value:
        return []
    parsed = getaddresses([header_value])
    return [addr.lower() for _name, addr in parsed if addr]


def _scan_captured_headers(path: Path) -> dict:
    """Scan only headers of a captured .eml file for matching purposes.

    Fast, lightweight scan that reads only headers (stops at blank line).
    Uses HeaderParser which doesn't parse body/attachments.
    Returns dict with: correlation_id, subject, from, to, cc, file_size
    """
    # Read only header portion (up to first blank line)
    header_bytes = []
    with builtins.open(path, "rb") as f:
        for line in f:
            if line in (b"\r\n", b"\n"):
                break
            header_bytes.append(line)
    headers_text = b"".join(header_bytes).decode("utf-8", errors="replace")

    # Parse headers only (no body processing)
    msg = HeaderParser(policy=policy.default).parsestr(headers_text)

    return {
        "correlation_id": msg.get("X-MCP-Correlation-Id", ""),
        "subject": msg.get("Subject", ""),
        "from": _extract_addr_specs(msg.get("From", "")),
        "to": _extract_addr_specs(msg.get("To", "")),
        "cc": _extract_addr_specs(msg.get("Cc", "")),
        "file_size": path.stat().st_size,
    }


def _parse_captured_email(path: Path) -> dict:
    """Parse a captured .eml file and extract relevant fields.

    Full parse including body and attachments. Only call for the selected file.
    Returns dict with: subject, to, cc, from, body_text, attachments
    """
    with builtins.open(path, "rb") as f:
        msg = BytesParser(policy=policy.default).parse(f)

    result = {
        "subject": msg.get("Subject", ""),
        "to": _extract_addr_specs(msg.get("To", "")),
        "cc": _extract_addr_specs(msg.get("Cc", "")),
        "from": _extract_addr_specs(msg.get("From", "")),
        "correlation_id": msg.get("X-MCP-Correlation-Id", ""),
        "body_text": "",
        "attachments": [],
    }

    # Extract body and attachments
    if msg.is_multipart():
        for part in msg.walk():
            content_type = part.get_content_type()
            content_disposition = part.get("Content-Disposition", "")

            if "attachment" in content_disposition:
                # Attachment - extract metadata only
                filename = part.get_filename() or "unnamed"
                try:
                    payload = part.get_payload(decode=True)
                    size = len(payload) if payload else 0
                except Exception:
                    size = 0
                result["attachments"].append(
                    {
                        "filename": filename,
                        "content_type": content_type,
                        "size_bytes": size,
                    }
                )
            elif content_type == "text/plain" and not result["body_text"]:
                # First text/plain part is the body
                payload = part.get_payload(decode=True)
                if payload:
                    charset = part.get_content_charset() or "utf-8"
                    try:
                        result["body_text"] = payload.decode(charset)
                    except (UnicodeDecodeError, LookupError):
                        result["body_text"] = payload.decode("utf-8", errors="replace")
    else:
        # Simple message
        payload = msg.get_payload(decode=True)
        if payload:
            charset = msg.get_content_charset() or "utf-8"
            try:
                result["body_text"] = payload.decode(charset)
            except (UnicodeDecodeError, LookupError):
                result["body_text"] = payload.decode("utf-8", errors="replace")

    return result


def _find_captured_email(
    correlation_id: str,
    subject: str,
    draft_recipients: list[str],
    from_addr: str | None = None,
    mail_size_bytes: int | None = None,
    envelope_recipients: list[str] | None = None,
) -> tuple[Path | None, str, str]:
    """Find a captured email file by correlation ID or fallback matching.

    Primary match: X-MCP-Correlation-Id header
    Fallback: subject + recipients + from + approximate size (within 20%)

    Args:
        correlation_id: UUID for primary matching
        subject: Email subject for fallback
        draft_recipients: To+Cc from draft (used if envelope_recipients unavailable)
        from_addr: From address from msmtp log
        mail_size_bytes: Message size from msmtp log
        envelope_recipients: All recipients from msmtp log (To+Cc+Bcc)

    Returns: (path or None, status, reason)
    - status is one of: captured, not_configured, not_found
    - reason provides detail for not_found cases (ambiguous, timeout, etc.)
    """
    if not CAPTURE_DIR.exists():
        return None, "not_configured", ""

    # Clean up old captures first
    _cleanup_old_captures()

    # Use msmtp envelope recipients if available and non-empty, else fall back to draft To/Cc
    # Normalize to lowercase addr-specs
    expected_recipients: set[str] = set()
    if envelope_recipients:
        expected_recipients = {r.lower() for r in envelope_recipients if r}
    if not expected_recipients:
        expected_recipients = {r.lower() for r in draft_recipients if r}

    # Retry loop to handle filesystem sync delays
    deadline = time.time() + CAPTURE_RETRY_SECONDS

    while time.time() < deadline:
        candidates = []
        now = time.time()

        for eml_file in CAPTURE_DIR.glob("*.eml"):
            try:
                mtime = eml_file.stat().st_mtime
                # Only consider files from last 60 seconds
                if now - mtime > 60:
                    continue

                # Use lightweight header scan (no body/attachment parsing)
                headers = _scan_captured_headers(eml_file)

                # Primary match: correlation ID
                if correlation_id and headers.get("correlation_id") == correlation_id:
                    return eml_file, "captured", ""

                # Fallback match: subject + recipients + from + size
                parsed_recipients = set(headers.get("to", []) + headers.get("cc", []))

                subject_match = headers.get("subject", "") == subject

                # Recipient matching: captured To+Cc should be subset of envelope recipients
                # (Bcc won't appear in captured headers but is in envelope_recipients)
                recipients_match = parsed_recipients <= expected_recipients

                # From match (if provided)
                from_match = True
                if from_addr:
                    parsed_from = headers.get("from", [])
                    from_match = from_addr.lower() in parsed_from

                # Size match (within 20% tolerance, if provided)
                size_match = True
                if mail_size_bytes and mail_size_bytes > 0:
                    file_size = headers.get("file_size", 0)
                    tolerance = mail_size_bytes * 0.2
                    size_match = abs(file_size - mail_size_bytes) <= tolerance

                if subject_match and recipients_match and from_match and size_match:
                    candidates.append(eml_file)

            except (OSError, ValueError):
                continue  # Skip unreadable files

        # If we have exactly one fallback match, use it
        if len(candidates) == 1:
            return candidates[0], "captured", ""

        # Multiple matches = ambiguous (per plan: return not_found with note)
        if len(candidates) > 1:
            return None, "not_found", "ambiguous"

        # No matches yet, wait and retry
        time.sleep(CAPTURE_RETRY_INTERVAL)

    # Timeout reached
    return None, "not_found", "timeout"


def _get_msmtp_log_size() -> int:
    """Get current size of msmtp log file."""
    log_path = os.path.expanduser("~/.msmtp.log")
    try:
        return os.path.getsize(log_path)
    except FileNotFoundError:
        return 0  # No log file yet - first email


def _parse_msmtp_log_entry(log_line: str) -> dict:
    """Parse an msmtp log entry to extract detailed information.

    Example log line:
    Aug 23 09:16:33 host=smtp.office365.com tls=on auth=on user=wh260@cam.ac.uk
    from=wh260@cam.ac.uk recipients=wh260@cam.ac.uk,cc@example.com,bcc@example.com
    mailsize=273 smtpstatus=250 smtpmsg='250 2.0.0 OK <aKj2OhY87X3qWDJs@maxwell> [Hostname=...]'
    exitcode=EX_OK
    """
    data = {}

    # Extract timestamp (first 15 chars typically)
    if len(log_line) >= 15:
        data["timestamp"] = log_line[:15].strip()

    # Extract recipients (can be comma-separated)
    recipients_match = re.search(r"recipients=([^\s]+)", log_line)
    if recipients_match:
        recipients_str = recipients_match.group(1)
        data["all_recipients"] = recipients_str.split(",")

    # Extract from address
    from_match = re.search(r"from=([^\s]+)", log_line)
    if from_match:
        data["from"] = from_match.group(1)

    # Extract mail size
    size_match = re.search(r"mailsize=(\d+)", log_line)
    if size_match:
        data["mail_size_bytes"] = int(size_match.group(1))

    # Extract SMTP status code
    status_match = re.search(r"smtpstatus=(\d+)", log_line)
    if status_match:
        data["smtp_status_code"] = status_match.group(1)

    # Extract SMTP message (including message ID)
    msg_match = re.search(r"smtpmsg='([^']+)'", log_line)
    if msg_match:
        smtp_msg = msg_match.group(1)
        data["smtp_message"] = smtp_msg

        # Try to extract message ID from SMTP response
        msg_id_match = re.search(r"<([^>]+)>", smtp_msg)
        if msg_id_match:
            data["message_id"] = msg_id_match.group(1)

    # Extract error message if present
    error_match = re.search(r"errormsg='([^']+)'", log_line)
    if error_match:
        data["error_message"] = error_match.group(1)

    # Extract exit code
    exit_match = re.search(r"exitcode=(\w+)", log_line)
    if exit_match:
        data["exit_code"] = exit_match.group(1)

    # Extract host
    host_match = re.search(r"host=([^\s]+)", log_line)
    if host_match:
        data["smtp_host"] = host_match.group(1)

    return data


def _check_recent_send(start_offset: int, end_offset: int) -> tuple[bool, bool, dict]:
    """Check if a recent send occurred and extract detailed information.

    Reads only the byte range [start_offset, end_offset) from the msmtp log,
    bounding the read to bytes appended during this session's mutt invocation.

    Returns:
        (send_occurred, send_successful, data_dict)
    """
    log_path = os.path.expanduser("~/.msmtp.log")
    try:
        with builtins.open(log_path, "rb") as f:
            f.seek(start_offset)
            chunk = f.read(end_offset - start_offset)
    except FileNotFoundError:
        return False, False, {}
    lines = chunk.decode(errors="replace").splitlines()

    # Find last line with exitcode= (our send attempt), scanning in reverse
    for line in reversed(lines):
        line = line.strip()
        if line and "exitcode=" in line:
            data = _parse_msmtp_log_entry(line)
            send_successful = "exitcode=EX_OK" in line
            return True, send_successful, data

    return False, False, {}
