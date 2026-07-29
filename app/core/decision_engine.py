"""
LIVELLO 2 — Core Decision Engine.

Gestisce gli Imprevisti ed Edge Cases:
  1. Timeout inattivita' (>10 min): chiude sessione (EXPIRED) e libera lock
  2. FAQ Switch: risponde alla FAQ e riprende lo stato sospeso (answer_and_resume)
  3. Anti-Loop: contatore rifiuti consecutivi (>=3 -> HANDOFF_TO_HUMAN)
  4. Abort: rilascia lock e resetta la sessione
"""

import json
from datetime import datetime, timedelta
from typing import Optional
from sqlalchemy.orm import Session as DbSession

from app.db.models import UserSession, FAQ, Tenant
from app.config import settings
from app.tools.calendar_service import release_user_locks


class DecisionEngine:
    """
    Gestore degli edge case e dei servizi di utilita'.
    """

    def check_session_timeout(self, session: UserSession, db: DbSession) -> bool:
        """
        Verifica se la sessione e' scaduta per inattivita' (>10 min).
        Se scaduta, disattiva la sessione, rilascia i lock e restituisce True.
        """
        if not session or session.state == "EXPIRED":
            return False

        if not session.last_interaction:
            return False

        elapsed = (datetime.utcnow() - session.last_interaction).total_seconds()
        if elapsed > settings.SESSION_TIMEOUT_SECONDS:
            # Marca come scaduta
            session.state = "EXPIRED"
            db.commit()
            # Rilascia lock
            release_user_locks(session.tenant_id, session.customer_phone, db)
            return True

        return False

    def handle_faq(self, topic: Optional[str], tenant_id: int, pending_prompt: str, db: DbSession) -> dict:
        """
        Recupera la risposta FAQ dal DB per il topic indicato e prepara
        la direttiva 'answer_and_resume'.
        """
        faq_item = None
        if topic:
            faq_item = (
                db.query(FAQ)
                .filter(FAQ.tenant_id == tenant_id, FAQ.topic == topic)
                .first()
            )

        if not faq_item:
            # Risposta generica di fallback per la FAQ
            answer = "In studio accettiamo pagamenti in contanti, bancomat e carte di credito."
        else:
            answer = faq_item.answer

        return {
            "next_action": "answer_and_resume",
            "faq_answer": answer,
            "pending_prompt": pending_prompt,
        }

    def handle_abort(self, session: UserSession, db: DbSession) -> dict:
        """
        Gestisce il pentimento dell'utente: azzera la sessione e rilascia i lock.
        """
        release_user_locks(session.tenant_id, session.customer_phone, db)
        session.state = "GATHERING_REQUIREMENTS"
        session.conv_data = json.dumps({})
        session.rejection_count = 0
        db.commit()

        return {
            "next_action": "abort",
            "message": "Prenotazione annullata.",
        }

    def handle_rejection(self, session: UserSession, db: DbSession) -> tuple[bool, dict]:
        """
        Incrementa il contatore anti-loop.
        Se rejection_count >= 3 -> HANDOFF_TO_HUMAN.
        Returns: (is_handoff: bool, directive: dict)
        """
        session.rejection_count = (session.rejection_count or 0) + 1
        db.commit()

        if session.rejection_count >= 3:
            session.state = "HANDOFF_TO_HUMAN"
            db.commit()
            release_user_locks(session.tenant_id, session.customer_phone, db)
            return True, {
                "next_action": "handoff_to_human",
                "reason": "troppi_rifiuti_consecutivi",
            }

        return False, {}
