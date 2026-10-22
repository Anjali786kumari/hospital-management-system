import re
from datetime import datetime
from decimal import Decimal, InvalidOperation

from flask import flash


class ValidationError(Exception):
    pass


EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
USERNAME_RE = re.compile(r"^[a-z0-9_.]{3,30}$")
GENDERS = ("Male", "Female", "Other")
BLOOD_GROUPS = ("", "A+", "A-", "B+", "B-", "AB+", "AB-", "O+", "O-")


def parse_date(s, field="Date"):
    try:
        return datetime.strptime((s or "").strip(), "%Y-%m-%d").date()
    except ValueError:
        raise ValidationError(f"{field} is not a valid date.")


def parse_time(s, field="Time"):
    try:
        return datetime.strptime((s or "").strip(), "%H:%M").time()
    except ValueError:
        raise ValidationError(f"{field} is not a valid time.")


def parse_money(s, field="Amount", allow_zero=False):
    try:
        d = Decimal((s or "").strip())
        if not d.is_finite():
            raise InvalidOperation
        d = d.quantize(Decimal("0.01"))
    except (InvalidOperation, ValueError):
        raise ValidationError(f"{field} must be a number.")
    if not d.is_finite() or d < 0 or (d == 0 and not allow_zero) or d > Decimal("10000000"):
        raise ValidationError(f"{field} is out of range.")
    return d


def parse_int(s, field="Value", lo=1, hi=10000):
    try:
        v = int((s or "").strip())
    except ValueError:
        raise ValidationError(f"{field} must be a whole number.")
    if not lo <= v <= hi:
        raise ValidationError(f"{field} must be between {lo} and {hi}.")
    return v


def clean_phone(s):
    p = re.sub(r"[\s\-()]", "", s or "")
    if not re.fullmatch(r"\+?\d{7,15}", p):
        raise ValidationError("Phone must be 7–15 digits (optionally starting with +).")
    return p


def required(form, key, label, maxlen=120):
    v = (form.get(key) or "").strip()
    if not v:
        raise ValidationError(f"{label} is required.")
    if len(v) > maxlen:
        raise ValidationError(f"{label} must be at most {maxlen} characters.")
    return v


def optional(form, key, maxlen=255):
    v = (form.get(key) or "").strip()
    if len(v) > maxlen:
        raise ValidationError(f"{key.replace('_', ' ').title()} is too long.")
    return v or None


def check_email(v):
    if not EMAIL_RE.match(v or ""):
        raise ValidationError("Enter a valid email address.")
    return v.lower()


def check_password(pw):
    if len(pw or "") < 8 or not re.search(r"[A-Za-z]", pw) or not re.search(r"\d", pw):
        raise ValidationError("Password must be 8+ characters with letters and digits.")
    return pw


def flash_error(e):
    flash(str(e), "danger")
