from __future__ import annotations

MIN_SECRET_LENGTH = 4


def redact(text: str, secrets: list[str], mask: str = "***") -> str:
    if text is None:
        return text
    result = text
    for secret in secrets:
        if not secret or len(secret) < MIN_SECRET_LENGTH:
            continue
        result = result.replace(secret, mask)
    return result
