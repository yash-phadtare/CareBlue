"""Idempotent demo dataset for the bundled demo hospital (admin@hospital.com).

Only runs when that hospital has no doctors AND no patients, so real
deployments are never touched. Safe to call on every startup.
"""
import logging
import sqlite3
from datetime import datetime, timedelta

from werkzeug.security import generate_password_hash

from CareBlue.database import get_db_path

logger = logging.getLogger(__name__)
DEMO_EMAIL = 'admin@hospital.com'


def ensure_demo_data():
    db_path = get_db_path()
    conn = sqlite3.connect(db_path, timeout=20)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("PRAGMA foreign_keys = ON")
        demo = conn.execute('SELECT id FROM staff WHERE email = ?', (DEMO_EMAIL,)).fetchone()
        if not demo:
            return False
        hid = demo['id']
        if conn.execute('SELECT COUNT(*) FROM doctors WHERE hospital_id = ?', (hid,)).fetchone()[0]:
            return False
        if conn.execute('SELECT COUNT(*) FROM patients WHERE hospital_id = ?', (hid,)).fetchone()[0]:
            return False

        # ---- Doctors (+ logins for two, so the doctor portal is demoable) ----
        doctors = [
            ('Meera Nair', 'Cardiology', 12, 800, '+91 98200 11223', 'Interventional cardiologist.', 'dr.meera'),
            ('Arjun Desai', 'Orthopedics', 8, 600, '+91 98200 44556', 'Sports injuries and joint care.', 'dr.arjun'),
            ('Kavya Reddy', 'Pediatrics', 6, 500, '+91 98200 77889', 'Child care and immunization.', None),
            ('Rohan Malhotra', 'General Medicine', 15, 400, '+91 98200 99001', 'Family physician.', None),
        ]
        doc_ids = []
        for name, spec, exp, fee, contact, bio, username in doctors:
            cur = conn.execute('''
                INSERT INTO doctors (name, specialization, experience, consultation_fee,
                                     contact, bio, username, password, created_by, hospital_id)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''', (name, spec, exp, fee, contact, bio, username,
                  generate_password_hash('doctor123') if username else None, hid, hid))
            doc_ids.append(cur.lastrowid)

        # ---- Weekly hours: Mon–Fri 09:00–17:00 (lunch 13:00–14:00), Sat morning ----
        for did in doc_ids:
            for day in ['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday']:
                conn.execute('''
                    INSERT INTO doctor_slots (doctor_id, day_of_week, start_time, end_time,
                                              break_start, break_end, hospital_id)
                    VALUES (?, ?, '09:00', '17:00', '13:00', '14:00', ?)
                ''', (did, day, hid))
            conn.execute('''
                INSERT INTO doctor_slots (doctor_id, day_of_week, start_time, end_time, hospital_id)
                VALUES (?, 'Saturday', '10:00', '13:00', ?)
            ''', (did, hid))

        # ---- Patients ----
        patients = [
            ('Aarav Sharma', 32, 'Male', '+91 98111 22334', 'MG Road, Bengaluru', 'Hypertension. Allergic to penicillin.'),
            ('Priya Iyer', 28, 'Female', '+91 98111 55667', 'Anna Nagar, Chennai', ''),
            ('Vikram Singh', 45, 'Male', '+91 98111 88990', 'Sector 62, Noida', 'Type 2 diabetes.'),
            ('Ananya Gupta', 7, 'Female', '+91 98111 33445', 'Park Street, Kolkata', 'Asthma. Uses inhaler.'),
            ('Rahul Verma', 54, 'Male', '+91 98111 66778', 'Andheri West, Mumbai', ''),
            ('Sneha Kulkarni', 36, 'Female', '+91 98111 99002', 'Kothrud, Pune', 'Migraine history.'),
        ]
        pat_ids = []
        for name, age, gender, contact, address, history in patients:
            cur = conn.execute('''
                INSERT INTO patients (name, age, gender, contact, address, medical_history, created_by, hospital_id)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ''', (name, age, gender, contact, address, history, hid, hid))
            pat_ids.append(cur.lastrowid)

        # ---- Appointments: past (completed), today + future (scheduled) ----
        today = datetime.now().date()
        past = [today - timedelta(days=6), today - timedelta(days=3), today - timedelta(days=1)]
        future = [today + timedelta(days=1), today + timedelta(days=2)]
        appt_ids = []

        def book(pid, did, day, slot, status, notes=''):
            cur = conn.execute('''
                INSERT INTO appointments (patient_id, doctor_id, date, time_slot, status, notes, hospital_id)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            ''', (pid, did, day.isoformat(), slot, status, notes, hid))
            appt_ids.append(cur.lastrowid)
            return cur.lastrowid

        a1 = book(pat_ids[0], doc_ids[0], past[0], '10:00', 'Completed', 'Follow-up in 2 weeks')
        a2 = book(pat_ids[2], doc_ids[3], past[1], '11:30', 'Completed')
        a3 = book(pat_ids[3], doc_ids[2], past[2], '10:30', 'Completed')
        book(pat_ids[1], doc_ids[0], today, '09:30', 'Scheduled')
        book(pat_ids[4], doc_ids[1], today, '10:00', 'Scheduled')
        book(pat_ids[5], doc_ids[0], future[0], '09:00', 'Scheduled')
        book(pat_ids[0], doc_ids[3], future[1], '11:00', 'Scheduled')

        # ---- Prescriptions for the completed visits ----
        conn.execute('''
            INSERT INTO prescriptions (appointment_id, diagnosis, medicines, instructions, hospital_id)
            VALUES (?, ?, ?, ?, ?)
        ''', (a1, 'Stage 1 hypertension. BP 142/92.',
              'Amlodipine|5mg|1-0-0|1|0|0|after\nLosartan|50mg|0-0-1|0|0|1|after',
              'Low-salt diet. Recheck BP in 2 weeks.', hid))
        conn.execute('''
            INSERT INTO prescriptions (appointment_id, diagnosis, medicines, instructions, hospital_id)
            VALUES (?, ?, ?, ?, ?)
        ''', (a2, 'Viral fever. Temperature 101.2F.',
              'Paracetamol|500mg|1-1-1|1|1|1|after',
              'Plenty of fluids and rest.', hid))
        conn.execute('''
            INSERT INTO prescriptions (appointment_id, diagnosis, medicines, instructions, hospital_id)
            VALUES (?, ?, ?, ?, ?)
        ''', (a3, 'Mild asthma exacerbation.',
              'Salbutamol inhaler|100mcg|SOS|0|0|0|after',
              'Avoid dust. Review inhaler technique.', hid))

        # ---- Ward + beds (one occupied) ----
        wcur = conn.execute("INSERT INTO wards (name, ward_type, hospital_id) VALUES ('General Ward A', 'General', ?)",
                            (hid,))
        wid = wcur.lastrowid
        for n in ['B-1', 'B-2', 'B-3', 'B-4', 'B-5', 'B-6']:
            conn.execute('INSERT INTO beds (ward_id, bed_number, hospital_id) VALUES (?, ?, ?)', (wid, n, hid))
        conn.execute("UPDATE beds SET status = 'Occupied', patient_id = ? WHERE ward_id = ? AND bed_number = 'B-2'",
                     (pat_ids[4], wid))

        # ---- Pharmacy stock (one low item on purpose) ----
        for name, strength, unit, stock, reorder, price in [
            ('Paracetamol', '500mg', 'strip of 15', 120, 20, 45.0),
            ('Amoxicillin', '250mg', 'strip of 10', 60, 15, 89.0),
            ('Cetirizine', '10mg', 'strip of 10', 4, 20, 55.0),
            ('ORS', '200ml', 'bottle', 40, 10, 35.0),
            ('Azithromycin', '500mg', 'strip of 5', 25, 10, 140.0),
            ('Metformin', '500mg', 'strip of 20', 80, 25, 70.0),
        ]:
            conn.execute('''
                INSERT INTO medicines (name, strength, unit, stock_qty, reorder_level, unit_price, hospital_id)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            ''', (name, strength, unit, stock, reorder, price, hid))

        # ---- Bills: one paid, one open ----
        bcur = conn.execute('INSERT INTO bills (appointment_id, patient_id, hospital_id, notes) VALUES (?, ?, ?, ?)',
                            (a1, pat_ids[0], hid, 'Consultation + tests'))
        b1 = bcur.lastrowid
        conn.execute('INSERT INTO bill_items (bill_id, label, amount) VALUES (?, ?, ?)',
                     (b1, 'Consultation — Dr. Meera Nair', 800.0))
        conn.execute('INSERT INTO bill_items (bill_id, label, amount) VALUES (?, ?, ?)', (b1, 'ECG', 350.0))
        conn.execute('INSERT INTO payments (bill_id, amount, method) VALUES (?, 1150.0, ?)', (b1, 'UPI'))
        bcur = conn.execute('INSERT INTO bills (patient_id, hospital_id, notes) VALUES (?, ?, ?)',
                            (pat_ids[1], hid, 'Vaccination'))
        b2 = bcur.lastrowid
        conn.execute('INSERT INTO bill_items (bill_id, label, amount) VALUES (?, ?, ?)', (b2, 'Flu vaccine', 950.0))
        conn.execute('INSERT INTO payments (bill_id, amount, method) VALUES (?, 400.0, ?)', (b2, 'Cash'))

        conn.commit()
        logger.info(f"Demo dataset seeded for hospital {hid}")
        return True
    except Exception as e:
        conn.rollback()
        logger.error(f"Demo seed failed: {e}")
        return False
    finally:
        conn.close()
