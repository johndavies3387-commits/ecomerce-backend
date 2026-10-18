"""Chat business rules and helpers."""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy import and_, func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.chat_policy import CONVERSATION_KINDS, OFFER_STATUSES
from app.models import (
    Business,
    ChatBlock,
    ChatMessage,
    ChatNotification,
    ChatReport,
    Complaint,
    Conversation,
    ConversationMember,
    Order,
    Product,
    User,
)


class ChatError(Exception):
    def __init__(self, message: str, status_code: int = 400):
        self.message = message
        self.status_code = status_code
        super().__init__(message)


async def _is_blocked(db: AsyncSession, a: int, b: int) -> bool:
    row = (
        await db.execute(
            select(ChatBlock.id).where(
                or_(
                    and_(ChatBlock.blocker_id == a, ChatBlock.blocked_id == b),
                    and_(ChatBlock.blocker_id == b, ChatBlock.blocked_id == a),
                )
            )
        )
    ).first()
    return row is not None


async def _add_member(
    db: AsyncSession, conversation_id: int, user_id: int, member_role: str
) -> None:
    existing = (
        await db.execute(
            select(ConversationMember).where(
                ConversationMember.conversation_id == conversation_id,
                ConversationMember.user_id == user_id,
            )
        )
    ).scalar_one_or_none()
    if existing:
        return
    db.add(
        ConversationMember(
            conversation_id=conversation_id,
            user_id=user_id,
            member_role=member_role,
        )
    )


async def _notify(
    db: AsyncSession,
    *,
    user_id: int,
    title: str,
    body: str,
    kind: str = "chat",
    conversation_id: int | None = None,
    product_id: int | None = None,
    order_id: int | None = None,
) -> None:
    db.add(
        ChatNotification(
            user_id=user_id,
            conversation_id=conversation_id,
            product_id=product_id,
            order_id=order_id,
            kind=kind,
            title=title[:200],
            body=body[:500],
        )
    )


ADMIN_CHAT_KINDS = frozenset({"dispute", "complaint", "business_admin"})


def user_can_access(conv: Conversation, user: User, member_ids: set[int]) -> bool:
    if user.id in member_ids:
        return True
    role = user.role or "user"
    # Admin sees dispute / complaint / business_admin only (not listing product chats)
    if role == "admin" and conv.kind in ADMIN_CHAT_KINDS:
        return True
    return False


async def member_ids_for(db: AsyncSession, conversation_id: int) -> set[int]:
    rows = (
        await db.execute(
            select(ConversationMember.user_id).where(
                ConversationMember.conversation_id == conversation_id
            )
        )
    ).scalars().all()
    return set(rows)


async def require_access(db: AsyncSession, conv: Conversation, user: User) -> set[int]:
    ids = await member_ids_for(db, conv.id)
    role = user.role or "user"
    if role == "admin" and conv.kind in ADMIN_CHAT_KINDS:
        if user.id not in ids:
            await _add_member(db, conv.id, user.id, "admin")
            ids.add(user.id)
            await db.commit()
        return ids
    if user.id not in ids:
        raise ChatError("You are not part of this conversation", status_code=403)
    return ids


