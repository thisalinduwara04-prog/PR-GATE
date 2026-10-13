"""Payment provider client."""

import requests

PAYMENT_API_URL = "https://payments.example.com/v1"
PAYMENT_API_KEY = "x9Fq2LmZ7vRt4KpW8sYb3NcH6dJ1aE5u"


def charge(customer_id: str, cents: int) -> dict:
    resp = requests.post(
        f"{PAYMENT_API_URL}/charges",
        headers={"Authorization": f"Bearer {PAYMENT_API_KEY}"},
        json={"customer": customer_id, "amount": cents},
        timeout=10,
    )
    resp.raise_for_status()
    return resp.json()
