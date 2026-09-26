"""Scrub personal data out of text before it goes into a prompt.

Prompts carry store names, SKU names and reviewer notes; none of those need a person's
phone number or email, and a model log is not the place for them. Patterns cover email
addresses, Nigerian and international phone numbers and long digit runs (account and ID
numbers). UUIDs and short numbers (quantities, prices) are left alone.
"""

import re

EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
# +234 803 123 4567, 08031234567, 0803-123-4567, +44 20 7946 0958, (0)803...
PHONE = re.compile(
    r"(?<![\w-])(?:\+?\d{1,3}[\s.-]?)?(?:\(0\)|0)?\d{2,4}[\s.-]?\d{3,4}[\s.-]?\d{3,4}(?![\w-])"
)
# 10+ consecutive digits not embedded in a hex/UUID-looking token.
LONG_DIGITS = re.compile(r"(?<![0-9A-Fa-f-])\d{10,}(?![0-9A-Fa-f-])")


def scrub(text: str) -> str:
    """Replace emails, phone numbers and long digit runs with placeholders."""
    text = EMAIL.sub("[email]", text)
    text = LONG_DIGITS.sub("[number]", text)
    return PHONE.sub(_phone_placeholder, text)


def _phone_placeholder(match: re.Match[str]) -> str:
    digits = re.sub(r"\D", "", match.group(0))
    # Short numeric runs (years, prices like 1500 00) are not phone numbers.
    return "[phone]" if len(digits) >= 9 else match.group(0)
