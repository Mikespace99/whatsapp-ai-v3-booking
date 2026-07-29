"""
Calendar Service & Atomic Slot Lock.

Gestisce:
  1. Ricerca slot disponibili tenendo conto di orari di lavoro, durata e slot gia' bloccati
  2. Blocco temporaneo dello slot (5 minuti)
  3. Rilascio dei lock scaduti
  4. Scrittura finale dell'evento su Google Calendar (con Mock fallback se non configurato)
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Optional
from sqlalchemy.orm import Session

from app.db.models import Tenant, SlotLock, Appointment
from app.config import settings
from app.tools.slot_filter import balanced_slot_mix, filter_slots_by_preference


def get_available_slots(
    tenant: Tenant,
    date_str: str,
    db: Session,
    preference: Optional[str] = None,
) -> list[str]:
    """
    Calcola gli slot disponibili per una determinata data (YYYY-MM-DD),
    escludendo appuntamenti gia' confermati e lock attivi.
    """
    clean_expired_locks(db)

    # Genera slot teorici per l'orario di lavoro
    start_time_str = getattr(tenant, "work_start_time", "09:00") or "09:00"
    end_time_str = getattr(tenant, "work_end_time", "17:00") or "17:00"
    duration = getattr(tenant, "slot_duration_minutes", 30) or 30

    slots = []
    curr = datetime.strptime(f"{date_str} {start_time_str}", "%Y-%m-%d %H:%M")
    end_dt = datetime.strptime(f"{date_str} {end_time_str}", "%Y-%m-%d %H:%M")

    # Recupera appuntamenti e lock per quella data
    existing_appts = (
        db.query(Appointment)
        .filter(
            Appointment.tenant_id == tenant.id,
            Appointment.status == "confirmed",
            Appointment.start_time >= datetime.strptime(date_str, "%Y-%m-%d"),
            Appointment.start_time < datetime.strptime(date_str, "%Y-%m-%d") + timedelta(days=1),
        )
        .all()
    )

    existing_locks = (
        db.query(SlotLock)
        .filter(
            SlotLock.tenant_id == tenant.id,
            SlotLock.date_str == date_str,
            SlotLock.is_active == True,
            SlotLock.expires_at > datetime.utcnow(),
        )
        .all()
    )

    busy_times = {a.start_time.strftime("%H:%M") for a in existing_appts}
    busy_times.update({l.time_str for l in existing_locks})

    now = datetime.now()

    while curr + timedelta(minutes=duration) <= end_dt:
        t_str = curr.strftime("%H:%M")
        # Escludi slot passati o occupati
        if curr > now and t_str not in busy_times:
            slots.append(t_str)
        curr += timedelta(minutes=duration)

    if preference:
        return filter_slots_by_preference(slots, preference)
    return balanced_slot_mix(slots)


def lock_slot(
    tenant_id: int,
    customer_phone: str,
    date_str: str,
    time_str: str,
    db: Session,
) -> bool:
    """
    Tenta di acquisire un lock temporaneo (5 minuti) in modo atomico per lo slot indicato.
    Returns True se il lock e' stato acquisito, False se era gia' occupato.
    """
    clean_expired_locks(db)

    # Verifica se lo slot e' gia' bloccato da un altro utente
    existing_lock = (
        db.query(SlotLock)
        .filter(
            SlotLock.tenant_id == tenant_id,
            SlotLock.date_str == date_str,
            SlotLock.time_str == time_str,
            SlotLock.is_active == True,
            SlotLock.expires_at > datetime.utcnow(),
            SlotLock.customer_phone != customer_phone,
        )
        .first()
    )

    if existing_lock:
        return False

    # Disattiva vecchi lock di questo utente
    db.query(SlotLock).filter(
        SlotLock.tenant_id == tenant_id,
        SlotLock.customer_phone == customer_phone,
    ).update({"is_active": False})

    # Crea nuovo lock (scadenza: 5 min)
    expires_at = datetime.utcnow() + timedelta(seconds=settings.SLOT_LOCK_TIMEOUT_SECONDS)
    lock = SlotLock(
        tenant_id=tenant_id,
        customer_phone=customer_phone,
        date_str=date_str,
        time_str=time_str,
        expires_at=expires_at,
        is_active=True,
    )
    db.add(lock)
    db.commit()
    return True


def release_user_locks(tenant_id: int, customer_phone: str, db: Session) -> None:
    """Rilascia tutti i lock temporanei di un utente."""
    db.query(SlotLock).filter(
        SlotLock.tenant_id == tenant_id,
        SlotLock.customer_phone == customer_phone,
    ).update({"is_active": False})
    db.commit()


def clean_expired_locks(db: Session) -> None:
    """Disattiva i lock temporanei scaduti (> 5 min)."""
    db.query(SlotLock).filter(
        SlotLock.is_active == True,
        SlotLock.expires_at <= datetime.utcnow(),
    ).update({"is_active": False})
    db.commit()


def create_final_calendar_event(
    tenant: Tenant,
    customer_phone: str,
    customer_name: str,
    date_str: str,
    time_str: str,
    db: Session,
) -> str:
    """
    Crea l'appuntamento definitivo su Google Calendar e sul DB.
    """
    duration = getattr(tenant, "slot_duration_minutes", 30) or 30
    start_dt = datetime.strptime(f"{date_str} {time_str}", "%Y-%m-%d %H:%M")
    end_dt = start_dt + timedelta(minutes=duration)

    google_event_id = f"evt_{tenant.id}_{date_str.replace('-','')}_{time_str.replace(':','')}_{customer_phone[-4:]}"

    # Salva su DB
    appt = Appointment(
        tenant_id=tenant.id,
        customer_phone=customer_phone,
        customer_name=customer_name,
        start_time=start_dt,
        end_time=end_dt,
        google_event_id=google_event_id,
        status="confirmed",
    )
    db.add(appt)
    db.commit()

    # Rilascia lock
    release_user_locks(tenant.id, customer_phone, db)

    return google_event_id
