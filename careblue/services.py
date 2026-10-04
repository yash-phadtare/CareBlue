"""Pure validation, money, time, and query services."""
import json
import math
from datetime import datetime, timezone, timedelta
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from zoneinfo import ZoneInfo
from flask import request, g, url_for
from database import setting
from careblue.constants import VALID_GENDERS

def facility_now():
    from flask import has_request_context
    hospital = getattr(g, "hospital", None) if has_request_context() else None
    zone = hospital["timezone"] if hospital else setting("FACILITY_TIMEZONE", "Asia/Kolkata")
    return datetime.now(ZoneInfo(zone))

def date_display(value):
    try:
        return datetime.strptime(str(value)[:10], "%Y-%m-%d").strftime("%d %b %Y")
    except (ValueError, TypeError):
        return value or "—"

def time_display(value):
    if value is None:
        return "Walk-in"
    try:
        return datetime.strptime(str(value), "%H:%M").strftime("%I:%M %p")
    except (ValueError, TypeError):
        return value or "—"

def datetime_display(value):
    if not value:
        return "—"
    try:
        stamp = datetime.fromisoformat(str(value))
        if stamp.tzinfo is None:
            stamp = stamp.replace(tzinfo=timezone.utc)
        return stamp.astimezone(facility_now().tzinfo).strftime("%d %b %Y, %I:%M %p")
    except ValueError:
        return value

def parse_money(value):
    try:
        amount = Decimal(str(value))
        if not amount.is_finite() or amount < 0 or amount > 10000000:
            return None
        return amount.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    except (InvalidOperation, TypeError, ValueError):
        return None

def to_minor(value):
    amount = parse_money(value)
    if amount is None:
        raise ValueError("Enter a valid non-negative amount.")
    return int(amount * 100)

def from_minor(value):
    return Decimal(value or 0) / 100

def bill_totals(conn, bill_id):
    total = conn.execute("SELECT COALESCE(SUM(amount_cents), 0) FROM bill_items WHERE bill_id = ?", (bill_id,)).fetchone()[0]
    paid = conn.execute("SELECT COALESCE(SUM(amount_cents), 0) FROM payments WHERE bill_id = ?", (bill_id,)).fetchone()[0]
    balance = total - paid
    return {"total": from_minor(total), "paid": from_minor(paid), "balance": from_minor(balance),
            "status": "Paid" if total > 0 and balance <= 0 else ("Partial" if paid > 0 else "Unpaid")}

def parse_medicines(value):
    if not value:
        return []
    if value.lstrip().startswith("["):
        result = json.loads(value)
        return result if isinstance(result, list) else []
    medicines = []
    for line in value.splitlines():
        parts = line.split("|")
        if len(parts) != 7:
            raise ValueError("A legacy medicine record is malformed; review it before migration.")
        medicines.append(dict(zip(("name", "dosage", "frequency", "morning", "afternoon", "evening", "meal"), parts)))
    return medicines

def age_display(value):
    return "—" if value is None else value


def patient_input(form):
    values = {key: form.get(key,'').strip() for key in ('name','age','gender','contact','address','allergies','medical_history')}
    values['gender'] = values['gender'] or 'Not recorded'
    error = validate_patient_input(values['name'],values['age'],values['gender'],values['contact'])
    if error:
        raise ValueError(error)
    if len(values['address'])>2000 or len(values['allergies'])>2000 or len(values['medical_history'])>10000:
        raise ValueError('Patient notes exceed the allowed length.')
    values['age'] = int(values['age']) if values['age'] else None
    return values


def medicine_form(form):
    try:
        count = int(form.get("medicine_count", "0"))
    except ValueError:
        raise ValueError("Invalid medicine count.")
    if not 0 <= count <= 50:
        raise ValueError("Use at most 50 medicines.")
    result = []
    for i in range(1, count + 1):
        item = {}
        for key in ("name", "dosage", "frequency", "route", "duration", "strength"):
            value = form.get(f"medicine_{key}_{i}", "").strip()
            if len(value) > 200:
                raise ValueError(f"Medicine {i}: {key} is too long.")
            item[key] = value
        if not any(item.values()):
            continue
        if not all(item[k] for k in ("name", "dosage", "frequency")):
            raise ValueError(f"Medicine {i}: name, dose and frequency are required.")
        item["meal"] = form.get(f"medicine_meal_{i}", "any")
        if item["meal"] not in {"before", "after", "any"}:
            raise ValueError("Choose a valid meal instruction.")
        for key in ("morning", "afternoon", "evening"):
            item[key] = "1" if form.get(f"medicine_{key}_{i}") else "0"
        result.append(item)
    return result

def validate_booking(conn, doctor_id, date, time_slot, hospital_id):
    doctor = conn.execute("SELECT id FROM doctors WHERE id=? AND hospital_id=? AND active=1", (doctor_id, hospital_id)).fetchone()
    if not doctor:
        return "This doctor is disabled or unavailable for booking."
    try:
        datetime.strptime(date, "%Y-%m-%d")
        if time_slot is not None:
            datetime.strptime(time_slot, "%H:%M")
    except (ValueError, TypeError):
        return "Enter a valid appointment date and time."
    absent = conn.execute("SELECT id FROM doctor_absences WHERE doctor_id = ? AND hospital_id = ? AND start_date <= ? AND end_date >= ?",
                          (doctor_id, hospital_id, date, date)).fetchone()
    if absent:
        return "The doctor is away on this date. Choose another day."
    return None

