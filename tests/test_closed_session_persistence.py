"""Fail-closed persistence behavior for compression-ended sessions."""

from unittest.mock import MagicMock

import pytest

from hermes_state import CompressionSessionClosedError
from run_agent import AIAgent


def _agent_with_db(db: MagicMock) -> AIAgent:
    agent = AIAgent.__new__(AIAgent)
    agent._persist_disabled = False
    agent._session_db = db
    agent._session_db_created = True
    agent._flushed_db_message_ids = set()
    agent._last_flushed_db_idx = 0
    agent._persist_user_message_idx = None
    agent._persist_user_message_override = None
    agent._persist_user_message_timestamp = None
    agent._active_compression_lock_holder = None
    agent.session_id = "compression-parent"
    return agent


def test_compression_closed_persist_propagates_terminal_error():
    db = MagicMock()
    db.append_message.side_effect = CompressionSessionClosedError("compression-parent")
    agent = _agent_with_db(db)

    with pytest.raises(CompressionSessionClosedError):
        agent._flush_messages_to_session_db_unlocked(
            [{"role": "user", "content": "turn"}],
        )


def test_transient_persist_error_remains_best_effort():
    db = MagicMock()
    db.append_message.side_effect = RuntimeError("temporary lock")
    agent = _agent_with_db(db)

    agent._flush_messages_to_session_db_unlocked(
        [{"role": "user", "content": "turn"}],
    )