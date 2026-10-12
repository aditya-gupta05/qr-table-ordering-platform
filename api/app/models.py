import uuid
from datetime import datetime

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    Computed,
    DateTime,
    Enum,
    Float,
    ForeignKey,
    Index,
    Integer,
    SmallInteger,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, TSVECTOR, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

from app.vocab import ORDER_STATUSES


class Base(DeclarativeBase):
    pass


order_status_enum = Enum(*ORDER_STATUSES, name="order_status")


class DiningTable(Base):
    __tablename__ = "dining_tables"

    id: Mapped[int] = mapped_column(primary_key=True)
    label: Mapped[str] = mapped_column(Text, unique=True)
    qr_token: Mapped[str] = mapped_column(Text, unique=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class MenuCategory(Base):
    __tablename__ = "menu_categories"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(Text, unique=True)
    position: Mapped[int] = mapped_column(Integer, default=0, server_default="0")

    items: Mapped[list["MenuItem"]] = relationship(back_populates="category")


class MenuItem(Base):
    __tablename__ = "menu_items"
    __table_args__ = (
        UniqueConstraint("category_id", "name", name="uq_menu_items_category_name"),
        CheckConstraint("price_cents >= 0", name="ck_menu_items_price_nonneg"),
        CheckConstraint("spice_level BETWEEN 0 AND 3", name="ck_menu_items_spice_range"),
        Index("ix_menu_items_dietary_tags", "dietary_tags", postgresql_using="gin"),
        Index("ix_menu_items_allergens", "allergens", postgresql_using="gin"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    category_id: Mapped[int] = mapped_column(ForeignKey("menu_categories.id"))
    name: Mapped[str] = mapped_column(Text)
    description: Mapped[str] = mapped_column(Text, default="", server_default="")
    price_cents: Mapped[int] = mapped_column(Integer)
    is_available: Mapped[bool] = mapped_column(default=True, server_default=text("true"))
    dietary_tags: Mapped[list[str]] = mapped_column(
        ARRAY(Text), default=list, server_default=text("'{}'")
    )
    allergens: Mapped[list[str]] = mapped_column(
        ARRAY(Text), default=list, server_default=text("'{}'")
    )
    spice_level: Mapped[int] = mapped_column(SmallInteger, default=0, server_default="0")
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    category: Mapped[MenuCategory] = relationship(back_populates="items")


class Order(Base):
    __tablename__ = "orders"
    __table_args__ = (Index("ix_orders_status_created_at", "status", "created_at"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    public_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), unique=True, server_default=text("gen_random_uuid()")
    )
    table_id: Mapped[int] = mapped_column(ForeignKey("dining_tables.id"))
    status: Mapped[str] = mapped_column(
        order_status_enum, default="received", server_default="received"
    )
    total_cents: Mapped[int] = mapped_column(Integer)
    idempotency_key: Mapped[str] = mapped_column(Text, unique=True)
    request_hash: Mapped[str] = mapped_column(Text)
    notes: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    items: Mapped[list["OrderItem"]] = relationship(back_populates="order", order_by="OrderItem.id")


class OrderItem(Base):
    __tablename__ = "order_items"
    __table_args__ = (CheckConstraint("qty > 0", name="ck_order_items_qty_positive"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    order_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("orders.id", ondelete="CASCADE"), index=True
    )
    menu_item_id: Mapped[int] = mapped_column(ForeignKey("menu_items.id"))
    qty: Mapped[int] = mapped_column(Integer)
    unit_price_cents: Mapped[int] = mapped_column(Integer)
    item_name: Mapped[str] = mapped_column(Text)
    notes: Mapped[str | None] = mapped_column(Text)

    order: Mapped[Order] = relationship(back_populates="items")


# --- Phase 4 retrieval (migration 0003). menu_items.tsv exists in the DB (generated column) but
# is deliberately not mapped here: the ORM would load it into every menu query for nothing.


class Document(Base):
    __tablename__ = "documents"

    id: Mapped[int] = mapped_column(primary_key=True)
    slug: Mapped[str] = mapped_column(Text, unique=True)
    source_type: Mapped[str] = mapped_column(Text)
    title: Mapped[str] = mapped_column(Text)
    uri: Mapped[str] = mapped_column(Text)
    content: Mapped[str] = mapped_column(Text)  # cleaned text; chunk offsets index into this
    content_hash: Mapped[str] = mapped_column(Text)
    meta: Mapped[dict] = mapped_column("metadata", JSONB, server_default=text("'{}'"))
    ingested_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ChunkConfig(Base):
    __tablename__ = "chunk_configs"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(Text, unique=True)
    strategy: Mapped[str] = mapped_column(Text)
    max_tokens: Mapped[int] = mapped_column(Integer)
    overlap_tokens: Mapped[int] = mapped_column(Integer)
    params: Mapped[dict] = mapped_column(JSONB, server_default=text("'{}'"))
    tokenizer: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Chunk(Base):
    __tablename__ = "chunks"
    __table_args__ = (
        UniqueConstraint(
            "document_id", "chunk_config_id", "chunk_index", name="uq_chunks_doc_config_idx"
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    document_id: Mapped[int] = mapped_column(ForeignKey("documents.id", ondelete="CASCADE"))
    chunk_config_id: Mapped[int] = mapped_column(
        ForeignKey("chunk_configs.id", ondelete="CASCADE"), index=True
    )
    chunk_index: Mapped[int] = mapped_column(Integer)
    content: Mapped[str] = mapped_column(Text)
    start_char: Mapped[int] = mapped_column(Integer)
    end_char: Mapped[int] = mapped_column(Integer)
    token_count: Mapped[int] = mapped_column(Integer)
    section_path: Mapped[str] = mapped_column(Text, server_default="")
    page: Mapped[int | None] = mapped_column(Integer)
    menu_item_ids: Mapped[list[int]] = mapped_column(ARRAY(Integer), server_default=text("'{}'"))
    meta: Mapped[dict] = mapped_column("metadata", JSONB, server_default=text("'{}'"))
    tsv: Mapped[str] = mapped_column(TSVECTOR, Computed("to_tsvector('english', content)"))


class ChunkEmbedding(Base):
    __tablename__ = "chunk_embeddings"

    chunk_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("chunks.id", ondelete="CASCADE"), primary_key=True
    )
    model: Mapped[str] = mapped_column(Text, primary_key=True)
    embedding = mapped_column(Vector(384))
    embed_text_hash: Mapped[str] = mapped_column(Text)


class MenuItemEmbedding(Base):
    __tablename__ = "menu_item_embeddings"

    menu_item_id: Mapped[int] = mapped_column(
        ForeignKey("menu_items.id", ondelete="CASCADE"), primary_key=True
    )
    model: Mapped[str] = mapped_column(Text, primary_key=True)
    content_hash: Mapped[str] = mapped_column(Text)
    embedding = mapped_column(Vector(384))


# --- Phase 5 chatbot (migration 0004). Timestamps are timestamptz. `turn_id` is a UUID minted per
# diner message; chat_messages (user + assistant rows), llm_calls, tool_calls and chat_actions of
# one turn all share it.


class ChatSession(Base):
    __tablename__ = "chat_sessions"
    __table_args__ = (
        CheckConstraint(
            "outcome IN ('resolved', 'escalated', 'abandoned', 'no_question')",
            name="ck_chat_sessions_outcome",
        ),
        Index("ix_chat_sessions_table_created", "table_id", "created_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    table_id: Mapped[int] = mapped_column(ForeignKey("dining_tables.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    last_activity_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    busy_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    prompt_version: Mapped[str | None] = mapped_column(Text)
    config: Mapped[dict] = mapped_column(JSONB, server_default=text("'{}'"))
    declared_allergens: Mapped[list[str]] = mapped_column(
        ARRAY(Text), default=list, server_default=text("'{}'")
    )
    outcome: Mapped[str | None] = mapped_column(Text)
    outcome_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    converted: Mapped[bool] = mapped_column(default=False, server_default=text("false"))
    turn_count: Mapped[int] = mapped_column(Integer, default=0, server_default="0")


class ChatMessage(Base):
    __tablename__ = "chat_messages"
    __table_args__ = (
        UniqueConstraint("session_id", "client_message_id", name="uq_chat_messages_client_id"),
        UniqueConstraint("session_id", "turn_index", "role", name="uq_chat_messages_turn_role"),
        CheckConstraint("role IN ('user', 'assistant')", name="ck_chat_messages_role"),
        Index("ix_chat_messages_turn_id", "turn_id"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    session_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("chat_sessions.id", ondelete="CASCADE")
    )
    turn_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    turn_index: Mapped[int] = mapped_column(Integer)
    role: Mapped[str] = mapped_column(Text)
    content: Mapped[str] = mapped_column(Text)  # PII-redacted; what the diner sees / sent
    content_llm: Mapped[str | None] = mapped_column(Text)  # what was sent to the model
    client_message_id: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str | None] = mapped_column(Text)  # respond() status for assistant rows
    turn_outcome: Mapped[str | None] = mapped_column(Text)
    citations: Mapped[list] = mapped_column(JSONB, server_default=text("'[]'"))
    allergen_notice: Mapped[dict | None] = mapped_column(JSONB)
    guardrails: Mapped[list] = mapped_column(JSONB, server_default=text("'[]'"))
    feedback: Mapped[bool | None] = mapped_column()  # True = helpful
    latency_ms: Mapped[int | None] = mapped_column(Integer)
    request_id: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class LLMCall(Base):
    """One row per LLM call. Never stores prompt or completion text (PII rule)."""

    __tablename__ = "llm_calls"
    __table_args__ = (
        Index("ix_llm_calls_session_turn", "session_id", "turn_id"),
        Index("ix_llm_calls_created_at", "created_at"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    session_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("chat_sessions.id", ondelete="CASCADE")
    )
    turn_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    call_index: Mapped[int] = mapped_column(SmallInteger)
    attempt: Mapped[int] = mapped_column(SmallInteger, default=1, server_default="1")
    model: Mapped[str] = mapped_column(Text)
    thinking: Mapped[str] = mapped_column(Text, default="off", server_default="off")
    temperature: Mapped[float | None] = mapped_column(Float)
    prompt_version: Mapped[str | None] = mapped_column(Text)
    prefix_sha256: Mapped[str | None] = mapped_column(Text)
    prompt_tokens: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    cache_hit_tokens: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    cache_miss_tokens: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    completion_tokens: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    reasoning_tokens: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    cost_usd_micros: Mapped[int] = mapped_column(BigInteger, default=0, server_default="0")
    price_period: Mapped[str | None] = mapped_column(Text)  # peak | offpeak
    latency_ms: Mapped[int | None] = mapped_column(Integer)
    finish_reason: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(Text, default="ok", server_default="ok")
    error_kind: Mapped[str | None] = mapped_column(Text)
    http_status: Mapped[int | None] = mapped_column(Integer)
    request_id: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ToolCall(Base):
    __tablename__ = "tool_calls"
    __table_args__ = (Index("ix_tool_calls_session_turn", "session_id", "turn_id"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    llm_call_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("llm_calls.id", ondelete="SET NULL")
    )
    session_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("chat_sessions.id", ondelete="CASCADE")
    )
    turn_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    name: Mapped[str] = mapped_column(Text)
    arguments: Mapped[dict | None] = mapped_column(JSONB)  # redacted, size-capped
    status: Mapped[str] = mapped_column(Text)  # ok | error | invalid_arguments | unknown_tool
    widened: Mapped[bool] = mapped_column(default=False, server_default=text("false"))
    result_ids: Mapped[list] = mapped_column(JSONB, server_default=text("'[]'"))
    signals: Mapped[dict] = mapped_column(JSONB, server_default=text("'{}'"))
    retrieval_timings: Mapped[dict] = mapped_column(JSONB, server_default=text("'{}'"))
    latency_ms: Mapped[int | None] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ChatAction(Base):
    __tablename__ = "chat_actions"
    __table_args__ = (
        CheckConstraint("kind IN ('add_to_cart', 'call_waiter')", name="ck_chat_actions_kind"),
        CheckConstraint(
            "status IN ('proposed', 'accepted', 'dismissed')", name="ck_chat_actions_status"
        ),
        Index("ix_chat_actions_session_turn", "session_id", "turn_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    session_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("chat_sessions.id", ondelete="CASCADE")
    )
    turn_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    kind: Mapped[str] = mapped_column(Text)
    payload: Mapped[dict] = mapped_column(JSONB, server_default=text("'{}'"))
    status: Mapped[str] = mapped_column(Text, default="proposed", server_default="proposed")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class WaiterRequest(Base):
    __tablename__ = "waiter_requests"
    __table_args__ = (
        CheckConstraint("source IN ('chat', 'button')", name="ck_waiter_requests_source"),
        CheckConstraint(
            "reason IN ('allergy_or_dietary', 'question_not_answered', "
            "'diner_asked_for_staff', 'other')",
            name="ck_waiter_requests_reason",
        ),
        CheckConstraint(
            "status IN ('open', 'acknowledged', 'closed')", name="ck_waiter_requests_status"
        ),
        # At most one open request per table; a second tap returns the existing one.
        Index(
            "uq_waiter_requests_one_open_per_table",
            "table_id",
            unique=True,
            postgresql_where=text("status = 'open'"),
        ),
        Index("ix_waiter_requests_status_created", "status", "created_at"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    table_id: Mapped[int] = mapped_column(ForeignKey("dining_tables.id"))
    session_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("chat_sessions.id", ondelete="SET NULL")
    )
    action_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("chat_actions.id", ondelete="SET NULL"), unique=True
    )
    source: Mapped[str] = mapped_column(Text)
    reason: Mapped[str] = mapped_column(Text)
    note: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(Text, default="open", server_default="open")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    acknowledged_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class AnswerCache(Base):
    __tablename__ = "answer_cache"
    __table_args__ = (
        Index(
            "ix_answer_cache_versions", "prompt_version", "model", "corpus_version", "menu_version"
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    question_norm: Mapped[str] = mapped_column(Text)
    embedding = mapped_column(Vector(384))
    message: Mapped[dict] = mapped_column(JSONB)
    prompt_version: Mapped[str] = mapped_column(Text)
    model: Mapped[str] = mapped_column(Text)
    corpus_version: Mapped[str] = mapped_column(Text)
    menu_version: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    hits: Mapped[int] = mapped_column(Integer, default=0, server_default="0")


class AnswerCacheShadow(Base):
    """Shadow mode: what the cache WOULD have served, paired with the fresh answer."""

    __tablename__ = "answer_cache_shadow"

    turn_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    cache_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("answer_cache.id", ondelete="CASCADE")
    )
    similarity: Mapped[float] = mapped_column(Float)
    fresh_message_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("chat_messages.id", ondelete="SET NULL")
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
