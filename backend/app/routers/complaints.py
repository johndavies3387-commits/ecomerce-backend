from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.deps import get_current_user, require_roles
from app.models import ChatMessage, Complaint, User
from app.services import chat as chat_service
from app.services.chat import _notify

router = APIRouter(prefix="/complaints", tags=["complaints"])

CATEGORIES = ("user", "suggestion", "difficulty", "other")
STATUSES = ("open", "reviewing", "resolved", "closed")


class ComplaintCreate(BaseModel):
    category: str = "other"
    subject: str | None = None
    body: str
    reported_user_id: int | None = Field(default=None, alias="reportedUserId")

    model_config = {"populate_by_name": True}


class ComplaintStatusUpdate(BaseModel):
    status: str
    admin_note: str | None = Field(default=None, alias="adminNote")

    model_config = {"populate_by_name": True}


def _out(c: Complaint, *, user_email: str | None = None) -> dict:
    return {
        "id": c.id,
        "userId": c.user_id,
        "userEmail": user_email,
        "conversationId": c.conversation_id,
        "category": c.category,
        "subject": c.subject,
        "body": c.body,
        "reportedUserId": c.reported_user_id,
        "status": c.status,
        "adminNote": c.admin_note,
        "createdAt": c.created_at.isoformat() if c.created_at else None,
    }


@router.post("")
async def create_complaint(
    body: ComplaintCreate,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    category = (body.category or "other").strip().lower()
    if category not in CATEGORIES:
        raise HTTPException(
            status_code=400,
            detail=f"category must be one of: {', '.join(CATEGORIES)}",
        )
    text = (body.body or "").strip()
    if len(text) < 5:
        raise HTTPException(status_code=400, detail="Please describe your complaint (min 5 characters)")

    subject = (body.subject or "").strip() or None
    if subject and len(subject) > 200:
        subject = subject[:200]

    reported_id = body.reported_user_id
    if reported_id is not None:
        if reported_id == user.id:
            raise HTTPException(status_code=400, detail="Cannot report yourself")
        reported = await db.get(User, reported_id)
        if not reported or reported.deleted_at is not None:
            raise HTTPException(status_code=404, detail="Reported user not found")

    conv = await chat_service.open_complaint_chat(
        db,
        user,
        category=category,
        subject=subject,
        body=text,
    )

    complaint = Complaint(
        user_id=user.id,
        conversation_id=conv.id,
        category=category,
        subject=subject,
        body=text,
        reported_user_id=reported_id,
        status="open",
    )
    db.add(complaint)
    await db.commit()
    await db.refresh(complaint)
    await db.refresh(conv)
    return {
        "ok": True,
        "complaint": _out(complaint, user_email=user.email),
        "conversation": await chat_service.conversation_to_dict_enriched(db, conv, user),
    }


@router.get("/mine")
async def my_complaints(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    rows = list(
        (
            await db.execute(
                select(Complaint)
                .where(Complaint.user_id == user.id)
                .order_by(Complaint.created_at.desc())
                .limit(50)
            )
        ).scalars().all()
    )
    dirty = False
    for c in rows:
        if not c.conversation_id:
            await chat_service.ensure_complaint_conversation(db, c, notify=False)
            dirty = True
    if dirty:
        await db.commit()
        for c in rows:
            await db.refresh(c)
    return {"complaints": [_out(c) for c in rows]}


@router.get("")
async def list_complaints(
    _: User = Depends(require_roles("admin")),
    db: AsyncSession = Depends(get_db),
):
    rows = list(
        (
            await db.execute(select(Complaint).order_by(Complaint.created_at.desc()).limit(100))
        ).scalars().all()
    )
    dirty = False
    for c in rows:
        if not c.conversation_id:
            await chat_service.ensure_complaint_conversation(db, c, notify=False)
            dirty = True
    if dirty:
        await db.commit()
        for c in rows:
            await db.refresh(c)

    user_ids = {c.user_id for c in rows}
    emails: dict[int, str] = {}
    if user_ids:
        users = (
            await db.execute(select(User).where(User.id.in_(user_ids)))
        ).scalars().all()
        emails = {u.id: u.email for u in users}
    return {
        "complaints": [_out(c, user_email=emails.get(c.user_id)) for c in rows]
    }


@router.patch("/{complaint_id}")
async def update_complaint(
    complaint_id: int,
    body: ComplaintStatusUpdate,
    admin: User = Depends(require_roles("admin")),
    db: AsyncSession = Depends(get_db),
):
    status = (body.status or "").strip().lower()
    if status not in STATUSES:
        raise HTTPException(
            status_code=400,
            detail=f"status must be one of: {', '.join(STATUSES)}",
        )
    complaint = await db.get(Complaint, complaint_id)
    if not complaint:
        raise HTTPException(status_code=404, detail="Complaint not found")

    complaint.status = status
    note = None
    if body.admin_note is not None:
        note = body.admin_note.strip() or None
        complaint.admin_note = note

    conv = await chat_service.ensure_complaint_conversation(db, complaint, notify=False)
    if conv:
        if note:
            db.add(
                ChatMessage(
                    conversation_id=conv.id,
                    sender_id=admin.id,
                    kind="text",
                    body=note,
                )
            )
        else:
            db.add(
                ChatMessage(
                    conversation_id=conv.id,
                    sender_id=admin.id,
                    kind="system",
                    body=f"Complaint marked {status}.",
                )
            )
        conv.last_message_at = datetime.now(timezone.utc)
        if status in {"resolved", "closed"}:
            conv.status = "closed" if status == "closed" else conv.status

    await _notify(
        db,
        user_id=complaint.user_id,
        conversation_id=complaint.conversation_id,
        kind="complaint",
        title=f"Complaint {status}",
        body=note or f"Your complaint is now marked {status}.",
    )
    await db.commit()
    await db.refresh(complaint)
    return {"ok": True, "complaint": _out(complaint)}