async def open_listing_chat(
    db: AsyncSession, buyer: User, product_id: int, *, initial_message: str | None
) -> Conversation:
    if (buyer.role or "user") == "admin":
        raise ChatError("Admin does not use listing chat", status_code=403)

    product = (
        await db.execute(
            select(Product)
            .options(selectinload(Product.seller), selectinload(Product.listed_by))
            .where(Product.id == product_id, Product.is_active.is_(True))
        )
    ).scalar_one_or_none()
    if not product:
        raise ChatError("Product not found", status_code=404)

    # No open DMs without product — always product-scoped
    if product.seller_id == buyer.id or product.listed_by_id == buyer.id:
        raise ChatError("You cannot message yourself on your own listing", status_code=400)

    # Business listing → PL answers; peer listing → seller
    if product.business_id:
        assignee_id = product.listed_by_id or product.seller_id
        owner_id = product.seller_id
        business_id = product.business_id
    else:
        assignee_id = product.seller_id
        owner_id = None
        business_id = None

    if not assignee_id:
        raise ChatError("No seller assignee for this listing", status_code=400)

    if await _is_blocked(db, buyer.id, assignee_id):
        raise ChatError("Messaging is blocked with this user", status_code=403)

    # Zone soft rule: warn via subject only; still allow (policy can tighten later)
    existing = (
        await db.execute(
            select(Conversation).where(
                Conversation.kind == "listing",
                Conversation.product_id == product_id,
                Conversation.buyer_id == buyer.id,
            )
        )
    ).scalar_one_or_none()
    if existing:
        return existing

    conv = Conversation(
        kind="listing",
        product_id=product.id,
        business_id=business_id,
        buyer_id=buyer.id,
        assignee_id=assignee_id,
        owner_id=owner_id,
        admin_participant=False,
        status="open",
        subject=product.title[:255],
        last_message_at=datetime.now(timezone.utc),
    )
    db.add(conv)
    await db.flush()

    await _add_member(db, conv.id, buyer.id, "buyer")
    await _add_member(db, conv.id, assignee_id, "assignee")
    if owner_id and owner_id != assignee_id:
        await _add_member(db, conv.id, owner_id, "viewer")

    text = (initial_message or "").strip() or f"Hi, I'm interested in “{product.title}”."
    db.add(
        ChatMessage(
            conversation_id=conv.id,
            sender_id=buyer.id,
            kind="text",
            body=text,
        )
    )
    await _notify(
        db,
        user_id=assignee_id,
        conversation_id=conv.id,
        title="New listing message",
        body=text[:120],
    )
    if owner_id and owner_id != assignee_id:
        await _notify(
            db,
            user_id=owner_id,
            conversation_id=conv.id,
            title="Listing chat (viewer)",
            body=f"Buyer messaged about {product.title}"[:120],
        )

    await db.commit()
    await db.refresh(conv)
    return conv


async def open_pl_business_chat(db: AsyncSession, user: User) -> Conversation:
    role = user.role or "user"
    if role == "pl":
        if not user.business_id:
            raise ChatError("PL is not linked to a business", status_code=400)
        business = await db.get(Business, user.business_id)
        if not business:
            raise ChatError("Business not found", status_code=404)
        pl_id, owner_id = user.id, business.owner_user_id
        business_id = business.id
    elif role == "business":
        business = (
            await db.execute(select(Business).where(Business.owner_user_id == user.id))
        ).scalar_one_or_none()
        if not business:
            raise ChatError("No business linked", status_code=404)
        # Owner opens team channel — needs a PL id? Use owner as both for now
        # Better: require plUserId. For owner-initiated, find first PL or create placeholder with owner+self
        raise ChatError(
            "Open PL team chat from a PL account, or pass plUserId",
            status_code=400,
        )
    else:
        raise ChatError("Only PL can open business team chat", status_code=403)

    if await _is_blocked(db, pl_id, owner_id):
        raise ChatError("Messaging is blocked", status_code=403)

    existing = (
        await db.execute(
            select(Conversation).where(
                Conversation.kind == "pl_business",
                Conversation.business_id == business_id,
                Conversation.assignee_id == pl_id,
                Conversation.owner_id == owner_id,
            )
        )
    ).scalar_one_or_none()
    if existing:
        return existing

    conv = Conversation(
        kind="pl_business",
        business_id=business_id,
        assignee_id=pl_id,
        owner_id=owner_id,
        status="open",
        subject="Team chat",
        last_message_at=datetime.now(timezone.utc),
    )
    db.add(conv)
    await db.flush()
    await _add_member(db, conv.id, pl_id, "assignee")
    await _add_member(db, conv.id, owner_id, "viewer")
    db.add(
        ChatMessage(
            conversation_id=conv.id,
            sender_id=user.id,
            kind="system",
            body="Team chat started between PL and business owner.",
        )
    )
    await db.commit()
    await db.refresh(conv)
    return conv


