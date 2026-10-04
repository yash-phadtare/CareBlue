"""Transactional domain operations. Callers supply a connection and actor context."""
import json
from datetime import datetime
from database import lock_record
from careblue.services import facility_now
from careblue.security import log_audit


def change_appointment_status(conn, appointment_id, doctor_id, hospital_id, status):
    if status not in {"Scheduled", "Completed", "Cancelled"}:
        raise ValueError("Choose a valid appointment status.")
    # The same lock serializes booking, prescribing, status and availability changes.
    lock_record(conn, "doctors", doctor_id)
    visit = conn.execute("SELECT * FROM appointments WHERE id=? AND doctor_id=? AND hospital_id=?",
                         (appointment_id, doctor_id, hospital_id)).fetchone()
    if not visit:
        raise ValueError("Appointment not found.")
    if status == visit["status"]:
        return
    if visit["status"] != "Scheduled":
        raise ValueError("Completed and cancelled visits cannot be reopened. Schedule a new visit.")
    if status == "Cancelled":
        signed = conn.execute("SELECT id FROM prescriptions WHERE appointment_id=? AND status IN ('Signed','Amended')",
                              (appointment_id,)).fetchone()
        billed = conn.execute("SELECT id FROM bills WHERE appointment_id=?", (appointment_id,)).fetchone()
        if signed or billed:
            raise ValueError("A signed or billed visit cannot be cancelled.")
    conn.execute("UPDATE appointments SET status=? WHERE id=?", (status, appointment_id))
    log_audit("update_status", "appointment", appointment_id, status, hospital_id=hospital_id, conn=conn)


def replace_doctor_hours(conn, doctor_id, hospital_id, day, start=None, end=None,
                         break_start=None, break_end=None):
    from careblue.services import generate_time_slots, validate_slot_input
    if day not in {"Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"}:
        raise ValueError("Choose a valid day of week.")
    lock_record(conn, "doctors", doctor_id)
    if not conn.execute("SELECT id FROM doctors WHERE id=? AND hospital_id=?", (doctor_id, hospital_id)).fetchone():
        raise ValueError("Doctor not found.")
    if start is not None:
        error = validate_slot_input(start, end, break_start, break_end)
        if error:
            raise ValueError(error)
    conn.execute("DELETE FROM doctor_slots WHERE doctor_id=? AND day_of_week=? AND hospital_id=?", (doctor_id, day, hospital_id))
    if start:
        conn.execute("INSERT INTO doctor_slots (doctor_id,day_of_week,start_time,end_time,break_start,break_end,hospital_id) VALUES (?,?,?,?,?,?,?)",
                     (doctor_id, day, start, end, break_start, break_end, hospital_id))
    log_audit("update_slots" if start else "day_off", "doctor", doctor_id, day, hospital_id=hospital_id, conn=conn)


def prescription_snapshot(appointment):
    keys = ("patient_name", "age", "gender", "date_of_birth", "allergies", "doctor_name",
            "specialization", "hospital_name", "date", "time_slot", "hospital_address", "hospital_phone",
            "hospital_email", "document_footer", "document_style")
    return json.dumps({key: appointment.get(key) for key in keys}, ensure_ascii=False)


