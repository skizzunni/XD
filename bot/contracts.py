"""Recognize equivalent MNQ expiry names without merging different contracts."""

import re


_MONTHS = {"MAR": "03", "JUN": "06", "SEP": "09", "DEC": "12"}


def contract_key(value):
    if not isinstance(value, str):
        return value
    match = re.fullmatch(r"MNQ (03|06|09|12)-([0-9]{2})", value.strip().upper())
    if match:
        return f"MNQ {match[1]}-{match[2]}"
    match = re.fullmatch(r"MNQ (MAR|JUN|SEP|DEC)([0-9]{2})", value.strip().upper())
    if match:
        return f"MNQ {_MONTHS[match[1]]}-{match[2]}"
    return value


def same_contract(left, right):
    return contract_key(left) == contract_key(right)
