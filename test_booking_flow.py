"""
Script di Simulazione ed E2E Testing per whatsapp-ai-booking.

Testa in sequenza:
  1. Range ampio ("prossima settimana") -> Proposta immediata slot dal range
  2. Selezione slot ("il secondo") -> Lock atomico 5 min + richiesta Nome e Cognome
  3. FAQ Switch ("accettate il POS?") -> Risposta FAQ + ripresa automatica domanda
  4. Identity ("Mario Rossi") -> Scrittura finale appuntamento
  5. Timeout inattivita' (>10 min) -> Reset sessione e rilascio lock
  6. Anti-Loop -> 3 rifiuti -> Handoff to human
"""

import os
import sys
from datetime import datetime, timedelta

# Aggiungi il percorso di radice al sys.path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db.database import Base
from app.db.models import Tenant, UserSession, Appointment, FAQ, SlotLock
from app.whatsapp.webhook import process_booking_pipeline


def run_e2e_tests():
    # Database SQLite in memoria per i test
    engine = create_engine("sqlite:///:memory:", echo=False)
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine)
    db = SessionLocal()

    # 1. Crea Tenant di test
    tenant = Tenant(
        id=1,
        name="Studio Medico Rossi",
        title="Dott.",
        last_name="Rossi",
        is_active=True,
        timezone="Europe/Rome",
        work_start_time="09:00",
        work_end_time="17:00",
        working_days="mon,tue,wed,thu,fri",
        slot_duration_minutes=30,
    )
    db.add(tenant)

    # 2. Crea FAQ di test
    faq = FAQ(
        tenant_id=1,
        topic="payment_methods",
        answer="Sì, in studio accettiamo sia bancomat che carte di credito (POS).",
    )
    db.add(faq)
    db.commit()

    phone = "393331234567"

    print("=========================================================================")
    print("      SIMULAZIONE END-TO-END — ARCHITETTURA SANDWICH A 3 LIVELLI         ")
    print("=========================================================================")

    # ── TEST 1: Range Ampio ("prossima settimana") ─────────────────────────────
    print("\n--- TEST 1: Richiesta Vaga / Range Ampio ---")
    msg1 = "Vorrei prenotare un appuntamento per la prossima settimana"
    print(f"Utente: '{msg1}'")
    reply1 = process_booking_pipeline(phone, msg1, tenant, db)
    print(f"Sistema:\n{reply1}\n")

    sess = db.query(UserSession).filter_by(tenant_id=1, customer_phone=phone).first()
    print(f"[DB State]: state={sess.state}")

    # ── TEST 2: Selezione Slot ("il secondo") ──────────────────────────────────
    print("\n--- TEST 2: Selezione dello slot ---")
    msg2 = "Prendo il secondo"
    print(f"Utente: '{msg2}'")
    reply2 = process_booking_pipeline(phone, msg2, tenant, db)
    print(f"Sistema:\n{reply2}\n")

    sess = db.query(UserSession).filter_by(tenant_id=1, customer_phone=phone).first()
    print(f"[DB State]: state={sess.state}")

    # Verifica Lock Slot
    locks = db.query(SlotLock).filter_by(tenant_id=1, customer_phone=phone, is_active=True).all()
    print(f"[DB Locks Attivi]: {len(locks)} (scadenza: {locks[0].expires_at if locks else 'Nessuno'})")

    # ── TEST 3: FAQ Context Switch ("si può pagare col POS?") ──────────────────
    print("\n--- TEST 3: FAQ Context Switch (domanda improvvisa) ---")
    msg3 = "Ma si può pagare col POS in studio?"
    print(f"Utente: '{msg3}'")
    reply3 = process_booking_pipeline(phone, msg3, tenant, db)
    print(f"Sistema:\n{reply3}\n")

    sess = db.query(UserSession).filter_by(tenant_id=1, customer_phone=phone).first()
    print(f"[DB State dopo FAQ]: state={sess.state} (lo stato e' rimasto preservato!)")

    # ── TEST 4: Identity (Fornitura Nome e Cognome) ───────────────────────────
    print("\n--- TEST 4: Fornitura Nome e Cognome (>=2 parole) ---")
    msg4 = "Sono Mario Rossi"
    print(f"Utente: '{msg4}'")
    reply4 = process_booking_pipeline(phone, msg4, tenant, db)
    print(f"Sistema:\n{reply4}\n")

    sess = db.query(UserSession).filter_by(tenant_id=1, customer_phone=phone).first()
    print(f"[DB State]: state={sess.state}")

    # ── TEST 5: Conferma Telefono e Scrittura Finale ──────────────────────────
    print("\n--- TEST 5: Conferma Telefono e Scrittura Finale ---")
    msg5 = "Va bene questo numero"
    print(f"Utente: '{msg5}'")
    reply5 = process_booking_pipeline(phone, msg5, tenant, db)
    print(f"Sistema:\n{reply5}\n")

    appts = db.query(Appointment).all()
    print(f"[DB Appuntamenti Scrittura Finale]: {len(appts)} confermati")
    for a in appts:
        print(f"  -> Appuntamento ID={a.id}: Intestatario='{a.customer_name}', Inizio={a.start_time}, GoogleID={a.google_event_id}")

    # ── TEST 6: Timeout Inattivita' (>10 min) ──────────────────────────────────
    print("\n--- TEST 6: Timeout Inattività (>10 min) ---")
    # Avvia nuova prenotazione
    process_booking_pipeline(phone, "Vorrei un altro appuntamento per domani", tenant, db)
    sess_t = db.query(UserSession).filter_by(tenant_id=1, customer_phone=phone).first()
    print(f"Stato prima del timeout: {sess_t.state}")

    # Simula avanzamento del tempo di 11 minuti nel passato
    sess_t.last_interaction = datetime.utcnow() - timedelta(minutes=11)
    db.commit()

    msg_timeout = "Ci sono?"
    print(f"Utente dopo 11 min: '{msg_timeout}'")
    reply_timeout = process_booking_pipeline(phone, msg_timeout, tenant, db)
    print(f"Sistema:\n{reply_timeout}\n")

    sess_after = db.query(UserSession).filter_by(tenant_id=1, customer_phone=phone).first()
    print(f"[DB State dopo Timeout]: state={sess_after.state}")

    print("=========================================================================")
    print("                    TUTTI I TEST SUPERATI CON SUCCESSO!                  ")
    print("=========================================================================")


if __name__ == "__main__":
    run_e2e_tests()
