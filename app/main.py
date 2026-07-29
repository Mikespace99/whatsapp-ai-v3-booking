import os
import json
import requests
from typing import Optional
from fastapi import FastAPI, Depends, Query, Request
from fastapi.responses import RedirectResponse, HTMLResponse
from sqlalchemy.orm import Session
from google_auth_oauthlib.flow import Flow

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
    token: Optional[str] = Query(None, description="WhatsApp Access Token"),
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
            whatsapp_access_token=token or "",
            work_start_time="09:00",
            work_end_time="17:00",
            working_days="mon,tue,wed,thu,fri",
            slot_duration_minutes=30,
            is_active=True,
        )
        db.add(tenant)
    else:
        tenant.whatsapp_phone_number_id = phone_id
        if token:
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


@app.get("/subscribe-waba")
def subscribe_waba(
    waba_id: str = Query(..., description="WhatsApp Business Account ID"),
    token: Optional[str] = Query(None, description="Meta System/User Access Token"),
):
    """
    Iscrive l'account WABA ai webhook di Meta via Graph API.
    """
    if not token:
        return {
            "status": "error",
            "message": "Devi specificare sia waba_id che token nell'URL. Esempio: /subscribe-waba?waba_id=123456&token=EAAG..."
        }

    url = f"https://graph.facebook.com/v18.0/{waba_id}/subscribed_apps"
    headers = {"Authorization": f"Bearer {token}"}

    try:
        res = requests.post(url, headers=headers, timeout=10)
        data = res.json()
        return {
            "status": "completed",
            "waba_id": waba_id,
            "meta_response": data
        }
    except Exception as e:
        return {
            "status": "error",
            "error_detail": str(e)
        }


@app.get("/auth/google")
def google_auth_login(
    request: Request,
    tenant_id: int = Query(1, description="ID del Tenant da collegare"),
    db: Session = Depends(get_db)
):
    """
    Avvia il flusso OAuth2 per collegare Google Calendar al Tenant specificato.
    """
    tenant = db.query(Tenant).filter(Tenant.id == tenant_id).first()
    if not tenant:
        return {"status": "error", "message": f"Tenant ID={tenant_id} non trovato"}

    redirect_uri = f"{request.url.scheme}://{request.url.netloc}/auth/google/callback"
    client_id = os.getenv("GOOGLE_CLIENT_ID")
    client_secret = os.getenv("GOOGLE_CLIENT_SECRET")

    if not client_id or not client_secret:
        return {
            "status": "error",
            "message": "Variabili GOOGLE_CLIENT_ID e GOOGLE_CLIENT_SECRET non trovate su Render.",
            "instruct": "Imposta GOOGLE_CLIENT_ID e GOOGLE_CLIENT_SECRET nell'Environment di Render.",
            "redirect_uri_da_autorizzare_su_google_cloud_console": redirect_uri
        }

    client_config = {
        "web": {
            "client_id": client_id,
            "client_secret": client_secret,
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://oauth2.googleapis.com/token",
            "redirect_uris": [redirect_uri],
        }
    }

    flow = Flow.from_client_config(
        client_config,
        scopes=["https://www.googleapis.com/auth/calendar"],
        redirect_uri=redirect_uri
    )

    authorization_url, state = flow.authorization_url(
        access_type="offline",
        include_granted_scopes="true",
        prompt="consent",
        state=str(tenant_id)
    )

    return RedirectResponse(authorization_url)


@app.get("/auth/google/callback")
def google_auth_callback(
    request: Request,
    code: str = Query(...),
    state: str = Query("1"),
    db: Session = Depends(get_db)
):
    """
    Callback OAuth2 di Google: scambia il codice temporaneo con il refresh token e salva su DB.
    """
    tenant_id = int(state) if state.isdigit() else 1
    tenant = db.query(Tenant).filter(Tenant.id == tenant_id).first()

    redirect_uri = f"{request.url.scheme}://{request.url.netloc}/auth/google/callback"
    client_id = os.getenv("GOOGLE_CLIENT_ID")
    client_secret = os.getenv("GOOGLE_CLIENT_SECRET")

    client_config = {
        "web": {
            "client_id": client_id,
            "client_secret": client_secret,
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://oauth2.googleapis.com/token",
            "redirect_uris": [redirect_uri],
        }
    }

    flow = Flow.from_client_config(
        client_config,
        scopes=["https://www.googleapis.com/auth/calendar"],
        redirect_uri=redirect_uri
    )

    flow.fetch_token(code=code)
    credentials = flow.credentials

    creds_dict = {
        "token": credentials.token,
        "refresh_token": credentials.refresh_token,
        "token_uri": credentials.token_uri,
        "client_id": credentials.client_id,
        "client_secret": credentials.client_secret,
        "scopes": credentials.scopes
    }

    tenant.google_credentials_json = json.dumps(creds_dict)
    if not tenant.google_calendar_id:
        tenant.google_calendar_id = "primary"

    db.commit()

    return HTMLResponse(content=f"""
    <html>
        <body style="font-family: sans-serif; text-align: center; padding-top: 50px;">
            <h1 style="color: #2e7d32;">Google Calendar Collegato con Successo!</h1>
            <p>Il tenant <strong>{tenant.name}</strong> (ID: {tenant.id}) è ora autorizzato a leggere e scrivere appuntamenti su Google Calendar.</p>
        </body>
    </html>
    """)




