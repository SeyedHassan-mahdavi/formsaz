# -*- coding: utf-8 -*-
from __future__ import annotations
import datetime
from telegram.error import TimedOut, BadRequest, RetryAfter, NetworkError

def now_iso() -> str:
    return datetime.datetime.utcnow().isoformat()

async def safe_answer(q, *args, **kwargs):
    try:
        await q.answer(*args, **kwargs)
    except (TimedOut, RetryAfter, NetworkError, BadRequest):
        pass