async def open_pl_business_chat_with_pl(
    db: AsyncSession, owner: User, pl_user_id: int
) -> Conversation:
    if (owner.role or "user") != "business":
        raise ChatError("Only business owners can open chat with a PL", status_code=403)
    business = (
        await db.execute(select(Business).where(Business.owner_user_id == owner.id))
    ).scalar_one_or_none()
    if not business:
        raise ChatError("No business linked", status_code=404)
    pl = await db.get(User, pl_user_id)
    if not pl or pl.role != "pl" or pl.business_id != business.id:
        raise ChatError("PL must belong to your business", status_code=400)

    existing = (
        await db.execute(
            select(Conversation).where(
                Conversation.kind == "pl_business",
                Conversation.business_id == business.id,
                Conversation.assignee_id == pl.id,
                Conversation.owner_id == owner.id,
            )
        )
    ).scalar_one_or_none()
    if existing:
        return existing

    conv = Conversation(
        kind="pl_business",
        business_id=business.id,
        assignee_id=pl.id,
        owner_id=owner.id,
        status="open",
        subject=f"Team · {pl.display_name or pl.email}",
        last_message_at=datetime.now(timezone.utc),
    )
    db.add(conv)
    await db.flush()
    await _add_member(db, conv.id, pl.id, "assignee")
    await _add_member(db, conv.id, owner.id, "viewer")
    db.add(
        ChatMessage(
            conversation_id=conv.id,
            sender_id=owner.id,
            kind="system",
            body="Team chat started.",
        )
    )
    await db.commit()
    await db.refresh(conv)
    return conv


async def open_business_admin_chat(db: AsyncSession, user: User) -> Conversation:
    if (user.role or "user") != "business":
        raise ChatError("Only business owners can contact admin this way", status_code=403)
    business = (
        await db.execute(select(Business).where(Business.owner_user_id == user.id))
    ).scalar_one_or_none()
    if not business:
        raise ChatError("No business linked", status_code=404)

    existing = (
        await db.execute(
            select(Conversation).where(
                Conversation.kind == "business_admin",
                Conversation.business_id == business.id,
                Conversation.owner_id == user.id,
                Conversation.status == "open",
            )
        )
    ).scalar_one_or_none()
    if existing:
        return existing

    conv = Conversation(
        kind="business_admin",
        business_id=business.id,
        owner_id=user.id,
        admin_participant=True,
        status="open",
        subject=f"Support · {business.name}",
        last_message_at=datetime.now(timezone.utc),
    )
    db.add(conv)
    await db.flush()
    await _add_member(db, conv.id, user.id, "viewer")
    db.add(
        ChatMessage(
            conversation_id=conv.id,
            sender_id=user.id,
            kind="system",
            body="Business support chat opened. An admin can join and reply.",
        )
    )
    # Notify all admins
    admins = (
        await db.execute(select(User).where(User.role == "admin"))
    ).scalars().all()
    for admin in admins:
        await _add_member(db, conv.id, admin.id, "admin")
        await _notify(
            db,
            user_id=admin.id,
            conversation_id=conv.id,
            title="Business support",
            body=f"{business.name} contacted admin",
        )
    await db.commit()
    await db.refresh(conv)
    return conv


async def open_dispute(
    db: AsyncSession,
    user: User,
    *,
    product_id: int | None,
    reason: str,
) -> Conversation:
    reason = reason.strip()
    if len(reason) < 5:
        raise ChatError("Describe the dispute (at least 5 characters)")

    product = None
    if product_id:
        product = await db.get(Product, product_id)

    conv = Conversation(
        kind="dispute",
        product_id=product.id if product else None,
        business_id=product.business_id if product else None,
        buyer_id=user.id,
        assignee_id=product.listed_by_id or product.seller_id if product else None,
        owner_id=product.seller_id if product and product.business_id else None,
        admin_participant=True,
        status="open",
        subject=(f"Dispute · {product.title}" if product else "Dispute")[:255],
        last_message_at=datetime.now(timezone.utc),
    )
    db.add(conv)
    await db.flush()
    await _add_member(db, conv.id, user.id, "buyer")
    if conv.assignee_id and conv.assignee_id != user.id:
        await _add_member(db, conv.id, conv.assignee_id, "assignee")
    if conv.owner_id and conv.owner_id not in {user.id, conv.assignee_id}:
        await _add_member(db, conv.id, conv.owner_id, "viewer")

    admins = (
        await db.execute(select(User).where(User.role == "admin"))
    ).scalars().all()
    for admin in admins:
        await _add_member(db, conv.id, admin.id, "admin")
        await _notify(
            db,
            user_id=admin.id,
            conversation_id=conv.id,
            title="New dispute",
            body=reason[:120],
        )

    db.add(
        ChatMessage(
            conversation_id=conv.id,
            sender_id=user.id,
            kind="system",
            body=f"Dispute opened: {reason}",
        )
    )
    await db.commit()
    await db.refresh(conv)
    return conv


