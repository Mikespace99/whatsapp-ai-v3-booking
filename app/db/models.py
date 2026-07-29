from sqlalchemy import Column, Integer, String, DateTime, Boolean, Text, ForeignKey, UniqueConstraint
from datetime import datetime
from app.db.database import Base


class Tenant(Base):
    __tablename__ = "tenants"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, nullable=False)

    title = Column(String, nullable=True)
    first_name = Column(String, nullable=True)
    last_name = Column(String, nullable=True)
    contact_phone = Column(String, nullable=True)

    whatsapp_phone_number_id = Column(String, unique=True, index=True, nullable=True)
    whatsapp_access_token = Column(String, nullable=True)

    google_access_token = Column(String, nullable=True)
    google_refresh_token = Column(String, nullable=True)
    google_token_expiry = Column(DateTime, nullable=True)

    work_start_time = Column(String, default="09:00", nullable=False)
    work_end_time = Column(String, default="17:00", nullable=False)
    work_start_time_2 = Column(String, nullable=True)
    work_end_time_2 = Column(String, nullable=True)
    working_days = Column(String, default="mon,tue,wed,thu,fri", nullable=False)

    slot_duration_minutes = Column(Integer, default=30, nullable=False)
    buffer_minutes = Column(Integer, default=10, nullable=False)
    minimum_notice_hours = Column(Integer, default=2, nullable=False)
    maximum_booking_days = Column(Integer, default=60, nullable=False)

    timezone = Column(String, default="Europe/Rome", nullable=False)
    custom_instructions = Column(String, nullable=True)

    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime, default=datetime.utcnow)


class UserSession(Base):
    """
    Stato persistente della sessione conversazionale di prenotazione.

    Stati della State Machine (BOOKING):
      - GATHERING_REQUIREMENTS : raccoglie preferenze temporali
      - CHECKING_CALENDAR     : verifica disponibilita' sul calendario
      - OFFERING_SLOTS        : propone fino a 3 slot
      - LOCKING_SLOT          : blocco temporaneo dello slot scelto (5 min)
      - WAITING_IDENTITY      : raccoglie/conferma Nome + Cognome (>=2 parole) e Telefono
      - CONFIRMING            : scrittura finale su Google Calendar & DB
      - HANDOFF_TO_HUMAN      : scatta dopo 3 rifiuti consecutivi
      - EXPIRED               : chiusa per inattivita' (>10 min)
    """
    __tablename__ = "user_sessions"

    id = Column(Integer, primary_key=True, index=True)
    tenant_id = Column(Integer, ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False)
    customer_phone = Column(String, nullable=False)

    state = Column(String, default="GATHERING_REQUIREMENTS", nullable=False)
    conv_data = Column(Text, nullable=True)  # JSON: customer, request, availability, locked_slot, appointment

    # Contatore per Anti-Loop (rifiuti consecutivi)
    rejection_count = Column(Integer, default=0, nullable=False)

    last_interaction = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    __table_args__ = (
        UniqueConstraint('tenant_id', 'customer_phone', name='uix_tenant_booking_customer'),
    )


class SlotLock(Base):
    """
    Blocco temporaneo dello slot (5 minuti) per evitare Race Conditions.
    """
    __tablename__ = "slot_locks"

    id = Column(Integer, primary_key=True, index=True)
    tenant_id = Column(Integer, ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False)
    customer_phone = Column(String, nullable=False)
    date_str = Column(String, nullable=False)  # YYYY-MM-DD
    time_str = Column(String, nullable=False)  # HH:MM
    expires_at = Column(DateTime, nullable=False)
    is_active = Column(Boolean, default=True)


class Appointment(Base):
    __tablename__ = "appointments"

    id = Column(Integer, primary_key=True, index=True)
    tenant_id = Column(Integer, ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False)
    customer_phone = Column(String, nullable=False)
    customer_name = Column(String, nullable=True)

    start_time = Column(DateTime, nullable=False)
    end_time = Column(DateTime, nullable=False)
    google_event_id = Column(String, unique=True, nullable=True)
    status = Column(String, default="confirmed")
    created_at = Column(DateTime, default=datetime.utcnow)


class FAQ(Base):
    """
    Tabella risposte FAQ per gestione context switch.
    """
    __tablename__ = "faqs"

    id = Column(Integer, primary_key=True, index=True)
    tenant_id = Column(Integer, ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False)
    topic = Column(String, nullable=False)  # es. "payment_methods", "parking", "location"
    answer = Column(Text, nullable=False)
