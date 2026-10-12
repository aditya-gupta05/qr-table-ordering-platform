"""Phase 4 retrieval: documents, chunk configs, chunks, embeddings, FTS columns, HNSW indexes

Revision ID: 0003
Revises: 0002

Embedding dimension is fixed at 384 (AGENTS.md). One partial HNSW index per supported model so a
query with `WHERE model = '<literal>'` can use it; adding a model means adding an index here.
"""

import sqlalchemy as sa
from alembic import op
from pgvector.sqlalchemy import Vector
from sqlalchemy.dialects import postgresql

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None

SOURCE_TYPES = (
    "faq",
    "policy",
    "allergen_practices",
    "dish_guide",
    "about",
    "menu_pdf",
)
# (index name, model literal). Keep in sync with app.retrieval.embedder.MODELS.
HNSW_MODELS = (
    ("ix_chunk_emb_hnsw_bge_small", "BAAI/bge-small-en-v1.5"),
    ("ix_chunk_emb_hnsw_arctic_xs", "snowflake/snowflake-arctic-embed-xs"),
)


def upgrade() -> None:
    op.create_table(
        "documents",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("slug", sa.Text, nullable=False, unique=True),
        sa.Column("source_type", sa.Text, nullable=False),
        sa.Column("title", sa.Text, nullable=False),
        sa.Column("uri", sa.Text, nullable=False),
        sa.Column("content", sa.Text, nullable=False),
        sa.Column("content_hash", sa.Text, nullable=False),
        sa.Column("metadata", postgresql.JSONB, nullable=False, server_default=sa.text("'{}'")),
        sa.Column(
            "ingested_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint(
            "source_type IN (" + ", ".join(f"'{s}'" for s in SOURCE_TYPES) + ")",
            name="ck_documents_source_type",
        ),
    )

    op.create_table(
        "chunk_configs",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("name", sa.Text, nullable=False, unique=True),
        sa.Column("strategy", sa.Text, nullable=False),
        sa.Column("max_tokens", sa.Integer, nullable=False),
        sa.Column("overlap_tokens", sa.Integer, nullable=False),
        sa.Column("params", postgresql.JSONB, nullable=False, server_default=sa.text("'{}'")),
        sa.Column("tokenizer", sa.Text, nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint(
            "strategy IN ('fixed_token', 'recursive_md', 'sentence')",
            name="ck_chunk_configs_strategy",
        ),
    )

    op.create_table(
        "chunks",
        sa.Column("id", sa.BigInteger, primary_key=True),
        sa.Column(
            "document_id",
            sa.Integer,
            sa.ForeignKey("documents.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "chunk_config_id",
            sa.Integer,
            sa.ForeignKey("chunk_configs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("chunk_index", sa.Integer, nullable=False),
        sa.Column("content", sa.Text, nullable=False),
        sa.Column("start_char", sa.Integer, nullable=False),
        sa.Column("end_char", sa.Integer, nullable=False),
        sa.Column("token_count", sa.Integer, nullable=False),
        sa.Column("section_path", sa.Text, nullable=False, server_default=""),
        sa.Column("page", sa.Integer),
        sa.Column(
            "menu_item_ids",
            postgresql.ARRAY(sa.Integer),
            nullable=False,
            server_default=sa.text("'{}'"),
        ),
        sa.Column("metadata", postgresql.JSONB, nullable=False, server_default=sa.text("'{}'")),
        sa.Column(
            "tsv",
            postgresql.TSVECTOR,
            sa.Computed("to_tsvector('english', content)", persisted=True),
        ),
        sa.UniqueConstraint(
            "document_id", "chunk_config_id", "chunk_index", name="uq_chunks_doc_config_idx"
        ),
    )
    op.create_index("ix_chunks_tsv", "chunks", ["tsv"], postgresql_using="gin")
    op.create_index("ix_chunks_menu_item_ids", "chunks", ["menu_item_ids"], postgresql_using="gin")
    op.create_index("ix_chunks_chunk_config_id", "chunks", ["chunk_config_id"])

    op.create_table(
        "chunk_embeddings",
        sa.Column(
            "chunk_id",
            sa.BigInteger,
            sa.ForeignKey("chunks.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("model", sa.Text, primary_key=True),
        sa.Column("embedding", Vector(384), nullable=False),
        sa.Column("embed_text_hash", sa.Text, nullable=False),
    )
    for name, model in HNSW_MODELS:
        op.execute(
            f"CREATE INDEX {name} ON chunk_embeddings USING hnsw "
            f"(embedding vector_cosine_ops) WITH (m = 16, ef_construction = 64) "
            f"WHERE model = '{model}'"
        )

    # No ANN index on menu items: ~42 rows, searched exactly after SQL filters.
    op.create_table(
        "menu_item_embeddings",
        sa.Column(
            "menu_item_id",
            sa.Integer,
            sa.ForeignKey("menu_items.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("model", sa.Text, primary_key=True),
        sa.Column("content_hash", sa.Text, nullable=False),
        sa.Column("embedding", Vector(384), nullable=False),
    )

    op.execute(
        "ALTER TABLE menu_items ADD COLUMN tsv tsvector GENERATED ALWAYS AS ("
        "setweight(to_tsvector('english', name), 'A') || "
        "setweight(to_tsvector('english', description), 'B')) STORED"
    )
    op.create_index("ix_menu_items_tsv", "menu_items", ["tsv"], postgresql_using="gin")


def downgrade() -> None:
    op.drop_index("ix_menu_items_tsv", table_name="menu_items")
    op.drop_column("menu_items", "tsv")
    op.drop_table("menu_item_embeddings")
    op.drop_table("chunk_embeddings")  # indexes go with the table
    op.drop_table("chunks")
    op.drop_table("chunk_configs")
    op.drop_table("documents")