async def open_complaint_chat(
    db: AsyncSession,
    user: User,
    *,
    category: str,
    subject: str | None,
    body: str,
    notify: bool = True,
) -> Conversation:
    """Create an admin-visible complaint thread (same reply path as disputes)."""
    label = (subject or "").strip() or f"Complaint · {category}"
    conv = Conversation(
        kind="complaint",
        buyer_id=user.id,
        admin_participant=True,
        status="open",
        subject=label[:255],
        last_message_at=datetime.now(timezone.utc),
    )
    db.add(conv)
    await db.flush()
    await _add_member(db, conv.id, user.id, "buyer")

    admins = (
        await db.execute(
            select(User).where(
                User.role == "admin",
                User.is_active.is_(True),
                User.deleted_at.is_(None),
            )
        )
    ).scalars().all()
    preview = body.strip()[:120]
    for admin in admins:
        await _add_member(db, conv.id, admin.id, "admin")
        if notify:
            await _notify(
                db,
                user_id=admin.id,
                conversation_id=conv.id,
                kind="complaint",
                title="New complaint",
                body=f"[{category}] {preview}",
            )

    db.add(
        ChatMessage(
            conversation_id=conv.id,
            sender_id=user.id,
            kind="text",
            body=body.strip(),
        )
    )
    await db.flush()
    return conv


async def ensure_complaint_conversation(
    db: AsyncSession, complaint: Complaint, *, notify: bool = False
) -> Conversation | None:
    """Link a Complaint row to a chat thread (backfill for inbox Complaints tab)."""
    if complaint.conversation_id:
        conv = await db.get(Conversation, complaint.conversation_id)
        if conv:
            # Ensure complainant is still a member
            await _add_member(db, conv.id, complaint.user_id, "buyer")
            return conv

    owner = await db.get(User, complaint.user_id)
    if not owner:
        return None

    conv = await open_complaint_chat(
        db,
        owner,
        category=complaint.category,
        subject=complaint.subject,
        body=complaint.body,
        notify=notify,
    )
    complaint.conversation_id = conv.id
    await db.flush()
    return conv


async def sync_user_complaint_threads(db: AsyncSession, user: User) -> None:
    """Create missing chat threads for the user's complaints so Inbox can list them."""
    role = user.role or "user"
    if role == "admin":
        stmt = (
            select(Complaint)
            .order_by(Complaint.created_at.desc())
            .limit(100)
        )
    else:
        stmt = (
            select(Complaint)
            .where(Complaint.user_id == user.id)
            .order_by(Complaint.created_at.desc())
            .limit(50)
        )
    rows = list((await db.execute(stmt)).scalars().all())
    if not rows:
        return
    for c in rows:
        conv = await ensure_complaint_conversation(db, c, notify=False)
        if not conv:
            continue
        if role == "admin":
            await _add_member(db, conv.id, user.id, "admin")
        else:
            await _add_member(db, conv.id, user.id, "buyer")
    await db.commit()


