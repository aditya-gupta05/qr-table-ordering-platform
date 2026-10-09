"""initial schema

Revision ID: 0001
Revises:
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None

ORDER_STATUSES = ("received", "preparing", "ready", "served", "cancelled")


def upgrade() -> None:
    # pgvector is enabled now so Phase 4 only needs to add the chunks table.
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")

    op.create_table(
        "dining_tables",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("label", sa.Text, nullable=False, unique=True),
        sa.Column("qr_token", sa.Text, nullable=False, unique=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    )
    op.create_table(
        "menu_categories",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("name", sa.Text, nullable=False, unique=True),
        sa.Column("position", sa.Integer, nullable=False, server_default="0"),
    )
    op.create_table(
        "menu_items",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("category_id", sa.Integer, sa.ForeignKey("menu_categories.id"), nullable=False),
        sa.Column("name", sa.Text, nullable=False),
        sa.Column("description", sa.Text, nullable=False, server_default=""),
        sa.Column("price_cents", sa.Integer, nullable=False),
        sa.Column("is_available", sa.Boolean, nullable=False, server_default=sa.text("true")),
        sa.Column(
            "dietary_tags", postgresql.ARRAY(sa.Text), nullable=False, server_default=sa.text("'{}'")
        ),
        sa.Column(
            "allergens", postgresql.ARRAY(sa.Text), nullable=False, server_default=sa.text("'{}'")
        ),
        sa.Column("spice_level", sa.SmallInteger, nullable=False, server_default="0"),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint("price_cents >= 0", name="ck_menu_items_price_nonneg"),
        sa.CheckConstraint("spice_level BETWEEN 0 AND 3", name="ck_menu_items_spice_range"),
        sa.UniqueConstraint("category_id", "name", name="uq_menu_items_category_name"),
    )
    op.create_index(
        "ix_menu_items_dietary_tags", "menu_items", ["dietary_tags"], postgresql_using="gin"
    )
    op.create_index("ix_menu_items_allergens", "menu_items", ["allergens"], postgresql_using="gin")

    order_status = postgresql.ENUM(*ORDER_STATUSES, name="order_status", create_type=False)
    order_status.create(op.get_bind(), checkfirst=True)

    op.create_table(
        "orders",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column("table_id", sa.Integer, sa.ForeignKey("dining_tables.id"), nullable=False),
        sa.Column("status", order_status, nullable=False, server_default="received"),
        sa.Column("total_cents", sa.Integer, nullable=False),
        sa.Column("idempotency_key", sa.Text, nullable=False, unique=True),
        sa.Column("notes", sa.Text),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    )
    op.create_index("ix_orders_status_created_at", "orders", ["status", "created_at"])

    op.create_table(
        "order_items",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column(
            "order_id", sa.BigInteger, sa.ForeignKey("orders.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("menu_item_id", sa.Integer, sa.ForeignKey("menu_items.id"), nullable=False),
        sa.Column("qty", sa.Integer, nullable=False),
        sa.Column("unit_price_cents", sa.Integer, nullable=False),
        sa.Column("item_name", sa.Text, nullable=False),
        sa.Column("notes", sa.Text),
        sa.CheckConstraint("qty > 0", name="ck_order_items_qty_positive"),
    )
    op.create_index("ix_order_items_order_id", "order_items", ["order_id"])


def downgrade() -> None:
    op.drop_table("order_items")
    op.drop_table("orders")
    op.execute("DROP TYPE IF EXISTS order_status")
    op.drop_table("menu_items")
    op.drop_table("menu_categories")
    op.drop_table("dining_tables")
