"""
ID generation utilities.
"""
import hashlib


def generate_client_order_id(opportunity_id: str, leg_index: int) -> str:
    """
    Generate an idempotent client order ID based on the opportunity and leg index.
    client_order_id = sha256(opportunity_id + leg_index)[:16]
    """
    payload = f"{opportunity_id}_{leg_index}".encode("utf-8")
    return hashlib.sha256(payload).hexdigest()[:16]
