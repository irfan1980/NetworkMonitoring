from __future__ import annotations

from typing import Any, Literal, Optional, Union

from pydantic import BaseModel, ConfigDict, ValidationError


class HelloMessage(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["hello"]
    device: str
    clientId: str


class ResyncMessage(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["resync"]


class AckMessage(BaseModel):
    model_config = ConfigDict(extra="ignore")

    type: Literal["ack"]
    alertId: str
    clientId: Optional[str] = None
    ackedBy: Optional[str] = None
    device: Optional[str] = None
    pin: Optional[str] = None


class SilenceMessage(BaseModel):
    model_config = ConfigDict(extra="ignore")

    type: Literal["silence"]
    alertId: Optional[str] = None
    clientId: Optional[str] = None
    device: Optional[str] = None
    minutes: Optional[int] = None


POST_HELLO_ALLOWED_TYPES = frozenset({"resync", "ack", "silence"})

_INBOUND_MODELS_BY_TYPE: dict[str, type[BaseModel]] = {
    "hello": HelloMessage,
    "resync": ResyncMessage,
    "ack": AckMessage,
    "silence": SilenceMessage,
}


def validate_inbound_hello(raw: dict) -> Optional[HelloMessage]:
    try:
        return HelloMessage.model_validate(raw)
    except ValidationError:
        return None


def validate_inbound(raw: dict) -> Optional[BaseModel]:
    msg_type = raw.get("type")
    if msg_type not in POST_HELLO_ALLOWED_TYPES:
        return None
    model = _INBOUND_MODELS_BY_TYPE[msg_type]
    try:
        return model.model_validate(raw)
    except ValidationError:
        return None


class SnapshotMessage(BaseModel):
    model_config = ConfigDict(extra="allow")

    type: Literal["snapshot"]
    seq: int
    ts: str


class DeltaMessage(BaseModel):
    model_config = ConfigDict(extra="allow")

    type: Literal["delta"]
    seq: int
    ts: str


class AlertMessage(BaseModel):
    model_config = ConfigDict(extra="allow")

    type: Literal["alert"]
    alertId: str
    rootCause: str
    severity: int


class AckResultMessage(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["ackResult"]
    alertId: str
    ok: bool
    reason: Optional[str] = None


_OUTBOUND_MODELS_BY_TYPE: dict[str, type[BaseModel]] = {
    "snapshot": SnapshotMessage,
    "delta": DeltaMessage,
    "alert": AlertMessage,
    "ackResult": AckResultMessage,
}


def validate_outbound(raw: dict) -> bool:
    msg_type = raw.get("type")
    model = _OUTBOUND_MODELS_BY_TYPE.get(msg_type)
    if model is None:
        return False
    try:
        model.model_validate(raw)
    except ValidationError:
        return False
    return True
