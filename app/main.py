from fastapi import FastAPI
from app.db.database import engine, Base
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