def save_prescription_version(conn, appointment, existing, diagnosis, medicines, instructions,
                              version, action, reviewed, reason, doctor_id, hospital_id):
    from careblue.migrations import insert_medicine_items
    if action not in {"save", "sign", "print", "next"}:
        raise ValueError("Choose a valid prescription action.")
    if not diagnosis or len(diagnosis) > 10000 or len(instructions) > 10000 or len(reason) > 2000:
        raise ValueError("Diagnosis is required; notes or amendment reason exceed the allowed length.")
    if appointment["status"] == "Cancelled":
        raise ValueError("A cancelled visit cannot receive a prescription.")
    if version != (existing["version"] if existing else 0):
        raise StalePrescription("This prescription changed in another session. Reload and review before saving.")
    signing = action in {"sign", "print", "next"}
    if signing and not reviewed:
        raise ValueError("Review the patient, allergies and all medicine details before signing.")
    if signing and any(not m["route"] or not m["duration"] for m in medicines):
        raise ValueError("Route and duration are required for every medicine before signing.")
    was_signed = existing and existing["status"] in {"Signed", "Amended"}
    if was_signed and (not signing or not reason):
        raise ValueError("A signed prescription requires a signed amendment with a reason.")
    status = "Amended" if was_signed else ("Signed" if signing else "Draft")
    version += 1
    payload = json.dumps(medicines, ensure_ascii=False)
    if existing:
        conn.execute("UPDATE prescriptions SET diagnosis=?,medicines=?,instructions=?,version=?,status=? WHERE id=?",
                     (diagnosis, payload, instructions, version, status, existing["id"]))
        prescription_id = existing["id"]
    else:
        prescription_id = conn.insert("INSERT INTO prescriptions (appointment_id,diagnosis,medicines,instructions,hospital_id,version,status) VALUES (?,?,?,?,?,?,?)",
                                       (appointment["id"], diagnosis, payload, instructions, hospital_id, version, status)).lastrowid
    revision = conn.insert("INSERT INTO prescription_versions (prescription_id,version,doctor_id,status,diagnosis,instructions,medicines,reason,identity_snapshot) VALUES (?,?,?,?,?,?,?,?,?)",
                           (prescription_id, version, doctor_id, status, diagnosis, instructions, payload, reason, prescription_snapshot(appointment)))
    insert_medicine_items(conn, revision.lastrowid, medicines)
    log_audit("prescription_" + status.lower(), "prescription", prescription_id, f"version={version}", hospital_id=hospital_id, conn=conn)
    return version, status


class StalePrescription(ValueError):
    pass


def visit_schedule_revision(visit):
    """Detect stale scheduling forms without changing the clinical record schema."""
    import hashlib
    values = [visit[key] for key in ('id', 'doctor_id', 'date', 'time_slot', 'status')]
    return hashlib.sha256(json.dumps(values).encode()).hexdigest()


def resolve_visit(conn, appointment_id, hospital_id, action, reason, revision, date='', time_slot=None):
    from careblue.services import validate_booking
    visit = conn.execute('SELECT * FROM appointments WHERE id=? AND hospital_id=?',
                         (appointment_id, hospital_id)).fetchone()
    if not visit:
        raise ValueError('Visit not found.')
    lock_record(conn, 'doctors', visit['doctor_id'])
    visit = conn.execute('SELECT * FROM appointments WHERE id=? AND hospital_id=?',
                         (appointment_id, hospital_id)).fetchone()
    if revision != visit_schedule_revision(visit):
        raise ValueError('This visit changed while you were reviewing it. Open the visit again before making a change.')
    if action not in {'reschedule', 'cancel'}:
        raise ValueError('Choose reschedule or cancel. Clinical completion belongs to the doctor.')
    if visit['status'] != 'Scheduled':
        raise ValueError('Only scheduled visits can be changed. Add a new visit if needed.')
    if not reason.strip() or len(reason) > 1000:
        raise ValueError('Enter a reason of up to 1,000 characters.')
    if (conn.execute('SELECT id FROM prescriptions WHERE appointment_id=?', (appointment_id,)).fetchone()
            or conn.execute('SELECT id FROM bills WHERE appointment_id=?', (appointment_id,)).fetchone()):
        raise ValueError('This visit has a prescription or bill. Ask the assigned doctor or billing team to review it; its schedule cannot be changed here.')
    before = {'date': visit['date'], 'time': visit['time_slot'], 'status': visit['status']}
    if action == 'cancel':
        conn.execute("UPDATE appointments SET status='Cancelled' WHERE id=? AND hospital_id=?", (appointment_id, hospital_id))
        after = {**before, 'status': 'Cancelled'}
    else:
        error = validate_booking(conn, visit['doctor_id'], date, time_slot, hospital_id)
        if error:
            raise ValueError(error)
        if datetime.strptime(date, '%Y-%m-%d').date() < facility_now().date():
            raise ValueError('Choose today or a future date.')
        if date == visit['date'] and time_slot == visit['time_slot']:
            raise ValueError('Choose a different date or time to reschedule this visit.')
        if time_slot is not None and conn.execute("SELECT id FROM appointments WHERE doctor_id=? AND date=? AND time_slot=? AND status!='Cancelled' AND id!=?",
                                                  (visit['doctor_id'], date, time_slot, appointment_id)).fetchone():
            raise ValueError('That time is already booked. Choose another time.')
        conn.execute('UPDATE appointments SET date=?,time_slot=? WHERE id=? AND hospital_id=?', (date, time_slot, appointment_id, hospital_id))
        after = {'date': date, 'time': time_slot, 'status': visit['status']}
    log_audit('visit_' + action, 'appointment', appointment_id,
              json.dumps({'before': before, 'after': after, 'reason': reason.strip()}, ensure_ascii=False),
              hospital_id=hospital_id, conn=conn)


