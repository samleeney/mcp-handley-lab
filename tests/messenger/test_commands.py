"""Tests for messenger command parsing and handling."""

import asyncio
from unittest.mock import patch

import pytest

from mcp_handley_lab.messenger.server import (
    ChatActor,
    IncomingEvent,
    _context_footer,
    _dispatch,
    _extract_usage,
    _get_or_create_actor,
    _parse_command,
)

# ---------------------------------------------------------------------------
# _parse_command tests
# ---------------------------------------------------------------------------


class TestParseCommand:
    def test_basic_reset(self):
        assert _parse_command("/reset") == ("/reset", "")

    def test_with_args(self):
        assert _parse_command("/model opus") == ("/model", "opus")

    def test_args_with_at(self):
        assert _parse_command("/model foo@bar") == ("/model", "foo@bar")

    def test_telegram_botname(self):
        assert _parse_command("/reset@MyBot") == ("/reset", "")

    def test_telegram_botname_with_args(self):
        assert _parse_command("/model@MyBot opus") == ("/model", "opus")

    def test_unknown_command(self):
        assert _parse_command("/random") is None

    def test_not_slash(self):
        assert _parse_command("hello") is None

    def test_path_not_command(self):
        assert _parse_command("/home/user/file") is None

    def test_all_commands(self):
        for cmd in (
            "/reset",
            "/cancel",
            "/model",
            "/help",
            "/status",
        ):
            assert _parse_command(cmd) is not None

    def test_whitespace_padding(self):
        assert _parse_command("  /reset  ") == ("/reset", "")

    def test_case_insensitive(self):
        assert _parse_command("/RESET") == ("/reset", "")

    def test_empty_string(self):
        assert _parse_command("") is None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class MockPlatform:
    """Mock platform that records send_text calls."""

    def __init__(self):
        self.sent: list[tuple[str, str]] = []

    def send_text(self, conversation_id, text, reply_to=None):
        self.sent.append((conversation_id, text))
        return "msg-id"

    def send_media(self, conversation_id, path, caption="", reply_to=None):
        return None

    def send_typing(self, conversation_id):
        pass


def _make_event(text: str, platform=None, conversation_id="test:123") -> IncomingEvent:
    plat = platform or MockPlatform()
    parsed = _parse_command(text)
    kind = "command" if parsed is not None else "text"
    return IncomingEvent(
        conversation_id=conversation_id,
        kind=kind,
        text=text,
        platform=plat,
        message_id="ev-1",
    )


def _make_actor(platform=None, conversation_id="test:123", tmp_path=None):
    plat = platform or MockPlatform()
    actor = ChatActor(conversation_id, plat)
    if tmp_path:
        actor.cwd = tmp_path
        actor._state_file = tmp_path / "loop_state.json"
        actor._msg_log_file = tmp_path / "message_log.json"
    return actor


# ---------------------------------------------------------------------------
# ChatActor command tests
# ---------------------------------------------------------------------------


class TestHelpCommand:
    @pytest.mark.asyncio
    async def test_help_sends_text(self, tmp_path):
        platform = MockPlatform()
        actor = _make_actor(platform, tmp_path=tmp_path)
        event = _make_event("/help", platform)
        await actor._handle(event)
        assert len(platform.sent) == 1
        text = platform.sent[0][1]
        assert "/reset" in text
        assert "/help" in text


class TestResetCommand:
    @pytest.mark.asyncio
    async def test_reset_clears_session_preserves_model(self, tmp_path):
        platform = MockPlatform()
        actor = _make_actor(platform, tmp_path=tmp_path)
        actor.loop_id = "claude-123"
        actor.session_id = "sess-abc"
        actor._model = "opus"
        actor._state_file.parent.mkdir(parents=True, exist_ok=True)
        actor._save_state()

        event = _make_event("/reset", platform)
        with patch("mcp_handley_lab.messenger.server.kill") as mock_kill:
            await actor._handle(event)
            mock_kill.assert_called_once_with("claude-123")

        assert actor.loop_id is None
        assert actor.session_id == ""
        assert actor._model == "opus"  # preserved
        assert not actor._stopped  # actor stays running
        assert "reset" in platform.sent[0][1].lower()


