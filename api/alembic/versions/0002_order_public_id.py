"""orders.public_id and orders.request_hash

Revision ID: 0002
Revises: 0001
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # gen_random_uuid() is built in since Postgres 13. Existing rows (none yet) are backfilled
    # by the default; request_hash gets a placeholder so NOT NULL can be applied safely.
    op.add_column(
        "orders",
        sa.Column(
            "public_id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
            server_default=sa.text("gen_random_uuid()"),
        ),
    )
    op.add_column(
        "orders", sa.Column("request_hash", sa.Text, nullable=False, server_default="")
    )
    op.alter_column("orders", "request_hash", server_default=None)
    op.create_unique_constraint("uq_orders_public_id", "orders", ["public_id"])


def downgrade() -> None:
    op.drop_constraint("uq_orders_public_id", "orders", type_="unique")
    op.drop_column("orders", "request_hash")
    op.drop_column("orders", "public_id")