def book_appointment(conn, patient_id, doctor_id, hospital_id, date, time_slot, notes, idempotency_key=None):
    from careblue.services import validate_booking
    if len(notes) > 10000:
        raise ValueError("Appointment notes must be under 10,000 characters.")
    if not conn.execute("SELECT id FROM patients WHERE id=? AND hospital_id=?", (patient_id, hospital_id)).fetchone():
        raise ValueError("Selected patient was not found.")
    if not conn.execute("SELECT id FROM doctors WHERE id=? AND hospital_id=?", (doctor_id, hospital_id)).fetchone():
        raise ValueError("Selected doctor was not found.")
    lock_record(conn, "doctors", doctor_id)
    if idempotency_key:
        previous = conn.execute("SELECT id FROM appointments WHERE hospital_id=? AND idempotency_key=?", (hospital_id, idempotency_key)).fetchone()
        if previous:
            return previous['id']
    error = validate_booking(conn, doctor_id, date, time_slot, hospital_id)
    if error:
        raise ValueError(error)
    if time_slot is not None and conn.execute("SELECT id FROM appointments WHERE doctor_id=? AND date=? AND time_slot=? AND status!='Cancelled'", (doctor_id, date, time_slot)).fetchone():
        raise ValueError("That slot was just booked. Please pick another time.")
    visit_id = conn.insert("INSERT INTO appointments (patient_id,doctor_id,date,time_slot,notes,hospital_id,idempotency_key) VALUES (?,?,?,?,?,?,?)",
                           (patient_id, doctor_id, date, time_slot, notes, hospital_id, idempotency_key)).lastrowid
    log_audit("create", "appointment", visit_id, f"doctor={doctor_id} patient={patient_id} {date} {time_slot}", hospital_id=hospital_id, conn=conn)
    return visit_id


def register_patient(conn, form, hospital_id, actor_id, idempotency_key=None):
    from careblue.services import patient_input
    if idempotency_key:
        previous = conn.execute("SELECT id FROM patients WHERE hospital_id=? AND idempotency_key=?", (hospital_id, idempotency_key)).fetchone()
        if previous:
            return previous['id']
    values = patient_input(form)
    patient_id = conn.insert("INSERT INTO patients(name,age,gender,contact,address,allergies,medical_history,created_by,hospital_id,idempotency_key) VALUES(?,?,?,?,?,?,?,?,?,?)",
        (values['name'],values['age'],values['gender'],values['contact'],values['address'],values['allergies'],values['medical_history'],actor_id,hospital_id,idempotency_key)).lastrowid
    log_audit('create','patient',patient_id,values['name'],hospital_id=hospital_id,conn=conn)
    return patient_id


def create_bill(conn, patient_id, appointment_id, hospital_id, items, notes, idempotency_key):
    from careblue.services import to_minor, from_minor
    if idempotency_key:
        previous = conn.execute("SELECT id FROM bills WHERE hospital_id=? AND idempotency_key=?", (hospital_id, idempotency_key)).fetchone()
        if previous:
            return previous["id"]
    if not conn.execute("SELECT id FROM patients WHERE id=? AND hospital_id=?", (patient_id, hospital_id)).fetchone():
        raise ValueError("Patient not found.")
    items = list(items)
    if appointment_id:
        visit = conn.execute("SELECT a.patient_id,d.name AS doctor_name,d.consultation_fee_cents FROM appointments a JOIN doctors d ON d.id=a.doctor_id WHERE a.id=? AND a.hospital_id=? AND a.status!='Cancelled'", (appointment_id, hospital_id)).fetchone()
        if not visit or str(visit["patient_id"]) != str(patient_id):
            raise ValueError("Visit does not belong to this patient.")
        lock_record(conn, "doctors", conn.execute("SELECT doctor_id FROM appointments WHERE id=?", (appointment_id,)).fetchone()[0])
        # Recheck after locking against concurrent cancellation.
        if conn.execute("SELECT status FROM appointments WHERE id=?", (appointment_id,)).fetchone()[0] == "Cancelled":
            raise ValueError("A cancelled visit cannot be billed.")
        if visit["consultation_fee_cents"]:
            items.insert(0, (f"Consultation — Dr. {visit['doctor_name']}", from_minor(visit["consultation_fee_cents"])))
    if not items:
        raise ValueError("Add at least one positive bill item.")
    bill_id = conn.insert("INSERT INTO bills (appointment_id,patient_id,hospital_id,notes,idempotency_key) VALUES (?,?,?,?,?)",
                          (appointment_id, patient_id, hospital_id, notes, idempotency_key)).lastrowid
    for label, amount in items:
        conn.execute("INSERT INTO bill_items (bill_id,label,amount_cents) VALUES (?,?,?)", (bill_id, label, to_minor(amount)))
    log_audit("create", "bill", bill_id, f"patient={patient_id} items={len(items)}", hospital_id=hospital_id, conn=conn)
    return bill_id