class TestInterruptCommands:
    @pytest.mark.asyncio
    @pytest.mark.parametrize("cmd", ["/reset", "/cancel"])
    async def test_dispatch_terminates_running_loop(self, cmd):
        """_dispatch sends terminate before enqueueing /reset or /cancel."""
        import mcp_handley_lab.messenger.server as srv

        old_actors = srv._actors
        old_loop = srv._loop

        try:
            srv._loop = asyncio.new_event_loop()
            srv._actors = {}

            platform = MockPlatform()
            conv_id = "test:interrupt"

            actor = _get_or_create_actor(conv_id, platform)
            actor.loop_id = "claude-stuck"

            event = _make_event(cmd, platform, conversation_id=conv_id)
            with patch("mcp_handley_lab.messenger.server.terminate") as mock_terminate:
                await _dispatch(event)
                mock_terminate.assert_called_once_with("claude-stuck")

            assert not actor.queue.empty()
        finally:
            srv._loop.close()
            srv._actors = old_actors
            srv._loop = old_loop

    @pytest.mark.asyncio
    async def test_dispatch_no_terminate_without_loop(self):
        """_dispatch skips terminate when no active loop."""
        import mcp_handley_lab.messenger.server as srv

        old_actors = srv._actors
        old_loop = srv._loop

        try:
            srv._loop = asyncio.new_event_loop()
            srv._actors = {}

            platform = MockPlatform()
            conv_id = "test:noop"

            event = _make_event("/reset", platform, conversation_id=conv_id)
            with patch("mcp_handley_lab.messenger.server.terminate") as mock_terminate:
                await _dispatch(event)
                mock_terminate.assert_not_called()
        finally:
            srv._loop.close()
            srv._actors = old_actors
            srv._loop = old_loop


class TestCancelCommand:
    @pytest.mark.asyncio
    async def test_cancel_sends_confirmation(self, tmp_path):
        platform = MockPlatform()
        actor = _make_actor(platform, tmp_path=tmp_path)
        actor.loop_id = "claude-123"

        event = _make_event("/cancel", platform)
        await actor._handle(event)

        assert "cancelled" in platform.sent[0][1].lower()
        assert actor.loop_id == "claude-123"  # session preserved
        assert not actor._stopped  # actor still running


class TestModelCommand:
    @pytest.mark.asyncio
    async def test_model_set(self, tmp_path):
        platform = MockPlatform()
        actor = _make_actor(platform, tmp_path=tmp_path)
        actor.cwd.mkdir(parents=True, exist_ok=True)

        event = _make_event("/model opus", platform)
        await actor._handle(event)

        assert actor._model == "opus"
        assert "opus" in platform.sent[0][1].lower()

    @pytest.mark.asyncio
    async def test_model_query(self, tmp_path):
        platform = MockPlatform()
        actor = _make_actor(platform, tmp_path=tmp_path)
        actor._model = "sonnet"

        event = _make_event("/model", platform)
        await actor._handle(event)

        assert "sonnet" in platform.sent[0][1].lower()

    @pytest.mark.asyncio
    async def test_model_query_default(self, tmp_path):
        platform = MockPlatform()
        actor = _make_actor(platform, tmp_path=tmp_path)

        event = _make_event("/model", platform)
        await actor._handle(event)

        assert "default" in platform.sent[0][1].lower()

    @pytest.mark.asyncio
    async def test_model_kills_active_loop(self, tmp_path):
        platform = MockPlatform()
        actor = _make_actor(platform, tmp_path=tmp_path)
        actor.loop_id = "claude-123"
        actor.session_id = "sess-abc"
        actor.cwd.mkdir(parents=True, exist_ok=True)

        event = _make_event("/model opus", platform)
        with patch("mcp_handley_lab.messenger.server.kill") as mock_kill:
            await actor._handle(event)
            mock_kill.assert_called_once_with("claude-123")

        assert actor.loop_id is None
        assert actor._model == "opus"
        assert "restarted" in platform.sent[0][1].lower()


