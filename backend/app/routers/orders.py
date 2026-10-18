from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.deps import get_current_user
from app.models import Order, Product, User
from app.services.chat import _notify

router = APIRouter(prefix="/orders", tags=["orders"])

ORDER_STATUSES = ("confirmed", "on_the_way", "shipped", "delivered", "cancelled")

STATUS_NOTIFY = {
    "confirmed": ("order_confirmed", "Order confirmed", "Your order is confirmed."),
    "on_the_way": ("order_on_the_way", "Order on the way", "Your order is on the way."),
    "shipped": ("order_shipped", "Order shipped", "Your order has been shipped."),
    "delivered": ("order_delivered", "Order delivered", "Your order was delivered."),
    "cancelled": ("order_cancelled", "Order cancelled", "Your order was cancelled."),
}


class OrderStatusUpdate(BaseModel):
    status: str


class OrderOut(BaseModel):
    id: int
    product_id: int | None = Field(serialization_alias="productId")
    buyer_id: int = Field(serialization_alias="buyerId")
    seller_id: int = Field(serialization_alias="sellerId")
    conversation_id: int | None = Field(default=None, serialization_alias="conversationId")
    status: str
    amount: float | None = None
    currency: str | None = None
    product_title: str | None = Field(default=None, serialization_alias="productTitle")
    created_at: str | None = Field(default=None, serialization_alias="createdAt")

    model_config = {"populate_by_name": True}


def _order_out(o: Order) -> dict:
    return {
        "id": o.id,
        "productId": o.product_id,
        "buyerId": o.buyer_id,
        "sellerId": o.seller_id,
        "conversationId": o.conversation_id,
        "status": o.status,
        "amount": float(o.amount) if o.amount is not None else None,
        "currency": o.currency,
        "productTitle": o.product_title,
        "createdAt": o.created_at.isoformat() if o.created_at else None,
    }


@router.get("")
async def list_orders(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    rows = (
        await db.execute(
            select(Order)
            .where(or_(Order.buyer_id == user.id, Order.seller_id == user.id))
            .order_by(Order.created_at.desc())
            .limit(100)
        )
    ).scalars().all()
    return {"orders": [_order_out(o) for o in rows]}


@router.patch("/{order_id}")
async def update_order_status(
    order_id: int,
    body: OrderStatusUpdate,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    status = (body.status or "").strip().lower()
    if status not in ORDER_STATUSES:
        raise HTTPException(
            status_code=400,
            detail=f"status must be one of: {', '.join(ORDER_STATUSES)}",
        )
    order = await db.get(Order, order_id)
    if not order:
        raise HTTPException(status_code=404, detail="Order not found")
    if user.id != order.seller_id and (user.role or "user") != "admin":
        raise HTTPException(status_code=403, detail="Only the seller can update order status")

    order.status = status
    kind, title, default_body = STATUS_NOTIFY[status]
    label = order.product_title or "your item"
    body_text = f"{default_body.rstrip('.')} — {label}."

    await _notify(
        db,
        user_id=order.buyer_id,
        conversation_id=order.conversation_id,
        product_id=order.product_id,
        order_id=order.id,
        kind=kind,
        title=title,
        body=body_text,
    )
    await db.commit()
    return {"ok": True, "order": _order_out(order)}


@router.post("/from-product/{product_id}")
async def create_order_from_product(
    product_id: int,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Buyer confirms a purchase at listing price (no offer)."""
    product = await db.get(Product, product_id)
    if not product or not product.is_active:
        raise HTTPException(status_code=404, detail="Product not found")
    if product.seller_id == user.id:
        raise HTTPException(status_code=400, detail="Cannot order your own listing")

    order = Order(
        product_id=product.id,
        buyer_id=user.id,
        seller_id=product.seller_id,
        status="confirmed",
        amount=product.price,
        currency=product.currency,
        product_title=product.title,
    )
    db.add(order)
    await db.flush()

    await _notify(
        db,
        user_id=user.id,
        product_id=product.id,
        order_id=order.id,
        kind="order_confirmed",
        title="Order confirmed",
        body=f"Your order for {product.title} is confirmed.",
    )
    await _notify(
        db,
        user_id=product.seller_id,
        product_id=product.id,
        order_id=order.id,
        kind="order_confirmed",
        title="New order",
        body=f"New order for {product.title}.",
    )
    await db.commit()
    await db.refresh(order)
    return {"ok": True, "order": _order_out(order)}
