import uuid
from datetime import datetime

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    Computed,
    DateTime,
    Enum,
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
