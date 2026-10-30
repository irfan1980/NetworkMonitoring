from __future__ import annotations

from datetime import datetime, timezone
from typing import Annotated, Optional

from pydantic import AfterValidator, PlainSerializer


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def to_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def iso(value: Optional[datetime]) -> Optional[str]:
    if value is None:
        return None
    return to_utc(value).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_iso(text: str) -> datetime:
    return to_utc(datetime.fromisoformat(text.replace("Z", "+00:00")))


UtcDatetime = Annotated[
    datetime,
    AfterValidator(to_utc),
    PlainSerializer(lambda v: iso(v), return_type=str, when_used="always"),
]
