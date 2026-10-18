"""Mask values of PII columns before they reach any output.

Reconciliation formats every row value it reports through this module, and
the gate's reasons, the logs and the JSON report all reuse those details, so
this is the single place masking happens.

A masked value is the first character plus a short keyed hash, e.g.
"S***#a1b2": enough to see that two values differ, not what they are. The key
is random per process, so masks are comparable within one run only, and a
mask can't be reversed by hashing a list of likely names offline.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets

_RUN_KEY = secrets.token_bytes(32)
HASH_CHARS = 4


def mask_value(value: str | None) -> str:
    if value is None:
        return "NULL"
    if value == "":
        return "''"
    digest = hmac.new(_RUN_KEY, value.encode("utf-8"), hashlib.sha256).hexdigest()[:HASH_CHARS]
    # For very short values the first character would reveal most of it.
    prefix = value[0] if len(value) > 2 else ""
    return f"{prefix}***#{digest}"
