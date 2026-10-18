from decimal import Decimal, InvalidOperation

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.database import get_db
from app.deps import get_current_user
from app.models import ChatMessage, ChatNotification, Conversation, User
from app.services import chat as chat_service

router = APIRouter(prefix="/chat", tags=["chat"])


class OpenListingRequest(BaseModel):
    product_id: int = Field(alias="productId")
    message: str | None = None

    model_config = {"populate_by_name": True}


class OpenDisputeRequest(BaseModel):
    reason: str
    product_id: int | None = Field(default=None, alias="productId")

    model_config = {"populate_by_name": True}


class OpenPlBusinessRequest(BaseModel):
    pl_user_id: int | None = Field(default=None, alias="plUserId")

    model_config = {"populate_by_name": True}


class PostMessageRequest(BaseModel):
    body: str = ""
    kind: str = "text"
    offer_amount: Decimal | None = Field(default=None, alias="offerAmount")
    offer_currency: str | None = Field(default=None, alias="offerCurrency")

    model_config = {"populate_by_name": True}


class OfferActionRequest(BaseModel):
    action: str  # accepted | rejected


class BlockRequest(BaseModel):
    user_id: int = Field(alias="userId")

    model_config = {"populate_by_name": True}


class ReportRequest(BaseModel):
    reason: str


def _http(exc: chat_service.ChatError) -> HTTPException:
    return HTTPException(status_code=exc.status_code, detail=exc.message)


@router.get("/policy")
async def chat_policy():
    from app.chat_policy import CONVERSATION_KINDS, FORBIDDEN_FEATURES

    return {
        "allowedKinds": list(CONVERSATION_KINDS),
        "forbiddenFeatures": list(FORBIDDEN_FEATURES),
        "rules": {
            "listingAssignee": "PL for business listings; peer seller for peer listings",
            "businessOwner": "Can view and reply on business listing chats",
            "admin": "Only dispute, complaint, and business_admin channels — never listing chats",
            "noOpenDm": True,
        },
    }


