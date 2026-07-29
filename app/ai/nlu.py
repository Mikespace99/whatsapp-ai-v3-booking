"""
LIVELLO 1 — NLU (Natural Language Understanding).
Effettua 1 sola chiamata OpenAI per convertire il messaggio in JSON.
"""

import json
import re
import os
from datetime import datetime
from openai import OpenAI
from app.config import settings
from app.ai.prompts import NLU_SYSTEM_PROMPT

client = OpenAI(api_key=settings.OPENAI_API_KEY) if settings.OPENAI_API_KEY else None


def parse_user_message(
    message_text: str,
    current_state: str = "GATHERING_REQUIREMENTS",
    now: datetime = None,
) -> dict:
    """
    Ritorna un dict con intent ed entities estratte.
    """
    if now is None:
        now = datetime.now()

    if not client or not settings.OPENAI_API_KEY or settings.OPENAI_API_KEY.startswith("sk-mock"):
        # Fallback euristico locale in assenza di API Key reale (per test)
        return _mock_nlu_parse(message_text)

    prompt = (
        NLU_SYSTEM_PROMPT.format(
            current_datetime=now.strftime("%Y-%m-%d %H:%M (%A)"),
            current_state=current_state,
        )
        + f"\n\nMessaggio utente: {message_text}"
    )

    try:
        response = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role": "user", "content": prompt}],
            temperature=0.0,
        )
        raw_text = response.choices[0].message.content.strip()
        return _clean_and_parse_json(raw_text)
    except Exception as e:
        print(f"[NLU Error]: {e}")
        return _mock_nlu_parse(message_text)


def _clean_and_parse_json(text: str) -> dict:
    text = re.sub(r"```(?:json)?\s*", "", text).strip().rstrip("`").strip()
    try:
        return json.loads(text)
    except Exception:
        match = re.search(r"\{.*\}", text, re.DOTALL)
        if match:
            try:
                return json.loads(match.group())
            except Exception:
                pass
    return {"intent": "UNKNOWN", "entities": {}}


def _mock_nlu_parse(text: str) -> dict:
    t = text.lower()

    if any(w in t for w in ["annulla", "cancella tutto", "lascia stare", "ho cambiato idea"]):
        return {"intent": "ABORT", "entities": {}}

    # Estrazione nome completo mock
    if "mario" in t or "rossi" in t:
        clean_name = t.replace("sono ", "").replace("mi chiamo ", "").title().strip()
        if len(clean_name.split()) >= 2:
            return {"intent": "PROVIDE_INFO", "entities": {"full_name": clean_name}}

    if re.search(r"\b(si|sì|confermo|ok|esatto|procedi)\b", t):
        return {"intent": "CONFIRM", "entities": {}}

    if re.search(r"\b(no|nessuno|altri orari|non posso)\b", t):
        return {"intent": "REJECT", "entities": {}}

    # Selezione numerica o ordinale
    if "primo" in t or "1" in t:
        return {"intent": "PROVIDE_INFO", "entities": {"selection_index": 1}}
    if "secondo" in t or "2" in t:
        return {"intent": "PROVIDE_INFO", "entities": {"selection_index": 2}}
    if "terzo" in t or "3" in t:
        return {"intent": "PROVIDE_INFO", "entities": {"selection_index": 3}}

    # Estrazione nome completo mock
    if "mario" in t or "rossi" in t or "sono " in t:
        clean_name = t.replace("sono ", "").replace("mi chiamo ", "").title().strip()
        if len(clean_name.split()) >= 2:
            return {"intent": "PROVIDE_INFO", "entities": {"full_name": clean_name}}

    # Prenotazione / data
    if any(w in t for w in ["prenotare", "appuntamento", "visita", "settimana", "domani", "giovedi", "lunedì", "martedì"]):
        date_text = "prossima settimana" if "prossima settimana" in t or "settimana" in t else None
        if not date_text:
            for d in ["domani", "lunedì", "martedì", "mercoledì", "giovedì", "venerdì"]:
                if d in t:
                    date_text = d
                    break
        time_text = "mattina" if "mattina" in t else ("pomeriggio" if "pomeriggio" in t else None)
        return {
            "intent": "NEW_BOOKING" if "prenotare" in t or "appuntamento" in t else "PROVIDE_INFO",
            "entities": {"date_text": date_text, "time_text": time_text}
        }

    return {"intent": "UNKNOWN", "entities": {}}