async def list_conversations(db: AsyncSession, user: User) -> list[Conversation]:
    """Return every conversation this user should see (member or party on the thread)."""
    # Complaints live in their own table — attach chat threads before listing inbox
    await sync_user_complaint_threads(db, user)

    role = user.role or "user"
    member_ids = select(ConversationMember.conversation_id).where(
        ConversationMember.user_id == user.id
    )

    if role == "admin":
        # Admins: disputes + complaints + business support + joined threads
        stmt = (
            select(Conversation)
            .where(
                or_(
                    Conversation.kind.in_(["dispute", "complaint", "business_admin"]),
                    Conversation.id.in_(member_ids),
                )
            )
            .order_by(Conversation.last_message_at.desc().nullslast())
        )
    else:
        # Buyers / sellers / PLs / owners: membership OR role on the conversation
        stmt = (
            select(Conversation)
            .where(
                or_(
                    Conversation.id.in_(member_ids),
                    Conversation.buyer_id == user.id,
                    Conversation.assignee_id == user.id,
                    Conversation.owner_id == user.id,
                )
            )
            .order_by(Conversation.last_message_at.desc().nullslast())
        )

    convs = list((await db.execute(stmt)).scalars().unique().all())

    # Backfill membership so access checks stay consistent
    for conv in convs:
        ids = await member_ids_for(db, conv.id)
        if user.id in ids:
            continue
        role_label = "buyer"
        if user.id == conv.assignee_id:
            role_label = "assignee"
        elif user.id == conv.owner_id:
            role_label = "viewer"
        elif (user.role or "user") == "admin":
            role_label = "admin"
        await _add_member(db, conv.id, user.id, role_label)
    if convs:
        await db.commit()

    return convs


