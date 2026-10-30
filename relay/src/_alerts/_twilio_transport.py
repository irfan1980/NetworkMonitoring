from __future__ import annotations

import httpx

TWILIO_API_BASE = "https://api.twilio.com/2010-04-01"


def send_via_twilio(
    *, account_sid: str, auth_token: str, from_number: str, to_numbers: list[str], body: str,
) -> None:
    url = f"{TWILIO_API_BASE}/Accounts/{account_sid}/Messages.json"
    with httpx.Client(timeout=10.0) as client:
        for to_number in to_numbers:
            response = client.post(
                url,
                auth=(account_sid, auth_token),
                data={"From": from_number, "To": to_number, "Body": body},
            )
            response.raise_for_status()