class TestStatusCommand:
    @pytest.mark.asyncio
    async def test_status_active(self, tmp_path):
        platform = MockPlatform()
        actor = _make_actor(platform, tmp_path=tmp_path)
        actor.loop_id = "claude-123"
        actor.session_id = "sess-abc"

        with patch(
            "mcp_handley_lab.messenger.server.loop_status",
            return_value={"ok": True, "running": True, "elapsed_seconds": 42.0},
        ):
            event = _make_event("/status", platform)
            await actor._handle(event)

        text = platform.sent[0][1]
        assert "running" in text.lower()
        assert "42s" in text

    @pytest.mark.asyncio
    async def test_status_no_session(self, tmp_path):
        platform = MockPlatform()
        actor = _make_actor(platform, tmp_path=tmp_path)

        event = _make_event("/status", platform)
        await actor._handle(event)

        assert "no active session" in platform.sent[0][1].lower()

    @pytest.mark.asyncio
    async def test_status_clears_stale_loop(self, tmp_path):
        platform = MockPlatform()
        actor = _make_actor(platform, tmp_path=tmp_path)
        actor.loop_id = "claude-dead"
        actor.cwd.mkdir(parents=True, exist_ok=True)

        with patch(
            "mcp_handley_lab.messenger.server.loop_status",
            side_effect=RuntimeError("not_found: loop not found"),
        ):
            event = _make_event("/status", platform)
            await actor._handle(event)

        assert actor.loop_id is None
        assert "expired" in platform.sent[0][1].lower()


class TestActorLifecycle:
    def test_stopped_actor_replaced(self, tmp_path):
        """_get_or_create_actor replaces a stopped actor."""
        import mcp_handley_lab.messenger.server as srv

        old_actors = srv._actors
        old_loop = srv._loop

        try:
            srv._loop = asyncio.new_event_loop()
            srv._actors = {}

            platform = MockPlatform()
            conv_id = "test:lifecycle"

            # Create initial actor
            actor1 = _get_or_create_actor(conv_id, platform)
            assert conv_id in srv._actors

            # Mark it as stopped
            actor1._stopped = True

            # Should create a new actor
            actor2 = _get_or_create_actor(conv_id, platform)
            assert actor2 is not actor1
            assert not actor2._stopped
        finally:
            srv._loop.close()
            srv._actors = old_actors
            srv._loop = old_loop


class TestQueryThreadsModel:
    @pytest.mark.asyncio
    async def test_query_passes_model_in_args(self, tmp_path):
        platform = MockPlatform()
        actor = _make_actor(platform, tmp_path=tmp_path)
        actor._model = "opus"
        actor.cwd.mkdir(parents=True, exist_ok=True)

        with (
            patch(
                "mcp_handley_lab.messenger.server.spawn",
                return_value="claude-test",
            ) as mock_spawn,
            patch(
                "mcp_handley_lab.messenger.server.run",
                return_value="response",
            ),
        ):
            result = actor._query("hello")

        assert result == "response"
        args_str = mock_spawn.call_args[1]["args"]
        assert "--model opus" in args_str

    @pytest.mark.asyncio
    async def test_query_no_model_default(self, tmp_path):
        platform = MockPlatform()
        actor = _make_actor(platform, tmp_path=tmp_path)
        actor.cwd.mkdir(parents=True, exist_ok=True)

        with (
            patch(
                "mcp_handley_lab.messenger.server.spawn",
                return_value="claude-test",
            ) as mock_spawn,
            patch(
                "mcp_handley_lab.messenger.server.run",
                return_value="response",
            ),
        ):
            actor._query("hello")

        args_str = mock_spawn.call_args[1]["args"]
        assert "--model" not in args_str

    @pytest.mark.asyncio
    async def test_query_disallows_plan_mode(self, tmp_path):
        platform = MockPlatform()
        actor = _make_actor(platform, tmp_path=tmp_path)
        actor.cwd.mkdir(parents=True, exist_ok=True)

        with (
            patch(
                "mcp_handley_lab.messenger.server.spawn",
                return_value="claude-test",
            ) as mock_spawn,
            patch(
                "mcp_handley_lab.messenger.server.run",
                return_value="response",
            ),
        ):
            actor._query("hello")

        args_str = mock_spawn.call_args[1]["args"]
        assert "--disallowed-tools" in args_str
        assert "EnterPlanMode" in args_str


