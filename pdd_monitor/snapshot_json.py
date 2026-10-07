"""Optional faster JSON for newly generated dashboard snapshots only.

Never use this encoder for sealed capture evidence, stored snapshot hashes, or
other byte-stable source documents. Its JSON values match the strict stdlib
contract, but valid float spellings can differ between encoders.
"""
from __future__ import annotations

import json
import math

try:
    import orjson as _orjson
except (ImportError, OSError):
    _orjson = None


def _fast_compatible(value):
    """Reject nonfinite numbers and opt out of extended/unsupported types.

    Only exact native types reach orjson: its extra datetime/dataclass/UUID
    conversions would otherwise broaden the existing dashboard JSON contract.
    Track active containers so a cycle cannot make the precheck loop forever.
    """
    compatible = True
    active = set()
    checked = set()
    stack = [(value, False)]
    while stack:
        current, leaving = stack.pop()
        kind = type(current)
        if kind in (dict, list, tuple):
            identity = id(current)
            if leaving:
                active.remove(identity)
                checked.add(identity)
                continue
            if identity in active:
                raise ValueError('Circular reference detected')
            if identity in checked:
                continue
            active.add(identity)
            stack.append((current, True))
            if kind is dict:
                for key, item in current.items():
                    if type(key) is not str:
                        compatible = False
                        if isinstance(key, float) and not math.isfinite(key):
                            raise ValueError('Out of range float values are not JSON compliant')
                    stack.append((item, False))
            else:
                stack.extend((item, False) for item in current)
        elif kind is float:
            if not math.isfinite(current):
                raise ValueError('Out of range float values are not JSON compliant')
        elif kind is int:
            if current < -(1 << 63) or current > (1 << 64) - 1:
                compatible = False
        elif kind not in (str, bool, type(None)):
            # The stdlib decides how subclasses and non-native objects behave;
            # do not let orjson silently serialize a type it uniquely supports.
            compatible = False
    return compatible


def dumps_bytes(value):
    """Encode a new dashboard value: UTF-8, indent=2, strict numbers, newline.

    Missing optional wheels and values outside the native fast path retain the
    standard library's behavior, including arbitrary-size integers and errors.
    """
    if _orjson is not None and _fast_compatible(value):
        try:
            return _orjson.dumps(value, option=_orjson.OPT_INDENT_2 | _orjson.OPT_APPEND_NEWLINE)
        except TypeError:
            # E.g. nesting beyond orjson's limit. Never coerce values or replace
            # numbers with null; the original strict encoder is the fallback.
            pass
    return (json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + '\n').encode('utf-8')
