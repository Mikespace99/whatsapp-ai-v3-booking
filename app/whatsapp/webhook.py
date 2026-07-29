"""
FastAPI Router per Webhook Meta WhatsApp.
"""

from fastapi import APIRouter, Request, Depends, Response
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool

from app.db.database import get_db
from app.db.models import Tenant, UserSession
from app.config import settings
from app.ai.nlu import parse_user_message
from app.ai.nlg import generate_user_message
from app.core.state_machine import BookingStateMachine
from app.whatsapp.sender import send_whatsapp_message

router = APIRouter()
state_machine = BookingStateMachine()


@router.get("")
async def verify_webhook(request: Request):
    params = request.query_params
    mode = params.get("hub.mode")
    token = params.get("hub.verify_token")
    challenge = params.get("hub.challenge")

    if mode and token:
        if mode == "subscribe" and token == settings.VERIFY_TOKEN:
            return Response(content=challenge, media_type="text/plain")
        return Response(content="Forbidden", status_code=403)

    return Response(content="Bad Request", status_code=400)


@router.post("")
async def whatsapp_webhook(request: Request, db: Session = Depends(get_db)):
    try:
        data = await request.json()
    except Exception:
        return {"status": "error", "message": "Invalid JSON"}

    entry = data.get("entry", [])
    if not entry:
        return {"status": "ok"}

    changes = entry[0].get("changes", [])
    if not changes:
        return {"status": "ok"}

    value = changes[0].get("value", {})
    messages = value.get("messages", [])

    if not messages:
        return {"status": "ok"}

    metadata = value.get("metadata", {})
    recipient_phone_id = metadata.get("phone_number_id")

    tenant = db.query(Tenant).filter(
        Tenant.whatsapp_phone_number_id == recipient_phone_id,
        Tenant.is_active == True
    ).first()

    if not tenant:
        # Fallback al primo tenant se non trovato per test locale
        tenant = db.query(Tenant).filter(Tenant.is_active == True).first()

    if not tenant:
        return {"status": "tenant_not_found"}

    message_obj = messages[0]
    sender_phone = message_obj.get("from")
    message_type = message_obj.get("type")

    if message_type == "text":
        message_body = message_obj.get("text", {}).get("body", "")

        # Esegui la pipeline a 3 livelli in un threadpool
        reply = await run_in_threadpool(
            process_booking_pipeline, sender_phone, message_body, tenant, db
        )

        return {"status": "ok", "reply_sent": reply}

    return {"status": "ok", "message": "Ignored non-text message"}


def process_booking_pipeline(
    sender_phone: str,
    message_body: str,
    tenant: Tenant,
    db: Session,
) -> str:
    """
    Esegue l'intero ciclo a Sandwich (3 Livelli):
      1. NLU (Livello 1 - AI): messaggio -> JSON intent/entities
      2. Core State Machine (Livello 2 - SW Deterministico): JSON -> directive_json
      3. NLG (Livello 3 - AI): directive_json -> risposta WhatsApp
    """
    # 1. Recupera o crea sessione
    session = (
        db.query(UserSession)
        .filter(
            UserSession.tenant_id == tenant.id,
            UserSession.customer_phone == sender_phone,
        )
        .first()
    )
    if not session:
        session = UserSession(
            tenant_id=tenant.id,
            customer_phone=sender_phone,
            state="GATHERING_REQUIREMENTS",
            conv_data=None,
        )
        db.add(session)
        db.commit()
        db.refresh(session)

    # LIVELLO 1: NLU — 1 chiamata LLM
    nlu_result = parse_user_message(
        message_text=message_body,
        current_state=session.state,
    )

    # LIVELLO 2: Core State Machine — Software Deterministico
    directive_json = state_machine.process_turn(
        nlu_result=nlu_result,
        session=session,
        tenant=tenant,
        db=db,
        customer_phone=sender_phone,
    )

    # LIVELLO 3: NLG — 1 chiamata LLM
    response_text = generate_user_message(
        directive=directive_json,
        tenant=tenant,
        user_message_text=message_body,
    )

    # Invia risposta WhatsApp
    send_whatsapp_message(
        to=sender_phone,
        text=response_text,
        token=tenant.whatsapp_access_token or "",
        phone_id=tenant.whatsapp_phone_number_id or "",
    )

    return response_text
