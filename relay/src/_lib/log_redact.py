from __future__ import annotations

import logging
import re

_SECRET_PARAM = re.compile(r"((?:clientSecret|token)=)[^&\s\"]+")


def _redact(value):
    return _SECRET_PARAM.sub(r"\1***REDACTED***", value) if isinstance(value, str) else value


class RedactWsSecretFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.msg, str):
            record.msg = _redact(record.msg)
        args = record.args
        if isinstance(args, tuple):
            record.args = tuple(_redact(a) for a in args)
        elif isinstance(args, dict):
            record.args = {k: _redact(v) for k, v in args.items()}
        return True
