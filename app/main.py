from fastapi import FastAPI, Depends, Query
from sqlalchemy.orm import Session
from app.db.database import engine, Base, get_db
from app.db.models import Tenant, FAQ
from app.whatsapp.webhook import router as webhook_router

# Crea automaticamente le tabelle se non esistono
Base.metadata.create_all(bind=engine)

app = FastAPI(
    title="WhatsApp AI Booking System",
    description="Architettura Neuro-Simbolica a Sandwich a 3 Livelli (NLU -> Core Logic -> NLG)",
    version="1.0.0",
)

app.include_router(webhook_router, prefix="/webhook", tags=["Webhook"])


@app.get("/")
def root():
    return {
        "status": "online",
        "service": "WhatsApp AI Booking System",
        "architecture": "Neuro-Symbolic 3-Level Sandwich (NLU -> Core -> NLG)"
    }


@app.get("/seed")
def seed_tenant(
    phone_id: str = Query(..., description="WhatsApp Phone Number ID"),
    token: str = Query(..., description="WhatsApp Access Token"),
    tenant_name: str = Query("Studio Medico Rossi", description="Nome dello Studio"),
    db: Session = Depends(get_db),
):
    """
    Endpoint di inizializzazione per registrare/aggiornare il Tenant e le FAQ di test.
    """
    tenant = db.query(Tenant).filter(Tenant.whatsapp_phone_number_id == phone_id).first()
    if not tenant:
        tenant = db.query(Tenant).first()

    if not tenant:
        tenant = Tenant(
            name=tenant_name,
            title="Dott.",
            last_name="Rossi",
            whatsapp_phone_number_id=phone_id,
            whatsapp_access_token=token,
            work_start_time="09:00",
            work_end_time="17:00",
            working_days="mon,tue,wed,thu,fri",
            slot_duration_minutes=30,
            is_active=True,
        )
        db.add(tenant)
    else:
        tenant.whatsapp_phone_number_id = phone_id
        tenant.whatsapp_access_token = token
        tenant.name = tenant_name
        tenant.is_active = True

    db.commit()
    db.refresh(tenant)

    # Inserisci FAQ di test se non presenti
    faq = db.query(FAQ).filter(FAQ.tenant_id == tenant.id, FAQ.topic == "payment_methods").first()
    if not faq:
        faq = FAQ(
            tenant_id=tenant.id,
            topic="payment_methods",
            answer="Sì, in studio accettiamo sia bancomat che carte di credito (POS).",
        )
        db.add(faq)
        db.commit()

    return {
        "status": "ok",
        "message": f"Tenant '{tenant.name}' inizializzato con successo!",
        "tenant_id": tenant.id,
        "whatsapp_phone_number_id": tenant.whatsapp_phone_number_id,
    }

