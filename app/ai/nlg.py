"""
LIVELLO 3 — NLG (Natural Language Generation).
Traduce la direttiva JSON generata dal Motore Logico in un messaggio naturale WhatsApp.
"""

import json
from openai import OpenAI
from app.config import settings
from app.ai.prompts import NLG_SYSTEM_PROMPT, build_tenant_context

client = OpenAI(api_key=settings.OPENAI_API_KEY) if settings.OPENAI_API_KEY else None


def generate_user_message(directive: dict, tenant=None, user_message_text: str = "") -> str:
    """
    Converte la direttiva JSON in testo WhatsApp.
    """
    if not client or not settings.OPENAI_API_KEY or settings.OPENAI_API_KEY.startswith("sk-mock"):
        return _fallback_template_nlg(directive)

    tenant_context = build_tenant_context(tenant)
    prompt = (
        NLG_SYSTEM_PROMPT.format(
            tenant_context=tenant_context,
            directive_json=json.dumps(directive, ensure_ascii=False, indent=2),
        )
        + (f"\n\nUltimo messaggio utente: {user_message_text}" if user_message_text else "")
    )

    try:
        response = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role": "user", "content": prompt}],
            temperature=0.3,
        )
        return response.choices[0].message.content.strip()
    except Exception as e:
        print(f"[NLG Error]: {e}")
        return _fallback_template_nlg(directive)


def _fallback_template_nlg(directive: dict) -> str:
    action = directive.get("next_action")

    if action == "session_expired":
        return "Mi spiace, ma per motivi di sicurezza la sessione di prenotazione precedente e' scaduta per inattivita' (>10 min). Puoi inviare un nuovo messaggio per ripartire con la prenotazione."

    if action == "offer_slots":
        slots = directive.get("slots", [])
        date_display = directive.get("date_display", "")
        lines = [f"Ecco le prossime disponibilita' trovate per {date_display}:"]
        for i, s in enumerate(slots, 1):
            lines.append(f"{i}. {s}")
        lines.append("\nQuale orario preferisci tra questi?")
        return "\n".join(lines)

    if action == "ask_slot_confirmation":
        dt = directive.get("slot_datetime_display", "")
        return f"Perfetto! Confermi la prenotazione dell'appuntamento per {dt}?"

    if action == "ask_identity_name":
        return "Buongiorno! Mi indichi per favore NOME e COGNOME completi della persona a cui intitolare l'appuntamento?"

    if action == "ask_phone_confirmation":
        name = directive.get("customer_name", "")
        phone = directive.get("phone", "")
        return f"Grazie {name}! Possiamo utilizzare il numero {phone} per le comunicazioni dell'appuntamento, oppure desideri fornirne un altro?"

    if action == "confirm_booking":
        name = directive.get("customer_name", "")
        dt = directive.get("slot_datetime_display", "")
        return f"Prenotazione confermata per {name}! Ti aspettiamo il {dt}. Buona giornata!"

    if action == "answer_and_resume":
        answer = directive.get("faq_answer", "")
        pending = directive.get("pending_prompt", "")
        return f"{answer}\n\n{pending}"

    if action == "handoff_to_human":
        return "Vedo che le opzioni proposte non incontrano le tue esigenze. Per trovare una soluzione personalizzata ti invito a contattare direttamente la nostra segreteria al numero fisso di studio. Grazie!"

    if action == "abort":
        return "Nessun problema, ho annullato la richiesta di prenotazione. Quando desideri riprovare sono a tua disposizione!"

    return "Come posso esserti utile per la prenotazione?"
