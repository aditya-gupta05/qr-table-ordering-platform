import uuid
from datetime import datetime
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field

from app.models import Order, OrderItem


class MenuItemOut(BaseModel):
    id: int
    name: str
    description: str
    price_cents: int
    dietary_tags: list[str]
    allergens: list[str]
    spice_level: int
    is_available: bool = True

    model_config = ConfigDict(from_attributes=True)


class MenuCategoryOut(BaseModel):
    id: int
    name: str
    items: list[MenuItemOut]


class TableOut(BaseModel):
    id: int
    label: str


class OrderItemIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    menu_item_id: int
    qty: Annotated[int, Field(ge=1, le=20)]
    notes: Annotated[str | None, Field(max_length=500)] = None


class OrderIn(BaseModel):
    # extra="forbid": a client-sent price (or any unknown field) is a 422, never silently ignored.
    model_config = ConfigDict(extra="forbid")

    table_token: str
    items: Annotated[list[OrderItemIn], Field(min_length=1, max_length=50)]
    notes: Annotated[str | None, Field(max_length=500)] = None


class OrderItemOut(BaseModel):
    menu_item_id: int
    item_name: str
    qty: int
    unit_price_cents: int
    notes: str | None


class OrderOut(BaseModel):
    id: int
    public_id: uuid.UUID
    table_label: str
    status: str
    total_cents: int
    notes: str | None
    items: list[OrderItemOut]
    created_at: datetime
    updated_at: datetime

    @classmethod
    def build(cls, order: Order, table_label: str) -> "OrderOut":
        return cls(
            id=order.id,
            public_id=order.public_id,
            table_label=table_label,
            status=order.status,
            total_cents=order.total_cents,
            notes=order.notes,
            items=[_item(i) for i in order.items],
            created_at=order.created_at,
            updated_at=order.updated_at,
        )


def _item(i: OrderItem) -> OrderItemOut:
    return OrderItemOut(
        menu_item_id=i.menu_item_id,
        item_name=i.item_name,
        qty=i.qty,
        unit_price_cents=i.unit_price_cents,
        notes=i.notes,
    )


class StatusIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: str
