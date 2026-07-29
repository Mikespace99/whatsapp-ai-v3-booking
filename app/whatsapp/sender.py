import requests


def send_whatsapp_message(to: str, text: str, token: str, phone_id: str) -> dict:
    """
    Invia un messaggio WhatsApp tramite Cloud API o stampa a console in MOCK mode.
    """
    if not token or not phone_id or token.startswith("your_") or phone_id.startswith("your_"):
        print("\n=================== [MOCK WHATSAPP SENDER] ===================")
        print(f"To:      {to}")
        print(f"Message:\n{text}")
        print("==============================================================\n")
        return {"status": "mock_sent", "recipient": to, "message": text}

    url = f"https://graph.facebook.com/v19.0/{phone_id}/messages"
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json"
    }
    payload = {
        "messaging_product": "whatsapp",
        "to": to,
        "type": "text",
        "text": {"body": text}
    }

    try:
        response = requests.post(url, json=payload, headers=headers)
        return response.json()
    except Exception as e:
        print(f"[WhatsApp Sender Error]: {e}")
        return {"status": "error", "message": str(e)}