@router.get("/inbox")
async def inbox(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    convs = await chat_service.list_conversations(db, user)
    unread_total = await chat_service.unread_count(db, user)
    # per-conversation unread approx from notifications
    items = []
    for c in convs:
        n = (
            await db.execute(
                select(ChatNotification.id).where(
                    ChatNotification.user_id == user.id,
                    ChatNotification.conversation_id == c.id,
                    ChatNotification.is_read.is_(False),
                )
            )
        ).scalars().all()
        items.append(
            await chat_service.conversation_to_dict_enriched(
                db, c, user, unread=len(n)
            )
        )
    return {"conversations": items, "unreadTotal": unread_total}


@router.post("/listing")
async def open_listing(
    body: OpenListingRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    try:
        conv = await chat_service.open_listing_chat(
            db, user, body.product_id, initial_message=body.message
        )
    except chat_service.ChatError as exc:
        raise _http(exc) from exc
    return await chat_service.conversation_to_dict_enriched(db, conv, user)


@router.post("/dispute")
async def open_dispute(
    body: OpenDisputeRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    try:
        conv = await chat_service.open_dispute(
            db, user, product_id=body.product_id, reason=body.reason
        )
    except chat_service.ChatError as exc:
        raise _http(exc) from exc
    return await chat_service.conversation_to_dict_enriched(db, conv, user)


@router.post("/pl-business")
async def open_pl_business(
    body: OpenPlBusinessRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    try:
        if (user.role or "user") == "business" and body.pl_user_id:
            conv = await chat_service.open_pl_business_chat_with_pl(
                db, user, body.pl_user_id
            )
        else:
            conv = await chat_service.open_pl_business_chat(db, user)
    except chat_service.ChatError as exc:
        raise _http(exc) from exc
    return await chat_service.conversation_to_dict_enriched(db, conv, user)


@router.post("/business-admin")
async def open_business_admin(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    try:
        conv = await chat_service.open_business_admin_chat(db, user)
    except chat_service.ChatError as exc:
        raise _http(exc) from exc
    return await chat_service.conversation_to_dict_enriched(db, conv, user)


@router.get("/conversations/{conversation_id}")
async def get_conversation(
    conversation_id: int,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    conv = await db.get(Conversation, conversation_id)
    if not conv:
        raise HTTPException(status_code=404, detail="Not found")
    try:
        await chat_service.require_access(db, conv, user)
        await chat_service.mark_read(db, user, conversation_id)
    except chat_service.ChatError as exc:
        raise _http(exc) from exc
    return await chat_service.conversation_to_dict_enriched(db, conv, user)


@router.get("/conversations/{conversation_id}/messages")
async def list_messages(
    conversation_id: int,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    limit: int = Query(100, ge=1, le=200),
):
    conv = await db.get(Conversation, conversation_id)
    if not conv:
        raise HTTPException(status_code=404, detail="Not found")
    try:
        await chat_service.require_access(db, conv, user)
    except chat_service.ChatError as exc:
        raise _http(exc) from exc
    rows = (
        await db.execute(
            select(ChatMessage)
            .where(ChatMessage.conversation_id == conversation_id)
            .order_by(ChatMessage.created_at.asc())
            .limit(limit)
        )
    ).scalars().all()
    try:
        await chat_service.mark_read(db, user, conversation_id)
    except chat_service.ChatError:
        pass
    meta = await chat_service.conversation_to_dict_enriched(db, conv, user)
    return {
        "messages": await chat_service.messages_to_dicts(db, list(rows)),
        "conversation": meta,
    }


@router.post("/conversations/{conversation_id}/messages")
async def send_message(
    conversation_id: int,
    body: PostMessageRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    try:
        amount = body.offer_amount
        if body.kind == "offer" and amount is None and body.body:
            # allow amount only via field
            pass
        msg = await chat_service.post_message(
            db,
            user,
            conversation_id,
            body=body.body,
            kind=body.kind,
            offer_amount=amount,
            offer_currency=body.offer_currency,
        )
    except chat_service.ChatError as exc:
        raise _http(exc) from exc
    except (InvalidOperation, TypeError) as exc:
        raise HTTPException(status_code=400, detail="Invalid offer amount") from exc
    return chat_service.message_to_dict(msg)


@router.post("/offers/{message_id}")
async def offer_action(
    message_id: int,
    body: OfferActionRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    try:
        msg = await chat_service.respond_offer(db, user, message_id, action=body.action)
    except chat_service.ChatError as exc:
        raise _http(exc) from exc
    return chat_service.message_to_dict(msg)


@router.post("/block")
async def block_user(
    body: BlockRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    try:
        await chat_service.block_user(db, user, body.user_id)
    except chat_service.ChatError as exc:
        raise _http(exc) from exc
    return {"ok": True}


@router.post("/conversations/{conversation_id}/report")
async def report(
    conversation_id: int,
    body: ReportRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    try:
        report = await chat_service.report_conversation(
            db, user, conversation_id, body.reason
        )
    except chat_service.ChatError as exc:
        raise _http(exc) from exc
    return {"ok": True, "id": report.id}


@router.get("/notifications")
async def notifications(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    rows = (
        await db.execute(
            select(ChatNotification)
            .where(ChatNotification.user_id == user.id)
            .order_by(ChatNotification.created_at.desc())
            .limit(80)
        )
    ).scalars().all()
    return {
        "notifications": [
            {
                "id": n.id,
                "kind": n.kind or "chat",
                "conversationId": n.conversation_id,
                "productId": n.product_id,
                "orderId": n.order_id,
                "title": n.title,
                "body": n.body,
                "isRead": n.is_read,
                "createdAt": n.created_at.isoformat() if n.created_at else None,
            }
            for n in rows
        ],
        "unreadTotal": await chat_service.unread_count(db, user),
    }


@router.post("/notifications/read")
async def mark_notifications_read(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    await db.execute(
        update(ChatNotification)
        .where(
            ChatNotification.user_id == user.id,
            ChatNotification.is_read.is_(False),
        )
        .values(is_read=True)
    )
    await db.commit()
    return {"ok": True}