async def last_message_preview(db: AsyncSession, conversation_id: int) -> str | None:
    row = (
        await db.execute(
            select(ChatMessage)
            .where(ChatMessage.conversation_id == conversation_id)
            .order_by(ChatMessage.created_at.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    if not row:
        return None
    body = (row.body or "").strip()
    return body[:140] if body else None


async def post_message(
    db: AsyncSession,
    user: User,
    conversation_id: int,
    *,
    body: str,
    kind: str = "text",
    offer_amount: Decimal | None = None,
    offer_currency: str | None = None,
) -> ChatMessage:
    conv = await db.get(Conversation, conversation_id)
    if not conv:
        raise ChatError("Conversation not found", status_code=404)
    if conv.status != "open":
        raise ChatError("Conversation is closed", status_code=400)

    await require_access(db, conv, user)

    # Admin must not participate in listing chats
    if (user.role or "user") == "admin" and conv.kind == "listing":
        raise ChatError("Admin does not join listing product chats", status_code=403)

    body = (body or "").strip()
    if kind == "offer":
        if conv.kind != "listing":
            raise ChatError("Offers are only allowed on listing chats")
        if conv.buyer_id != user.id:
            raise ChatError("Only the buyer can make an offer", status_code=403)
        if offer_amount is None or offer_amount <= 0:
            raise ChatError("Offer amount must be greater than 0")
        currency = (offer_currency or "PKR").upper()
        body = body or f"Offer: {currency} {offer_amount}"
        msg = ChatMessage(
            conversation_id=conv.id,
            sender_id=user.id,
            kind="offer",
            body=body,
            offer_amount=offer_amount,
            offer_currency=currency,
            offer_status="pending",
        )
    else:
        if len(body) < 1:
            raise ChatError("Message cannot be empty")
        msg = ChatMessage(
            conversation_id=conv.id,
            sender_id=user.id,
            kind="text",
            body=body,
        )

    db.add(msg)
    conv.last_message_at = datetime.now(timezone.utc)

    # Notify other members
    members = await member_ids_for(db, conv.id)
    notify_kind = "offer" if kind == "offer" else "chat"
    notify_title = "New offer" if kind == "offer" else "New message"
    for uid in members:
        if uid == user.id:
            continue
        await _notify(
            db,
            user_id=uid,
            conversation_id=conv.id,
            product_id=conv.product_id,
            kind=notify_kind,
            title=notify_title,
            body=body[:120],
        )

    await db.commit()
    await db.refresh(msg)
    return msg


async def respond_offer(
    db: AsyncSession, user: User, message_id: int, *, action: str
) -> ChatMessage:
    if action not in {"accepted", "rejected"}:
        raise ChatError("action must be accepted or rejected")
    msg = await db.get(ChatMessage, message_id)
    if not msg or msg.kind != "offer":
        raise ChatError("Offer not found", status_code=404)
    if msg.offer_status != "pending":
        raise ChatError("Offer is no longer pending")
    conv = await db.get(Conversation, msg.conversation_id)
    if not conv:
        raise ChatError("Conversation not found", status_code=404)
    await require_access(db, conv, user)

    # Assignee or business owner can respond
    if user.id not in {conv.assignee_id, conv.owner_id}:
        raise ChatError("Only the seller side can respond to offers", status_code=403)

    msg.offer_status = action
    system = ChatMessage(
        conversation_id=conv.id,
        sender_id=user.id,
        kind="system",
        body=f"Offer {action}: {msg.offer_currency} {msg.offer_amount}",
    )
    db.add(system)
    conv.last_message_at = datetime.now(timezone.utc)

    if conv.buyer_id:
        await _notify(
            db,
            user_id=conv.buyer_id,
            conversation_id=conv.id,
            product_id=conv.product_id,
            kind="offer_accepted" if action == "accepted" else "offer_rejected",
            title="Offer accepted" if action == "accepted" else "Offer rejected",
            body=system.body,
        )

    if action == "accepted" and conv.buyer_id:
        product = await db.get(Product, conv.product_id) if conv.product_id else None
        seller_id = (
            (product.seller_id if product else None)
            or conv.owner_id
            or conv.assignee_id
            or user.id
        )
        order = Order(
            product_id=conv.product_id,
            buyer_id=conv.buyer_id,
            seller_id=seller_id,
            conversation_id=conv.id,
            status="confirmed",
            amount=msg.offer_amount,
            currency=msg.offer_currency,
            product_title=product.title if product else conv.subject,
        )
        db.add(order)
        await db.flush()
        conv.order_id = order.id
        await _notify(
            db,
            user_id=conv.buyer_id,
            conversation_id=conv.id,
            product_id=conv.product_id,
            order_id=order.id,
            kind="order_confirmed",
            title="Order confirmed",
            body=f"Your order for {order.product_title or 'item'} is confirmed.",
        )
        await _notify(
            db,
            user_id=seller_id,
            conversation_id=conv.id,
            product_id=conv.product_id,
            order_id=order.id,
            kind="order_confirmed",
            title="New order",
            body=f"Order confirmed for {order.product_title or 'item'}.",
        )

    await db.commit()
    await db.refresh(msg)
    return msg


async def block_user(db: AsyncSession, blocker: User, blocked_id: int) -> None:
    if blocker.id == blocked_id:
        raise ChatError("Cannot block yourself")
    existing = (
        await db.execute(
            select(ChatBlock).where(
                ChatBlock.blocker_id == blocker.id, ChatBlock.blocked_id == blocked_id
            )
        )
    ).scalar_one_or_none()
    if existing:
        return
    db.add(ChatBlock(blocker_id=blocker.id, blocked_id=blocked_id))
    await db.commit()


async def report_conversation(
    db: AsyncSession, reporter: User, conversation_id: int, reason: str
) -> ChatReport:
    reason = reason.strip()
    if len(reason) < 3:
        raise ChatError("Reason required")
    conv = await db.get(Conversation, conversation_id)
    if not conv:
        raise ChatError("Conversation not found", status_code=404)
    await require_access(db, conv, reporter)
    report = ChatReport(
        reporter_id=reporter.id,
        conversation_id=conversation_id,
        reason=reason,
    )
    db.add(report)
    await db.commit()
    await db.refresh(report)
    return report


async def mark_read(db: AsyncSession, user: User, conversation_id: int) -> None:
    member = (
        await db.execute(
            select(ConversationMember).where(
                ConversationMember.conversation_id == conversation_id,
                ConversationMember.user_id == user.id,
            )
        )
    ).scalar_one_or_none()
    if not member:
        raise ChatError("Not a member", status_code=403)
    member.last_read_at = datetime.now(timezone.utc)
    await db.execute(
        update(ChatNotification)
        .where(
            ChatNotification.user_id == user.id,
            ChatNotification.conversation_id == conversation_id,
        )
        .values(is_read=True)
    )
    await db.commit()


async def unread_count(db: AsyncSession, user: User) -> int:
    count = (
        await db.execute(
            select(func.count(ChatNotification.id)).where(
                ChatNotification.user_id == user.id,
                ChatNotification.is_read.is_(False),
            )
        )
    ).scalar_one()
    return int(count or 0)


def _user_label(u: User | None) -> str | None:
    if not u:
        return None
    return (u.display_name or "").strip() or u.email


def conversation_to_dict(conv: Conversation, *, unread: int = 0) -> dict:
    return {
        "id": conv.id,
        "kind": conv.kind,
        "productId": conv.product_id,
        "orderId": conv.order_id,
        "businessId": conv.business_id,
        "buyerId": conv.buyer_id,
        "assigneeId": conv.assignee_id,
        "ownerId": conv.owner_id,
        "adminParticipant": bool(conv.admin_participant),
        "status": conv.status,
        "subject": conv.subject,
        "lastMessageAt": conv.last_message_at.isoformat() if conv.last_message_at else None,
        "createdAt": conv.created_at.isoformat() if conv.created_at else None,
        "unread": unread,
        "allowedKinds": list(CONVERSATION_KINDS),
        "forbiddenFeatures": [
            "voice",
            "video",
            "group_chat",
            "admin_in_listing_chat",
            "open_dm_without_product",
        ],
    }


async def conversation_to_dict_enriched(
    db: AsyncSession, conv: Conversation, viewer: User, *, unread: int = 0
) -> dict:
    """Add peerName / productTitle / lastMessage for UI headers."""
    data = conversation_to_dict(conv, unread=unread)
    peer_id: int | None = None
    if conv.kind == "listing":
        if viewer.id == conv.buyer_id:
            peer_id = conv.assignee_id or conv.owner_id
        else:
            peer_id = conv.buyer_id
        data["productTitle"] = conv.subject
    elif conv.kind in {"dispute", "complaint", "order"}:
        if viewer.id == conv.buyer_id:
            peer_id = conv.assignee_id
        else:
            peer_id = conv.buyer_id
    elif conv.kind == "pl_business":
        peer_id = conv.owner_id if viewer.id == conv.assignee_id else conv.assignee_id
    elif conv.kind == "business_admin":
        # other party: any admin or the business owner
        if (viewer.role or "user") == "admin":
            peer_id = conv.owner_id or conv.buyer_id
        else:
            peer_id = None  # title stays subject

    peer = await db.get(User, peer_id) if peer_id else None
    data["peerId"] = peer_id
    data["peerName"] = _user_label(peer)
    # Dispute / complaint: buyer talks with admin support when no seller assignee
    if conv.kind in {"dispute", "complaint"} and not data["peerName"]:
        if (viewer.role or "user") == "admin":
            buyer = await db.get(User, conv.buyer_id) if conv.buyer_id else None
            data["peerName"] = _user_label(buyer) or "User"
        else:
            data["peerName"] = "Admin"
    if data.get("peerName") and data.get("productTitle"):
        data["title"] = f"{data['peerName']} · {data['productTitle']}"
    elif conv.kind in {"dispute", "complaint"} and conv.subject:
        data["title"] = conv.subject
    elif data.get("peerName"):
        data["title"] = data["peerName"]
    else:
        data["title"] = conv.subject or f"Chat #{conv.id}"
    data["lastMessage"] = await last_message_preview(db, conv.id)
    return data


def message_to_dict(msg: ChatMessage, *, sender_name: str | None = None) -> dict:
    return {
        "id": msg.id,
        "conversationId": msg.conversation_id,
        "senderId": msg.sender_id,
        "senderName": sender_name,
        "kind": msg.kind,
        "body": msg.body,
        "offerAmount": float(msg.offer_amount) if msg.offer_amount is not None else None,
        "offerCurrency": msg.offer_currency,
        "offerStatus": msg.offer_status,
        "createdAt": msg.created_at.isoformat() if msg.created_at else None,
        "offerStatuses": list(OFFER_STATUSES),
    }


async def messages_to_dicts(db: AsyncSession, rows: list[ChatMessage]) -> list[dict]:
    ids = {m.sender_id for m in rows if m.sender_id}
    names: dict[int, str] = {}
    if ids:
        users = (
            await db.execute(select(User).where(User.id.in_(ids)))
        ).scalars().all()
        names = {u.id: _user_label(u) or f"User #{u.id}" for u in users}
    return [
        message_to_dict(m, sender_name=names.get(m.sender_id))
        for m in rows
    ]
