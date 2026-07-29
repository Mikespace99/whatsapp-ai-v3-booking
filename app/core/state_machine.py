"""
LIVELLO 2 — Core State Machine per BOOKING.

Ciclo di vita della prenotazione:
  GATHERING_REQUIREMENTS -> CHECKING_CALENDAR -> OFFERING_SLOTS -> LOCKING_SLOT -> WAITING_IDENTITY -> CONFIRMING
"""

import json
from datetime import datetime, timedelta
from typing import Optional
from sqlalchemy.orm import Session as DbSession

from app.db.models import UserSession, Tenant
from app.core.temporal_parser import parse_date_text, parse_time_text
from app.tools.calendar_service import get_available_slots, lock_slot, create_final_calendar_event, release_user_locks
from app.core.decision_engine import DecisionEngine


class BookingStateMachine:
    """
    Motore a stati deterministico per la gestione della prenotazione.
    """

    def __init__(self):
        self._decision_engine = DecisionEngine()

    def process_turn(
        self,
        nlu_result: dict,
        session: UserSession,
        tenant: Tenant,
        db: DbSession,
        customer_phone: str,
    ) -> dict:
        """
        Elabora il turno di conversazione.
        Returns: directive_json (dict comando per il Livello 3 NLG)
        """
        # 1. Controlla Timeout Inattivita' (>10 min)
        if self._decision_engine.check_session_timeout(session, db):
            # Sessione appena scaduta
            return {"next_action": "session_expired"}

        intent = nlu_result.get("intent", "UNKNOWN")
        entities = nlu_result.get("entities", {})

        # Carica dati della sessione
        conv_data = self._load_data(session)

        # 2. Gestione ABORT (pentimento)
        if intent == "ABORT":
            return self._decision_engine.handle_abort(session, db)

        # 3. Gestione FAQ Question (Context Switch)
        if intent == "FAQ_QUESTION":
            pending_prompt = self._get_pending_prompt(session.state, conv_data)
            topic = entities.get("faq_topic")
            return self._decision_engine.handle_faq(topic, session.tenant_id, pending_prompt, db)

        # 4. Gestione REJECT (Rifiuto slot proposti)
        if intent == "REJECT" and session.state in ("OFFERING_SLOTS", "LOCKING_SLOT"):
            is_handoff, handoff_directive = self._decision_engine.handle_rejection(session, db)
            if is_handoff:
                return handoff_directive

            # Cerca nuovi slot per il giorno successivo
            current_date = conv_data.get("availability", {}).get("date")
            if current_date:
                next_date = (datetime.strptime(current_date, "%Y-%m-%d") + timedelta(days=1)).strftime("%Y-%m-%d")
                slots = get_available_slots(tenant, next_date, db)
                conv_data["availability"] = {"date": next_date, "slots": slots}
                self._save_session(session, "OFFERING_SLOTS", conv_data, db)
                return {
                    "next_action": "offer_slots",
                    "date_display": self._format_date_italian(next_date),
                    "slots": slots,
                }

        # --- ESECUZIONE DELLA MACCHINA A STATI ---
        state = session.state or "GATHERING_REQUIREMENTS"

        if state == "GATHERING_REQUIREMENTS":
            return self._step_gathering(intent, entities, session, tenant, db, conv_data)

        if state == "OFFERING_SLOTS":
            return self._step_offering(intent, entities, session, tenant, db, conv_data)

        if state == "LOCKING_SLOT":
            return self._step_locking(intent, entities, session, tenant, db, conv_data)

        if state == "WAITING_IDENTITY":
            return self._step_identity(intent, entities, session, tenant, db, conv_data, customer_phone)

        # Fallback di sicurezza: riparte da GATHERING_REQUIREMENTS
        return self._step_gathering(intent, entities, session, tenant, db, conv_data)

    # -----------------------------------------------------------------------
    # Steps
    # -----------------------------------------------------------------------

    def _step_gathering(self, intent, entities, session, tenant, db, conv_data) -> dict:
        """
        Stato: GATHERING_REQUIREMENTS -> CHECKING_CALENDAR -> OFFERING_SLOTS.
        Risolve la data (anche range ampi senza chiedere chiarimenti) e cerca subito gli slot.
        """
        date_text = entities.get("date_text")
        time_text = entities.get("time_text")

        # Risolvi la data (se "prossima settimana", restituisce il lunedi' di inizio range)
        resolved_date = parse_date_text(date_text)
        if not resolved_date:
            # Default: cerca a partire da domani se non indicata
            resolved_date = (datetime.now() + timedelta(days=1)).strftime("%Y-%m-%d")

        pref_time = entities.get("time_of_day") or ("morning" if time_text and "mattina" in time_text else None)

        # Interroga il calendario per la data calcolata
        slots = get_available_slots(tenant, resolved_date, db, preference=pref_time)

        conv_data["request"] = {"date_text": date_text, "time_text": time_text}
        conv_data["availability"] = {"date": resolved_date, "slots": slots}
        session.rejection_count = 0  # reset contatore rifiuti

        self._save_session(session, "OFFERING_SLOTS", conv_data, db)

        return {
            "next_action": "offer_slots",
            "date_display": self._format_date_italian(resolved_date),
            "slots": slots,
        }

    def _step_offering(self, intent, entities, session, tenant, db, conv_data) -> dict:
        """
        Stato: OFFERING_SLOTS -> LOCKING_SLOT.
        L'utente sceglie uno slot (es. "1", "il secondo", "10:00").
        """
        slots = conv_data.get("availability", {}).get("slots", [])
        date_str = conv_data.get("availability", {}).get("date")

        idx = entities.get("selection_index")
        selected_slot = None

        if idx is not None and 1 <= idx <= len(slots):
            selected_slot = slots[idx - 1]
        elif entities.get("time_text"):
            parsed_t = parse_time_text(entities.get("time_text"))
            if parsed_t and parsed_t in slots:
                selected_slot = parsed_t

        if not selected_slot and len(slots) == 1:
            selected_slot = slots[0]

        if not selected_slot:
            # L'utente non ha espresso una selezione valida
            return {
                "next_action": "offer_slots",
                "date_display": self._format_date_italian(date_str),
                "slots": slots,
            }

        # Tenta il LOCK ATOMICO dello slot (5 minuti)
        lock_ok = lock_slot(
            tenant_id=session.tenant_id,
            customer_phone=session.customer_phone,
            date_str=date_str,
            time_str=selected_slot,
            db=db,
        )

        if not lock_ok:
            # Slot appena rubato da un altro (Race Condition)
            # Riesegui la ricerca slot per lo stesso giorno
            new_slots = get_available_slots(tenant, date_str, db)
            conv_data["availability"]["slots"] = new_slots
            self._save_session(session, "OFFERING_SLOTS", conv_data, db)
            return {
                "next_action": "offer_slots",
                "notice": "L'orario selezionato e' appena stato occupato da un altro utente. Ecco gli orari ancora disponibili:",
                "date_display": self._format_date_italian(date_str),
                "slots": new_slots,
            }

        # Lock acquisito con successo! Salva lo slot bloccato e passa a WAITING_IDENTITY o ASK_CONFIRMATION
        conv_data["locked_slot"] = {"date": date_str, "time": selected_slot}
        session.rejection_count = 0

        # Verifica se l'anagrafica e' gia' presente e valida (>=2 parole)
        customer = conv_data.get("customer", {})
        full_name = customer.get("full_name")
        is_name_valid = full_name and len(full_name.strip().split()) >= 2
        is_phone_confirmed = customer.get("phone_confirmed")

        if is_name_valid and is_phone_confirmed:
            self._save_session(session, "LOCKING_SLOT", conv_data, db)
            return {
                "next_action": "ask_slot_confirmation",
                "slot_datetime_display": f"{self._format_date_italian(date_str)} alle ore {selected_slot}",
            }
        else:
            self._save_session(session, "WAITING_IDENTITY", conv_data, db)
            if not is_name_valid:
                return {"next_action": "ask_identity_name"}
            else:
                return {
                    "next_action": "ask_phone_confirmation",
                    "customer_name": full_name,
                    "phone": customer.get("phone", session.customer_phone),
                }

    def _step_locking(self, intent, entities, session, tenant, db, conv_data) -> dict:
        """
        Stato: LOCKING_SLOT. L'utente risponde alla richiesta di conferma dello slot.
        """
        if intent == "CONFIRM":
            # Passa alla raccolta anagrafica se incompleta, oppure a CONFIRMING
            customer = conv_data.get("customer", {})
            full_name = customer.get("full_name")
            is_name_valid = full_name and len(full_name.strip().split()) >= 2
            is_phone_confirmed = customer.get("phone_confirmed")

            if not is_name_valid or not is_phone_confirmed:
                self._save_session(session, "WAITING_IDENTITY", conv_data, db)
                if not is_name_valid:
                    return {"next_action": "ask_identity_name"}
                else:
                    return {
                        "next_action": "ask_phone_confirmation",
                        "customer_name": full_name,
                        "phone": customer.get("phone", session.customer_phone),
                    }

            # Tutto completo → Esegui la prenotazione definitiva!
            return self._finalize_booking(session, tenant, db, conv_data)

        # Se non conferma → riproponi la domanda di conferma
        locked = conv_data.get("locked_slot", {})
        return {
            "next_action": "ask_slot_confirmation",
            "slot_datetime_display": f"{self._format_date_italian(locked.get('date'))} alle ore {locked.get('time')}",
        }

    def _step_identity(self, intent, entities, session, tenant, db, conv_data, customer_phone: str) -> dict:
        """
        Stato: WAITING_IDENTITY. Raccolta Nome + Cognome (>=2 parole) e Conferma Telefono.
        """
        customer = conv_data.setdefault("customer", {})

        # Estrai nome se fornito nel messaggio
        full_name_extracted = entities.get("full_name")
        if full_name_extracted:
            words = full_name_extracted.strip().split()
            if len(words) >= 2:
                customer["full_name"] = full_name_extracted

        # Imposta telefono di default dal numero WhatsApp se non presente
        if not customer.get("phone"):
            customer["phone"] = customer_phone

        # Se l'utente scrive un nuovo numero nel testo → usa quello e marca confermato
        if entities.get("phone"):
            customer["phone"] = entities.get("phone")
            customer["phone_confirmed"] = True

        # Se risponde "si" alla conferma del telefono -> marca confermato
        if intent == "CONFIRM" and customer.get("full_name") and not customer.get("phone_confirmed"):
            customer["phone_confirmed"] = True

        # Ricalcola la validita' DOPO l'aggiornamento
        full_name = customer.get("full_name")
        is_name_valid = full_name and len(full_name.strip().split()) >= 2
        is_phone_confirmed = customer.get("phone_confirmed")

        if not is_name_valid:
            self._save_session(session, "WAITING_IDENTITY", conv_data, db)
            return {"next_action": "ask_identity_name"}

        if not is_phone_confirmed:
            self._save_session(session, "WAITING_IDENTITY", conv_data, db)
            return {
                "next_action": "ask_phone_confirmation",
                "customer_name": full_name,
                "phone": customer.get("phone", customer_phone),
            }

        # Entrambi completi! Passa a CONFIRMING ed esegui la prenotazione
        return self._finalize_booking(session, tenant, db, conv_data)

    def _finalize_booking(self, session, tenant, db, conv_data) -> dict:
        """Esegue la creazione finale dell'appuntamento."""
        locked = conv_data.get("locked_slot", {})
        customer = conv_data.get("customer", {})

        date_str = locked.get("date")
        time_str = locked.get("time")
        full_name = customer.get("full_name")
        phone = customer.get("phone", session.customer_phone)

        # Scrive su Google Calendar & DB
        evt_id = create_final_calendar_event(
            tenant=tenant,
            customer_phone=phone,
            customer_name=full_name,
            date_str=date_str,
            time_str=time_str,
            db=db,
        )

        dt_display = f"{self._format_date_italian(date_str)} alle ore {time_str}"

        # Resetta la sessione per la prossima interazione
        session.state = "GATHERING_REQUIREMENTS"
        session.conv_data = json.dumps({})
        session.rejection_count = 0
        db.commit()

        return {
            "next_action": "confirm_booking",
            "customer_name": full_name,
            "slot_datetime_display": dt_display,
            "google_event_id": evt_id,
        }

    # -----------------------------------------------------------------------
    # Helper
    # -----------------------------------------------------------------------

    def _load_data(self, session: UserSession) -> dict:
        raw = session.conv_data
        if raw:
            try:
                return json.loads(raw)
            except Exception:
                pass
        return {}

    def _save_session(self, session: UserSession, state: str, conv_data: dict, db: DbSession):
        session.state = state
        session.conv_data = json.dumps(conv_data, ensure_ascii=False)
        session.last_interaction = datetime.utcnow()
        db.commit()

    def _get_pending_prompt(self, state: str, conv_data: dict) -> str:
        if state == "OFFERING_SLOTS":
            slots = conv_data.get("availability", {}).get("slots", [])
            date_str = conv_data.get("availability", {}).get("date")
            dt_display = self._format_date_italian(date_str)
            return f"Fatta questa premessa, per {dt_display} ho disponibilita' alle {', '.join(slots)}. Quale orario le blocco?"
        if state == "LOCKING_SLOT":
            locked = conv_data.get("locked_slot", {})
            return f"Fatta questa premessa, le confermo l'appuntamento per {self._format_date_italian(locked.get('date'))} alle ore {locked.get('time')}?"
        if state == "WAITING_IDENTITY":
            return "Fatta questa premessa, mi puo' indicare il nome e cognome completi dell'intestatario dell'appuntamento?"
        return "Come desidera procedere con la prenotazione?"

    def _format_date_italian(self, date_str: Optional[str]) -> str:
        if not date_str:
            return ""
        try:
            months = {
                1: "gennaio", 2: "febbraio", 3: "marzo", 4: "aprile",
                5: "maggio", 6: "giugno", 7: "luglio", 8: "agosto",
                9: "settembre", 10: "ottobre", 11: "novembre", 12: "dicembre",
            }
            days = {
                0: "lunedì", 1: "martedì", 2: "mercoledì", 3: "giovedì",
                4: "venerdì", 5: "sabato", 6: "domenica",
            }
            dt = datetime.strptime(date_str, "%Y-%m-%d")
            return f"{days[dt.weekday()]} {dt.day} {months[dt.month]}"
        except ValueError:
            return date_str