def pay_bill(conn, bill_id, hospital_id, amount, method, idempotency_key):
    from careblue.services import bill_totals, to_minor, parse_money
    from careblue.constants import PAYMENT_METHODS
    amount = parse_money(amount)
    if amount is None or amount <= 0 or method not in PAYMENT_METHODS:
        raise ValueError('Enter a positive payment amount and a valid payment method.')
    if not conn.execute("SELECT id FROM bills WHERE id=? AND hospital_id=?", (bill_id, hospital_id)).fetchone():
        raise ValueError("Bill not found.")
    lock_record(conn, "bills", bill_id)
    if idempotency_key:
        old = conn.execute("SELECT amount_cents,method FROM payments WHERE bill_id=? AND idempotency_key=?", (bill_id, idempotency_key)).fetchone()
        if old:
            if old["amount_cents"] != to_minor(amount) or old["method"] != method:
                raise ValueError("This request token was already used for a different payment.")
            return False
    if amount > bill_totals(conn, bill_id)["balance"]:
        raise ValueError("Payment exceeds the outstanding balance.")
    conn.execute("INSERT INTO payments (bill_id,amount_cents,method,idempotency_key) VALUES (?,?,?,?)", (bill_id, to_minor(amount), method, idempotency_key))
    log_audit("payment", "bill", bill_id, f"{amount:.2f} via {method}", hospital_id=hospital_id, conn=conn)
    return True


def assign_bed(conn, bed_id, patient_id, hospital_id):
    lock_record(conn, "beds", bed_id)
    bed = conn.execute("SELECT * FROM beds WHERE id=? AND hospital_id=? AND archived=0", (bed_id, hospital_id)).fetchone()
    patient = conn.execute("SELECT id,name FROM patients WHERE id=? AND hospital_id=?", (patient_id, hospital_id)).fetchone()
    if not bed or not patient:
        raise ValueError("Bed or patient not found.")
    if bed["status"] != "Available" or bed["patient_id"]:
        raise ValueError("That bed is no longer free.")
    lock_record(conn, "patients", patient_id)
    if conn.execute("SELECT id FROM admissions WHERE patient_id=? AND discharged_at IS NULL", (patient_id,)).fetchone():
        raise ValueError("Patient already occupies another bed.")
    conn.execute("INSERT INTO admissions (bed_id,patient_id,hospital_id) VALUES (?,?,?)", (bed_id, patient_id, hospital_id))
    conn.execute("UPDATE beds SET status='Occupied',patient_id=? WHERE id=?", (patient_id, bed_id))
    log_audit("assign", "bed", bed_id, f"{bed['bed_number']} -> {patient['name']}", hospital_id=hospital_id, conn=conn)


def discharge_bed(conn, bed_id, hospital_id):
    lock_record(conn, "beds", bed_id)
    bed = conn.execute("SELECT * FROM beds WHERE id=? AND hospital_id=? AND archived=0", (bed_id, hospital_id)).fetchone()
    if not bed or bed["status"] != "Occupied":
        raise ValueError("Bed is not occupied.")
    conn.execute("UPDATE admissions SET discharged_at=CURRENT_TIMESTAMP WHERE bed_id=? AND discharged_at IS NULL", (bed_id,))
    conn.execute("UPDATE beds SET status='Available',patient_id=NULL WHERE id=?", (bed_id,))
    log_audit("discharge", "bed", bed_id, bed["bed_number"], hospital_id=hospital_id, conn=conn)