class TestStatePersistence:
    def test_save_load_with_model(self, tmp_path):
        platform = MockPlatform()
        actor = _make_actor(platform, tmp_path=tmp_path)
        actor.loop_id = "claude-123"
        actor.session_id = "sess-abc"
        actor._model = "opus"
        actor.cwd.mkdir(parents=True, exist_ok=True)
        actor._save_state()

        actor2 = _make_actor(platform, tmp_path=tmp_path)
        actor2._load_state()
        assert actor2.loop_id == "claude-123"
        assert actor2.session_id == "sess-abc"
        assert actor2._model == "opus"

    def test_load_state_without_model(self, tmp_path):
        """Old state files without model field still load correctly."""
        import json

        platform = MockPlatform()
        actor = _make_actor(platform, tmp_path=tmp_path)
        state_file = tmp_path / "loop_state.json"
        state_file.write_text(
            json.dumps({"loop_id": "claude-old", "session_id": "sess-old"})
        )
        actor._load_state()
        assert actor.loop_id == "claude-old"
        assert actor._model == ""


# ---------------------------------------------------------------------------
# _extract_usage / _context_footer tests
# ---------------------------------------------------------------------------

_RESULT_EVENT = {
    "type": "result",
    "total_cost_usd": 0.05,
    "modelUsage": {
        "claude-opus-4-6": {
            "contextWindow": 200000,
            "inputTokens": 80000,
            "outputTokens": 20000,
            "cacheCreationInputTokens": 5000,
            "cacheReadInputTokens": 3000,
            "costUSD": 0.05,
        },
    },
}


class TestExtractUsage:
    def test_extracts_from_last_cell(self):
        cells = [
            {"index": 0, "events": []},
            {"index": 1, "events": [{"type": "assistant"}, _RESULT_EVENT]},
        ]
        usage = _extract_usage(cells)
        assert usage is not None
        assert usage["context_window"] == 200000
        assert usage["input_tokens"] == 80000
        assert usage["output_tokens"] == 20000
        assert "cost_usd" not in usage

    def test_empty_cells(self):
        assert _extract_usage([]) is None

    def test_no_result_event(self):
        cells = [{"index": 0, "events": [{"type": "assistant"}]}]
        assert _extract_usage(cells) is None

    def test_no_events_key(self):
        cells = [{"index": 0}]
        assert _extract_usage(cells) is None


class TestContextFooter:
    def test_basic_footer(self):
        usage = {
            "context_window": 200000,
            "input_tokens": 80000,
            "output_tokens": 20000,
        }
        assert _context_footer(usage) == "50% context"

    def test_zero_context_window(self):
        usage = {
            "context_window": 0,
            "input_tokens": 0,
            "output_tokens": 0,
        }
        assert _context_footer(usage) == ""


class TestContextFooterOnResponse:
    @pytest.mark.asyncio
    async def test_response_includes_footer(self, tmp_path):
        platform = MockPlatform()
        actor = _make_actor(platform, tmp_path=tmp_path)
        actor.loop_id = "claude-123"
        actor.cwd.mkdir(parents=True, exist_ok=True)

        cells = [{"index": 0, "events": [_RESULT_EVENT]}]
        event = _make_event("hello", platform)
        event.kind = "text"

        with (
            patch(
                "mcp_handley_lab.messenger.server.run",
                return_value="Hello there!",
            ),
            patch(
                "mcp_handley_lab.messenger.server.read_cells_raw",
                return_value=cells,
            ),
        ):
            await actor._handle(event)

        text = platform.sent[0][1]
        assert "Hello there!" in text
        assert "50% context" in text

    @pytest.mark.asyncio
    async def test_response_no_footer_when_no_usage(self, tmp_path):
        platform = MockPlatform()
        actor = _make_actor(platform, tmp_path=tmp_path)
        actor.loop_id = "claude-123"
        actor.cwd.mkdir(parents=True, exist_ok=True)

        event = _make_event("hello", platform)
        event.kind = "text"

        with (
            patch(
                "mcp_handley_lab.messenger.server.run",
                return_value="Hello there!",
            ),
            patch(
                "mcp_handley_lab.messenger.server.read_cells_raw",
                return_value=[],
            ),
        ):
            await actor._handle(event)

        text = platform.sent[0][1]
        assert "Hello there!" in text
        assert "context" not in text.lower()
