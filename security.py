"""Employee closing codes: hashing, validation, generation, lookup, and a small
in-memory limiter for repeated wrong codes."""
import hashlib
import hmac
import secrets
from datetime import datetime, timedelta

from flask import current_app

from constants import (CODE_ATTEMPT_LIMIT, CODE_LOCKOUT_MINUTES, CODE_MAX_LENGTH,
                       CODE_MIN_LENGTH, GENERATED_CODE_LENGTH)
from models import Employee


def hash_code(code):
    """Keyed hash (HMAC-SHA256). The same code always gives the same hash, which is
    what lets us find an employee by code and keep codes unique."""
    pepper = current_app.config["CODE_PEPPER"].encode()
    return hmac.new(pepper, code.encode(), hashlib.sha256).hexdigest()


def validate_code_format(code):
    """Return an error message if the code is unacceptable, else None."""
    if not (code.isascii() and code.isdigit()):
        return "Codes must contain digits only."
    if not CODE_MIN_LENGTH <= len(code) <= CODE_MAX_LENGTH:
        return f"Codes must be {CODE_MIN_LENGTH} to {CODE_MAX_LENGTH} digits long."
    if len(set(code)) == 1:
        return "A code can't be a single repeated digit."
    if code in "0123456789" or code in "9876543210":
        return "A code can't be a simple sequence like 1234."
    return None


def code_in_use(code_hash, ignore_id=None):
    query = Employee.query.filter_by(code_hash=code_hash)
    if ignore_id is not None:
        query = query.filter(Employee.id != ignore_id)
    return query.first() is not None


def generate_unique_code():
    while True:
        code = "".join(secrets.choice("0123456789") for _ in range(GENERATED_CODE_LENGTH))
        if validate_code_format(code) is None and not code_in_use(hash_code(code)):
            return code


def find_employee_by_code(code):
    """The active employee who owns this code, or None."""
    if not code:
        return None
    return (Employee.query.filter_by(code_hash=hash_code(code))
            .filter(Employee.archived_at.is_(None)).first())


# --- Limiter for wrong codes (kept in memory: resets when the app restarts) ---
_failures = {}   # device address -> {"count": int, "locked_until": datetime or None}


def lockout_minutes_left(device):
    entry = _failures.get(device)
    if entry and entry["locked_until"] and entry["locked_until"] > datetime.now():
        return int((entry["locked_until"] - datetime.now()).total_seconds() // 60) + 1
    return 0


def register_failed_code(device):
    """Count a wrong code. Returns True if this attempt triggered a lockout."""
    entry = _failures.setdefault(device, {"count": 0, "locked_until": None})
    entry["count"] += 1
    if entry["count"] >= CODE_ATTEMPT_LIMIT:
        entry["count"] = 0
        entry["locked_until"] = datetime.now() + timedelta(minutes=CODE_LOCKOUT_MINUTES)
        return True
    return False


def clear_failed_codes(device):
    _failures.pop(device, None)