"""Phase 5 chatbot: sessions, messages, llm/tool call logs, actions, waiter requests, answer cache

Revision ID: 0004
Revises: 0003

No prompt or answer text is stored in llm_calls/tool_calls (PII rule); chat_messages holds
PII-redacted text only. answer_cache.embedding is vector(384) like the retrieval tables.
"""

import sqlalchemy as sa
from alembic import op
from pgvector.sqlalchemy import Vector
from sqlalchemy.dialects import postgresql as pg

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None

NOW = sa.func.now()
UUID = pg.UUID(as_uuid=True)
GEN_UUID = sa.text("gen_random_uuid()")


def _ts(name: str, *, nullable: bool = False, default: bool = True) -> sa.Column:
    return sa.Column(
        name,
        sa.DateTime(timezone=True),
        nullable=nullable,
        server_default=NOW if default else None,
    )


def upgrade() -> None:
    op.create_table(
        "chat_sessions",
        sa.Column("id", UUID, primary_key=True, server_default=GEN_UUID),
        sa.Column("table_id", sa.Integer, sa.ForeignKey("dining_tables.id"), nullable=False),
        _ts("created_at"),
        _ts("last_activity_at"),
        _ts("busy_until", nullable=True, default=False),
        sa.Column("prompt_version", sa.Text),
        sa.Column("config", pg.JSONB, nullable=False, server_default=sa.text("'{}'")),
        sa.Column(
            "declared_allergens", pg.ARRAY(sa.Text), nullable=False, server_default=sa.text("'{}'")
        ),
        sa.Column("outcome", sa.Text),
        _ts("outcome_at", nullable=True, default=False),
        sa.Column("converted", sa.Boolean, nullable=False, server_default=sa.text("false")),
        sa.Column("turn_count", sa.Integer, nullable=False, server_default="0"),
        sa.CheckConstraint(
            "outcome IN ('resolved', 'escalated', 'abandoned', 'no_question')",
            name="ck_chat_sessions_outcome",
        ),
    )
    op.create_index("ix_chat_sessions_table_created", "chat_sessions", ["table_id", "created_at"])

    op.create_table(
        "chat_messages",
        sa.Column("id", sa.BigInteger, primary_key=True),
        sa.Column(
            "session_id", UUID, sa.ForeignKey("chat_sessions.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("turn_id", UUID, nullable=False),
        sa.Column("turn_index", sa.Integer, nullable=False),
        sa.Column("role", sa.Text, nullable=False),
        sa.Column("content", sa.Text, nullable=False),
        sa.Column("content_llm", sa.Text),
        sa.Column("client_message_id", sa.Text),
        sa.Column("status", sa.Text),
        sa.Column("turn_outcome", sa.Text),
        sa.Column("citations", pg.JSONB, nullable=False, server_default=sa.text("'[]'")),
        sa.Column("allergen_notice", pg.JSONB),
        sa.Column("guardrails", pg.JSONB, nullable=False, server_default=sa.text("'[]'")),
        sa.Column("feedback", sa.Boolean),
        sa.Column("latency_ms", sa.Integer),
        sa.Column("request_id", sa.Text),
        _ts("created_at"),
        sa.UniqueConstraint("session_id", "client_message_id", name="uq_chat_messages_client_id"),
        sa.UniqueConstraint(
            "session_id", "turn_index", "role", name="uq_chat_messages_turn_role"
        ),
        sa.CheckConstraint("role IN ('user', 'assistant')", name="ck_chat_messages_role"),
    )
    op.create_index("ix_chat_messages_turn_id", "chat_messages", ["turn_id"])

    op.create_table(
        "llm_calls",
        sa.Column("id", sa.BigInteger, primary_key=True),
        sa.Column(
            "session_id", UUID, sa.ForeignKey("chat_sessions.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("turn_id", UUID, nullable=False),
        sa.Column("call_index", sa.SmallInteger, nullable=False),
        sa.Column("attempt", sa.SmallInteger, nullable=False, server_default="1"),
        sa.Column("model", sa.Text, nullable=False),
        sa.Column("thinking", sa.Text, nullable=False, server_default="off"),
        sa.Column("temperature", sa.Float),
        sa.Column("prompt_version", sa.Text),
        sa.Column("prefix_sha256", sa.Text),
        sa.Column("prompt_tokens", sa.Integer, nullable=False, server_default="0"),
        sa.Column("cache_hit_tokens", sa.Integer, nullable=False, server_default="0"),
        sa.Column("cache_miss_tokens", sa.Integer, nullable=False, server_default="0"),
        sa.Column("completion_tokens", sa.Integer, nullable=False, server_default="0"),
        sa.Column("reasoning_tokens", sa.Integer, nullable=False, server_default="0"),
        sa.Column("cost_usd_micros", sa.BigInteger, nullable=False, server_default="0"),
        sa.Column("price_period", sa.Text),
        sa.Column("latency_ms", sa.Integer),
        sa.Column("finish_reason", sa.Text),
        sa.Column("status", sa.Text, nullable=False, server_default="ok"),
        sa.Column("error_kind", sa.Text),
        sa.Column("http_status", sa.Integer),
        sa.Column("request_id", sa.Text),
        _ts("created_at"),
    )
    op.create_index("ix_llm_calls_session_turn", "llm_calls", ["session_id", "turn_id"])
    op.create_index("ix_llm_calls_created_at", "llm_calls", ["created_at"])

    op.create_table(
        "tool_calls",
        sa.Column("id", sa.BigInteger, primary_key=True),
        sa.Column("llm_call_id", sa.BigInteger, sa.ForeignKey("llm_calls.id", ondelete="SET NULL")),
        sa.Column(
            "session_id", UUID, sa.ForeignKey("chat_sessions.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("turn_id", UUID, nullable=False),
        sa.Column("name", sa.Text, nullable=False),
        sa.Column("arguments", pg.JSONB),
        sa.Column("status", sa.Text, nullable=False),
        sa.Column("widened", sa.Boolean, nullable=False, server_default=sa.text("false")),
        sa.Column("result_ids", pg.JSONB, nullable=False, server_default=sa.text("'[]'")),
        sa.Column("signals", pg.JSONB, nullable=False, server_default=sa.text("'{}'")),
        sa.Column("retrieval_timings", pg.JSONB, nullable=False, server_default=sa.text("'{}'")),
        sa.Column("latency_ms", sa.Integer),
        _ts("created_at"),
    )
    op.create_index("ix_tool_calls_session_turn", "tool_calls", ["session_id", "turn_id"])

    op.create_table(
        "chat_actions",
        sa.Column("id", UUID, primary_key=True, server_default=GEN_UUID),
        sa.Column(
            "session_id", UUID, sa.ForeignKey("chat_sessions.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("turn_id", UUID, nullable=False),
        sa.Column("kind", sa.Text, nullable=False),
        sa.Column("payload", pg.JSONB, nullable=False, server_default=sa.text("'{}'")),
        sa.Column("status", sa.Text, nullable=False, server_default="proposed"),
        _ts("created_at"),
        _ts("resolved_at", nullable=True, default=False),
        sa.CheckConstraint("kind IN ('add_to_cart', 'call_waiter')", name="ck_chat_actions_kind"),
        sa.CheckConstraint(
            "status IN ('proposed', 'accepted', 'dismissed')", name="ck_chat_actions_status"
        ),
    )
    op.create_index("ix_chat_actions_session_turn", "chat_actions", ["session_id", "turn_id"])

    op.create_table(
        "waiter_requests",
        sa.Column("id", sa.BigInteger, primary_key=True),
        sa.Column("table_id", sa.Integer, sa.ForeignKey("dining_tables.id"), nullable=False),
        sa.Column("session_id", UUID, sa.ForeignKey("chat_sessions.id", ondelete="SET NULL")),
        sa.Column(
            "action_id", UUID, sa.ForeignKey("chat_actions.id", ondelete="SET NULL"), unique=True
        ),
        sa.Column("source", sa.Text, nullable=False),
        sa.Column("reason", sa.Text, nullable=False),
        sa.Column("note", sa.Text),
        sa.Column("status", sa.Text, nullable=False, server_default="open"),
        _ts("created_at"),
        _ts("acknowledged_at", nullable=True, default=False),
        _ts("updated_at"),
        sa.CheckConstraint("source IN ('chat', 'button')", name="ck_waiter_requests_source"),
        sa.CheckConstraint(
            "reason IN ('allergy_or_dietary', 'question_not_answered', "
            "'diner_asked_for_staff', 'other')",
            name="ck_waiter_requests_reason",
        ),
        sa.CheckConstraint(
            "status IN ('open', 'acknowledged', 'closed')", name="ck_waiter_requests_status"
        ),
    )
    op.create_index(
        "uq_waiter_requests_one_open_per_table",
        "waiter_requests",
        ["table_id"],
        unique=True,
        postgresql_where=sa.text("status = 'open'"),
    )
    op.create_index("ix_waiter_requests_status_created", "waiter_requests", ["status", "created_at"])

    op.create_table(
        "answer_cache",
        sa.Column("id", sa.BigInteger, primary_key=True),
        sa.Column("question_norm", sa.Text, nullable=False),
        sa.Column("embedding", Vector(384), nullable=False),
        sa.Column("message", pg.JSONB, nullable=False),
        sa.Column("prompt_version", sa.Text, nullable=False),
        sa.Column("model", sa.Text, nullable=False),
        sa.Column("corpus_version", sa.Text, nullable=False),
        sa.Column("menu_version", sa.Text, nullable=False),
        _ts("created_at"),
        sa.Column("hits", sa.Integer, nullable=False, server_default="0"),
    )
    op.create_index(
        "ix_answer_cache_versions",
        "answer_cache",
        ["prompt_version", "model", "corpus_version", "menu_version"],
    )

    op.create_table(
        "answer_cache_shadow",
        sa.Column("turn_id", UUID, primary_key=True),
        sa.Column(
            "cache_id",
            sa.BigInteger,
            sa.ForeignKey("answer_cache.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("similarity", sa.Float, nullable=False),
        sa.Column(
            "fresh_message_id", sa.BigInteger, sa.ForeignKey("chat_messages.id", ondelete="SET NULL")
        ),
        _ts("created_at"),
    )


def downgrade() -> None:
    for table in (
        "answer_cache_shadow",
        "answer_cache",
        "waiter_requests",
        "chat_actions",
        "tool_calls",
        "llm_calls",
        "chat_messages",
        "chat_sessions",
    ):
        op.drop_table(table)