def page_query(conn, query, params=(), size=25, key="page"):
    sorts = {
        "admin.view_patients": {"name": "Name", "age": "Age", "id": "Record number"},
        "admin.view_doctors": {"name": "Name", "specialization": "Specialization", "id": "Record number"},
        "admin.view_appointments": {"date": "Date", "patient_name": "Patient", "status": "Status"},
        "doctor.doctor_appointments": {"date": "Date", "patient_name": "Patient", "status": "Status"},
        "doctor.all_patients_history": {"name": "Name", "last_visit": "Last visit"},
        "admin.billing": {"id": "Bill number", "patient_name": "Patient", "balance_cents": "Balance"},
        "admin.pharmacy": {"name": "Name", "stock_qty": "Stock", "unit_price_cents": "Price"},
        "admin.audit_log_view": {"id": "Event number", "created_at": "Time", "action": "Action"},
    }.get(request.endpoint, {})
    chosen = request.args.get("sort")
    direction = "DESC" if request.args.get("direction") == "desc" else "ASC"
    if chosen in sorts:
        query = f"SELECT * FROM ({query}) sorted_rows ORDER BY {chosen} {direction}, id {direction}"
    g.sort_options = sorts
    try:
        page = max(1, int(request.args.get(key, "1")))
    except ValueError:
        page = 1
    count = conn.execute("SELECT COUNT(*) FROM (" + query + ") page_count", params).fetchone()[0]
    pages = max(1, math.ceil(count / size))
    page = min(page, pages)
    rows = conn.execute(query + " LIMIT ? OFFSET ?", (*params, size, (page - 1) * size)).fetchall()
    def link(p):
        args = request.args.to_dict()
        args[key] = p
        return url_for(request.endpoint, **(request.view_args or {}), **args)
    pager = {"page": page, "pages": pages, "total": count, "start": (page - 1) * size + 1 if count else 0,
             "end": min(page * size, count), "prev": link(page - 1) if page > 1 else None,
             "next": link(page + 1) if page < pages else None}
    g.setdefault("pagers", []).append(pager)
    return rows


def visit_period_filter():
    """An optional list scope; it never restricts booking or completion."""
    period=request.args.get('period','all')
    today=facility_now().strftime('%Y-%m-%d')
    if period=='today':
        return ' AND a.date=?',(today,)
    if period=='upcoming':
        return ' AND a.date>?',(today,)
    if period=='past':
        return ' AND a.date<?',(today,)
    return '',()


def validate_patient_input(name, age, gender, contact):
    """Server-side mirror of the patient form rules. Returns error string or None."""
    if not name:
        return 'Patient name is required'
    if len(name) > 120 or len(contact) > 30:
        return 'Name or contact is too long'
    if age in (None, ''):
        age = None
    try:
        age_n = int(age) if age is not None else None
    except (TypeError, ValueError):
        return 'Age must be a whole number'
    if age_n is not None and not 0 <= age_n <= 130:
        return 'Age must be between 0 and 130'
    if gender not in VALID_GENDERS:
        return 'Please select a valid gender'
    return None


def validate_doctor_input(name, specialization, experience, consultation_fee, contact):
    """Server-side mirror of the doctor form rules. Returns error string or None."""
    if not all([name, specialization]):
        return 'Name and specialization are required'
    if len(name) > 120 or len(specialization) > 120 or len(contact) > 30:
        return 'One of the text fields is too long'
    try:
        exp_n = int(experience)
    except (TypeError, ValueError):
        return 'Experience must be a whole number of years'
    if not 0 <= exp_n <= 60:
        return 'Experience must be between 0 and 60 years'
    try:
        fee_n = parse_money(consultation_fee)
    except (TypeError, ValueError):
        return 'Consultation fee must be a number'
    if fee_n is None or fee_n > 1000000:
        return 'Consultation fee is out of range'
    return None


def validate_slot_input(start_time, end_time, break_start, break_end):
    """Validate HH:MM slot configuration. Returns error string or None."""
    fmt = '%H:%M'
    try:
        start = datetime.strptime(start_time, fmt)
        end = datetime.strptime(end_time, fmt)
    except (TypeError, ValueError):
        return 'Start and end times are required (HH:MM)'
    if start >= end:
        return 'End time must be after start time'
    if break_start or break_end:
        try:
            bs = datetime.strptime(break_start, fmt)
            be = datetime.strptime(break_end, fmt)
        except (TypeError, ValueError):
            return 'Break times must both be set or both empty'
        if not (start <= bs < be <= end):
            return 'Break must fall inside working hours'
    return None


def generate_time_slots(start_time, end_time, break_start=None, break_end=None, interval=15):
    slots = []
    current_time = datetime.strptime(start_time, '%H:%M')
    end_time = datetime.strptime(end_time, '%H:%M')
    
    while current_time < end_time:
        slot_end = current_time + timedelta(minutes=interval)
        
        if break_start and break_end:
            break_start_time = datetime.strptime(break_start, '%H:%M')
            break_end_time = datetime.strptime(break_end, '%H:%M')
            if current_time < break_end_time and slot_end > break_start_time:
                current_time = break_end_time
                continue
        
        if slot_end <= end_time:
            # Format time in hh:mm am/pm format
            start_str = current_time.strftime('%I:%M %p').lower()
            end_str = slot_end.strftime('%I:%M %p').lower()
            slots.append({
                'start': current_time.strftime('%H:%M'),  # Keep 24h format for storage
                'end': slot_end.strftime('%H:%M'),
                'display_start': start_str,
                'display_end': end_str
            })
        current_time = slot_end
    
    return slots

