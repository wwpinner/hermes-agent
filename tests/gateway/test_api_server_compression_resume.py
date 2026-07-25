"""Regression tests for API-server session continuation routing."""

import asyncio
from unittest.mock import MagicMock

import pytest

from gateway.platforms.api_server import (
    APIServerAdapter,
    _CompressionContinuationUnavailable,
)
from gateway.platforms.base import PlatformConfig


def _adapter() -> APIServerAdapter:
    return APIServerAdapter(PlatformConfig(enabled=True, extra={}))


def test_compression_parent_resolves_to_verified_live_tip():
    db = MagicMock()
    db.get_session.side_effect = [
        {"ended_at": "2026-07-25T12:00:00Z", "end_reason": "compression"},
        {"ended_at": None, "end_reason": None},
    ]
    db.get_compression_tip.return_value = "tip-session"

    resolved = asyncio.run(_adapter()._resolve_session_resume_target(db, "parent-session"))

    assert resolved == "tip-session"


def test_compression_parent_without_tip_fails_closed():
    db = MagicMock()
    db.get_session.return_value = {
        "ended_at": "2026-07-25T12:00:00Z",
        "end_reason": "compression",
    }
    db.get_compression_tip.return_value = None

    with pytest.raises(_CompressionContinuationUnavailable):
        asyncio.run(_adapter()._resolve_session_resume_target(db, "parent-session"))


def test_compression_parent_with_ended_tip_fails_closed():
    db = MagicMock()
    db.get_session.side_effect = [
        {"ended_at": "2026-07-25T12:00:00Z", "end_reason": "compression"},
        {"ended_at": "2026-07-25T12:01:00Z", "end_reason": "session_reset"},
    ]
    db.get_compression_tip.return_value = "ended-tip"

    with pytest.raises(_CompressionContinuationUnavailable):
        asyncio.run(_adapter()._resolve_session_resume_target(db, "parent-session"))
