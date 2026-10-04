"""Integration tests use only temporary SQLite files or isolated PostgreSQL schemas."""
import os
import sqlite3
import tempfile
import time
import unittest
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from pathlib import Path
from unittest.mock import patch
from werkzeug.security import generate_password_hash
from careblue import create_app
from careblue.migrations import migrate
from careblue.services import facility_now, parse_money, bill_totals
from database import get_db_connection, IntegrityError
from careblue.accounts import create_administrator

PASSWORD = "Correct-horse-42-battery"

class SQLiteWorkflows(unittest.TestCase):
    postgres = False

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.database_url = None
        if self.postgres:
            import psycopg
            from psycopg.conninfo import make_conninfo
            self.schema = "careblue_test_" + uuid.uuid4().hex
            with psycopg.connect(os.environ["TEST_POSTGRES_URL"], autocommit=True) as c:
                c.execute('CREATE SCHEMA ' + self.schema)
            self.database_url = make_conninfo(os.environ["TEST_POSTGRES_URL"], options="-csearch_path=" + self.schema)
        self.app = create_app({"TESTING": True, "DATABASE_URL": self.database_url,
            "DATABASE_PATH": str(Path(self.temp.name) / "test.db"), "SECRET_KEY": "test-secret",
            "UPLOAD_FOLDER": str(Path(self.temp.name) / "uploads"), "SESSION_COOKIE_SECURE": False,
            "REGISTRATION_ENABLED": False, "PRODUCTION": False})
        from cryptography.fernet import Fernet
        self.app.config["MFA_ENCRYPTION_KEY"] = Fernet.generate_key().decode()
        with self.app.app_context():
            migrate()
            c = get_db_connection()
            for number in (1, 2):
                create_administrator(c, f"Admin {number}", f"admin{number}@example.test", generate_password_hash(PASSWORD), f"Hospital {number}")
            for number, hospital in ((1, 1), (2, 1), (3, 2)):
                c.execute("INSERT INTO doctors (name, specialization, username, password, hospital_id, consultation_fee_cents) VALUES (?, 'General', ?, ?, ?, 12345)",
                    (f"Doctor {number}", f"doctor{number}", generate_password_hash(PASSWORD), hospital))
                c.execute("INSERT INTO patients (name, age, gender, contact, hospital_id, allergies) VALUES (?, 35, 'Other', '555-0123', ?, 'Penicillin')",
                    (f"Patient {number}", hospital))
            self.date = (facility_now() + timedelta(days=2)).strftime("%Y-%m-%d")
            day = (facility_now() + timedelta(days=2)).strftime("%A")
            c.execute("INSERT INTO doctor_slots (doctor_id, day_of_week, start_time, end_time, break_start, break_end, hospital_id) VALUES (1, ?, '09:00', '17:00', '12:05', '13:00', 1)", (day,))
            for patient, doctor, hospital in ((1, 1, 1), (2, 2, 1), (3, 3, 2)):
                c.execute("INSERT INTO appointments (patient_id, doctor_id, date, time_slot, hospital_id) VALUES (?, ?, ?, '09:00', ?)",
                    (patient, doctor, self.date, hospital))
            c.commit()
            c.close()
        self.client = self.app.test_client()
        self.login_as("admin", 1)

    def tearDown(self):
        self.temp.cleanup()
        if self.postgres:
            import psycopg
            with psycopg.connect(os.environ["TEST_POSTGRES_URL"], autocommit=True) as c:
                c.execute('DROP SCHEMA ' + self.schema + ' CASCADE')

    def login_as(self, role, actor_id, client=None):
        client = client or self.client
        with client.session_transaction() as session:
            session.clear()
            session.update(user_id=actor_id, user_type=role, hospital_id=2 if actor_id == 3 else 1,
                hospital_name="Test Hospital", name="Test User", session_version=1,
                authenticated_at=int(time.time()), _csrf_token="test-csrf")
        return client

    def post(self, path, data=None, client=None, follow=True):
        return (client or self.client).post(path, data={**(data or {}), "csrf_token": "test-csrf"}, follow_redirects=follow)

    def rows(self, sql, params=()):
        with self.app.app_context():
            c = get_db_connection()
            try:
                return c.execute(sql, params).fetchall()
            finally:
                c.close()

    def test_migrations_idempotent_and_ready(self):
        with self.app.app_context():
            migrate()
        self.assertEqual(self.client.get("/ready").status_code, 200)
        from careblue.migrations import LATEST_VERSION
        self.assertEqual(len(self.rows("SELECT * FROM schema_migrations")), LATEST_VERSION)

    def test_admin_pages_render_without_error(self):
        for path in ["/admin/dashboard", "/admin/add_patient", "/admin/add_doctor", "/admin/view_patients",
                     "/admin/view_doctors", "/admin/set_doctor_credentials/1", "/admin/set_doctor_slots/1",
                     "/admin/schedule_appointment", "/admin/view_appointments", "/admin/billing",
                     "/admin/billing/new", "/admin/pharmacy", "/admin/beds", "/admin/audit_log"]:
            with self.subTest(path=path):
                response = self.client.get(path)
                self.assertEqual(response.status_code, 200, response.get_data(as_text=True)[:500])
                self.assertNotIn("Error loading", response.get_data(as_text=True))

    def test_doctor_pages_and_authorization(self):
        self.login_as("doctor", 1)
        for path in ["/doctor/dashboard", "/doctor/appointments", "/doctor/patients",
                     "/doctor/patient_history/1", "/doctor/prescriptions/1", "/doctor/print_prescription/1"]:
            with self.subTest(path=path):
                self.assertEqual(self.client.get(path).status_code, 200)
        for path in ["/doctor/prescriptions/2", "/doctor/print_prescription/2", "/doctor/patient_history/2",
                     "/doctor/print_prescription/3", "/doctor/patient_history/3"]:
            response = self.client.get(path)
            self.assertNotIn(b"Patient 2", response.data)
            self.assertNotIn(b"Patient 3", response.data)
        self.assertEqual(self.client.get("/admin/dashboard").status_code, 403)

    def test_csrf_and_logout_method(self):
        self.assertEqual(self.client.post("/admin/add_patient", data={}).status_code, 400)
        self.assertEqual(self.client.get("/logout").status_code, 405)
        self.assertEqual(self.post("/logout", follow=False).status_code, 302)

    def test_disabled_session_revoked(self):
        with self.app.app_context():
            c = get_db_connection()
            c.execute("UPDATE staff SET active=0 WHERE id=1")
            c.commit(); c.close()
        self.assertEqual(self.client.get("/admin/dashboard").status_code, 302)

    def test_money_rejects_nonfinite(self):
        for value in ["NaN", "Infinity", "-1", "10000001", "oops"]:
            self.assertIsNone(parse_money(value))
        self.assertEqual(str(parse_money("0.105")), "0.11")

    def test_timed_booking_is_flexible_but_prevents_collisions(self):
        for slot in ("08:00", "12:00", "12:15", "17:00", "09:01"):
            response = self.post("/admin/schedule_appointment", dict(patient_id="1", doctor_id="1", date=self.date, time_slot=slot))
            self.assertNotIn(b"working hours", response.data)
        self.assertEqual(len(self.rows("SELECT * FROM appointments")), 8)
        self.post("/admin/schedule_appointment", dict(patient_id="1", doctor_id="1", date=self.date, time_slot="09:01"))
        self.assertEqual(len(self.rows("SELECT * FROM appointments")), 8)

    def test_cross_tenant_booking_rejected(self):
        self.post("/admin/schedule_appointment", dict(patient_id="3", doctor_id="1", date=self.date, time_slot="10:00"))
        self.assertEqual(len(self.rows("SELECT * FROM appointments")), 3)

    def test_create_patient_and_fee(self):
        self.post("/admin/add_patient", dict(name="New Patient", age="20", gender="Other", contact="555-1234", allergies="Latex"))
        self.assertEqual(self.rows("SELECT * FROM patients WHERE name='New Patient'")[0]["allergies"], "Latex")
        self.post("/admin/add_doctor", dict(name="New Doctor", specialization="General", experience="5", consultation_fee="12.35", contact="555-1234"))
        self.assertEqual(self.rows("SELECT * FROM doctors WHERE name='New Doctor'")[0]["consultation_fee_cents"], 1235)

    def create_bill(self):
        response = self.post("/admin/billing/create", dict(patient_id="1", appointment_id="1", item_count="1", item_label_1="Test", item_amount_1="0.10"))
        self.assertNotIn(b"Error creating", response.data)
        return self.rows("SELECT * FROM bills")[0]["id"]

    def test_bill_totals_payment_and_double_submit(self):
        bill = self.create_bill()
        with self.app.app_context():
            c = get_db_connection()
            self.assertEqual(str(bill_totals(c, bill)["total"]), "123.55")
            c.close()
        self.post(f"/admin/billing/{bill}/pay", dict(amount="123.55", method="Cash"))
        self.post(f"/admin/billing/{bill}/pay", dict(amount="123.55", method="Cash"))
        self.assertEqual(len(self.rows("SELECT * FROM payments")), 1)
        for path in ["/admin/billing", f"/admin/billing/{bill}", f"/admin/billing/{bill}/invoice"]:
            self.assertEqual(self.client.get(path).status_code, 200)

    def test_parallel_payments_do_not_overpay(self):
        bill = self.create_bill()
        clients = [self.login_as("admin", 1, self.app.test_client()) for _ in range(2)]
        with ThreadPoolExecutor(2) as pool:
            results = list(pool.map(lambda client: self.post(f"/admin/billing/{bill}/pay", dict(amount="100", method="Cash"), client=client), clients))
        self.assertEqual(len(self.rows("SELECT * FROM payments")), 1)

    def test_beds_keep_discharge_history(self):
        self.post("/admin/wards/add", dict(name="Ward", ward_type="General"))
        ward = self.rows("SELECT * FROM wards")[0]["id"]
        self.post("/admin/beds/add", dict(ward_id=str(ward), bed_number="A1"))
        bed = self.rows("SELECT * FROM beds")[0]["id"]
        self.post(f"/admin/beds/{bed}/assign", dict(patient_id="1"))
        self.post(f"/admin/beds/{bed}/discharge")
        admissions = self.rows("SELECT * FROM admissions")
        self.assertEqual(len(admissions), 1)
        self.assertIsNotNone(admissions[0]["discharged_at"])

    def test_stock_adjustments_record_reason(self):
        self.post("/admin/medicines/add", dict(name="Example", stock_qty="10", reorder_level="5", unit_price="0.10"))
        med = self.rows("SELECT * FROM medicines")[0]["id"]
        self.post(f"/admin/medicines/{med}/adjust", dict(delta="-3", reason="Dispensed"))
        self.assertEqual(self.rows("SELECT * FROM medicines")[0]["stock_qty"], 7)
        self.assertEqual(self.rows("SELECT * FROM stock_movements")[0]["reason"], "Dispensed")
        self.post(f"/admin/medicines/{med}/adjust", dict(delta="-50", reason="Correction"))
        self.assertEqual(self.rows("SELECT * FROM medicines")[0]["stock_qty"], 7)

    def rx_form(self, version=0, action="save"):
        return dict(diagnosis="Test diagnosis", medicine_count="1", medicine_name_1="Example | medicine",
            medicine_dosage_1="1 tablet", medicine_frequency_1="Twice daily", medicine_route_1="Oral",
            medicine_duration_1="5 days", medicine_meal_1="after", medicine_morning_1="1",
            version=str(version), action=action, reviewed="1")

    def test_prescription_versions_and_stale_write(self):
        self.login_as("doctor", 1)
        self.assertEqual(self.post("/doctor/prescriptions/1", self.rx_form()).status_code, 200)
        self.assertEqual(self.post("/doctor/prescriptions/1", self.rx_form(), follow=False).status_code, 409)
        self.post("/doctor/prescriptions/1", self.rx_form(1, "sign"))
        self.assertEqual(self.rows("SELECT * FROM prescriptions")[0]["status"], "Signed")
        response = self.post("/doctor/prescriptions/1", self.rx_form(2, "sign"))
        self.assertEqual(response.status_code, 422)
        data = self.rx_form(2, "sign"); data["amendment_reason"] = "Corrected duration"
        self.post("/doctor/prescriptions/1", data)
        self.assertEqual(len(self.rows("SELECT * FROM prescription_versions")), 3)
        self.assertEqual(self.rows("SELECT * FROM prescription_items")[0]["name"], "Example | medicine")
        self.assertIn(b"Amended", self.client.get("/doctor/print_prescription/1").data)

    def test_prescription_history_immutable(self):
        self.login_as("doctor", 1)
        self.post("/doctor/prescriptions/1", self.rx_form())
        with self.app.app_context():
            c = get_db_connection()
            with self.assertRaises(IntegrityError):
                c.execute("DELETE FROM prescription_versions")
            c.rollback(); c.close()

    def test_server_pagination(self):
        with self.app.app_context():
            c = get_db_connection()
            for i in range(60):
                c.execute("INSERT INTO patients (name, age, hospital_id) VALUES (?, 20, 1)", (f"Paging {i:03}",))
            c.commit(); c.close()
        page = self.client.get("/admin/view_patients?search=Paging&page=2").data
        self.assertIn(b"26", page)
        self.assertIn(b"Paging 025", page)
        self.assertNotIn(b"Paging 000", page)

    def test_lookup_is_tenant_scoped(self):
        rows = self.client.get("/admin/lookup/patients?q=Patient").get_json()
        self.assertEqual(len(rows), 2)
        self.assertNotIn("Patient 3", str(rows))

    def test_authentication_and_rate_limit_persist(self):
        self.client.get("/logout")  # GET must not log out.
        with self.client.session_transaction() as session:
            session.clear(); session["_csrf_token"] = "test-csrf"
        for _ in range(5):
            self.post("/login", dict(username="admin1@example.test", password="wrong", user_type="admin"), follow=False)
        response = self.post("/login", dict(username="admin1@example.test", password=PASSWORD, user_type="admin"), follow=False)
        self.assertEqual(response.status_code, 429)
        self.assertEqual(len(self.rows("SELECT * FROM login_attempts")), 1)

    def test_login_success_sets_revocable_session(self):
        with self.client.session_transaction() as session:
            session.clear(); session["_csrf_token"] = "test-csrf"
        response = self.post("/login", dict(username="admin1@example.test", password=PASSWORD, user_type="admin"), follow=False)
        self.assertEqual(response.status_code, 302)
        self.assertEqual(self.client.get("/admin/dashboard").status_code, 200)

    def test_security_headers_and_no_demo_ui(self):
        response = self.client.get("/admin/dashboard")
        self.assertIn("frame-ancestors 'none'", response.headers["Content-Security-Policy"])
        self.assertEqual(response.headers["Cache-Control"], "no-store")
        self.assertNotIn(b"unpkg.com", response.data)
        self.assertNotIn(b"fonts.googleapis", response.data)
        self.post("/logout")
        self.assertNotIn(b"admin123", self.client.get("/login").data)

    def test_ajax_validation_keeps_destination_unconsumed(self):
        response = self.client.post('/admin/add_patient', data={'csrf_token':'test-csrf', 'name':''},
                                    headers={'X-Requested-With':'CareBlue'})
        self.assertEqual(response.status_code, 422)
        self.assertIn('error', response.get_json())

    def test_payment_idempotency(self):
        bill = self.create_bill()
        data = dict(amount='10.10', method='Cash', idempotency_key='same-request')
        self.post(f'/admin/billing/{bill}/pay', data)
        self.post(f'/admin/billing/{bill}/pay', data)
        self.assertEqual(len(self.rows('SELECT * FROM payments')), 1)

    def test_absence_prevents_booking(self):
        from careblue.services import facility_now
        with self.app.app_context():
            day = (facility_now() + timedelta(days=7)).strftime('%Y-%m-%d')
        self.post('/admin/doctors/1/absences', dict(start_date=day, end_date=day, reason='Leave'))
        self.assertEqual(len(self.rows('SELECT * FROM doctor_absences')), 1)
        response = self.post('/admin/schedule_appointment', dict(patient_id='1',doctor_id='1',date=day,time_slot='10:00'))
        self.assertIn(b'away', response.data)

    def test_mfa_code_cannot_be_replayed(self):
        import pyotp
        from careblue.accounts import encrypt_secret, verify_mfa
        with self.app.app_context():
            secret = pyotp.random_base32()
            c = get_db_connection()
            c.execute('UPDATE staff SET totp_secret=? WHERE id=1', (encrypt_secret(secret),))
            c.commit()
            account = c.execute('SELECT * FROM staff WHERE id=1').fetchone()
            c.close()
            code = pyotp.TOTP(secret).now()
            self.assertTrue(verify_mfa(account, code, 'admin'))
            self.assertFalse(verify_mfa(account, code, 'admin'))

    def test_reset_token_one_use(self):
        import hashlib
        with self.app.app_context():
            c = get_db_connection()
            c.execute("INSERT INTO reset_tokens (token_hash,actor_type,actor_id,expires_at) VALUES (?,'admin',1,?)",
                      (hashlib.sha256(b'one-use-token').hexdigest(), int(time.time())+1800))
            c.commit(); c.close()
        data = dict(token='one-use-token',password='New-password-42',confirm_password='New-password-42')
        self.post('/reset-password',data)
        with self.client.session_transaction() as session:
            session['_csrf_token']='test-csrf'
        self.assertEqual(self.post('/reset-password',data,follow=False).status_code,400)

    def test_signed_and_billed_visits_cannot_be_cancelled(self):
        self.login_as('doctor', 1)
        self.post('/doctor/prescriptions/1', self.rx_form(0, 'sign'))
        response = self.post('/doctor/update_appointment_status/1', {'status':'Cancelled'})
        self.assertIn(b'cannot be cancelled', response.data)
        self.assertEqual(self.rows('SELECT status FROM appointments WHERE id=1')[0]['status'], 'Scheduled')
        self.login_as('admin', 1)
        self.create_bill()
        self.login_as('doctor', 1)
        self.post('/doctor/update_appointment_status/1', {'status':'Cancelled'})
        self.assertEqual(self.rows('SELECT status FROM appointments WHERE id=1')[0]['status'], 'Scheduled')

    def test_visits_complete_without_date_restriction_and_cannot_reopen(self):
        self.login_as('doctor', 1)
        response = self.post('/doctor/update_appointment_status/1', {'status':'Completed'})
        self.assertNotIn(b'future visit', response.data)
        self.assertEqual(self.rows('SELECT status FROM appointments WHERE id=1')[0]['status'], 'Completed')
        response = self.post('/doctor/update_appointment_status/1', {'status':'Scheduled'})
        self.assertIn(b'cannot be reopened', response.data)
        self.login_as('doctor', 2)
        self.post('/doctor/update_appointment_status/2', {'status':'Cancelled'})
        response = self.post('/doctor/update_appointment_status/2', {'status':'Scheduled'})
        self.assertIn(b'cannot be reopened', response.data)
        self.assertEqual(self.rows('SELECT status FROM appointments WHERE id=2')[0]['status'], 'Cancelled')

    def test_hours_changes_preserve_existing_bookings(self):
        from datetime import datetime
        day = datetime.strptime(self.date, '%Y-%m-%d').strftime('%A')
        for form in ({'day':day,'day_off':'1'}, {'day':day,'start_time':'10:00','end_time':'17:00'}):
            response = self.post('/admin/set_doctor_slots/1', form)
            self.assertNotIn(b'conflicting scheduled visits', response.data)
            self.assertEqual(self.rows('SELECT time_slot FROM appointments WHERE id=1')[0]['time_slot'], '09:00')
        self.post('/admin/set_doctor_slots/1', {'day':day,'start_time':'09:00','end_time':'18:00'})
        self.assertEqual(self.rows('SELECT end_time FROM doctor_slots WHERE doctor_id=1')[0]['end_time'], '18:00')

    def test_disabled_doctor_cannot_be_booked(self):
        with self.app.app_context():
            c = get_db_connection()
            c.execute('UPDATE doctors SET active=0 WHERE id=1')
            c.commit(); c.close()
        response = self.post('/admin/schedule_appointment', {'patient_id':'1','doctor_id':'1','date':self.date,'time_slot':'10:00'})
        self.assertIn(b'disabled', response.data)
        self.assertEqual(len(self.rows('SELECT * FROM appointments')), 3)

    def test_populated_bed_view_and_archival_preserve_history(self):
        self.post('/admin/wards/add', {'name':'Regression Ward'})
        ward = self.rows('SELECT id FROM wards')[0]['id']
        self.post('/admin/beds/add', {'ward_id':str(ward),'bed_number':'REGRESSION-BED'})
        bed = self.rows('SELECT id FROM beds')[0]['id']
        self.assertIn(b'REGRESSION-BED', self.client.get('/admin/beds').data)
        self.post(f'/admin/beds/{bed}/assign', {'patient_id':'1'})
        self.post(f'/admin/beds/{bed}/discharge')
        self.post(f'/admin/beds/{bed}/delete')
        self.assertEqual(self.rows('SELECT archived FROM beds WHERE id=?', (bed,))[0]['archived'], 1)
        self.assertIn(b'REGRESSION-BED', self.client.get('/admin/admissions').data)
        self.assertNotIn(b'REGRESSION-BED', self.client.get('/admin/beds').data)
        self.post(f'/admin/beds/{bed}/assign', {'patient_id':'1'})
        self.assertEqual(len(self.rows('SELECT * FROM admissions')), 1)

    def test_medicine_archival_preserves_stock_movements(self):
        self.post('/admin/medicines/add', {'name':'Archived Example','stock_qty':'10','unit_price':'12'})
        medicine = self.rows('SELECT id FROM medicines')[0]['id']
        self.post(f'/admin/medicines/{medicine}/adjust', {'delta':'-1','reason':'Dispensed'})
        self.post(f'/admin/medicines/{medicine}/delete')
        self.assertEqual(self.rows('SELECT archived FROM medicines')[0]['archived'], 1)
        self.assertEqual(len(self.rows('SELECT * FROM stock_movements')), 1)
        self.assertNotIn(b'Archived Example', self.client.get('/admin/pharmacy').data)
        self.assertEqual(self.client.get('/admin/lookup/medicines?q=Archived').get_json(), [])
        self.post(f'/admin/medicines/{medicine}/adjust', {'delta':'5','reason':'Retry'})
        self.assertEqual(self.rows('SELECT stock_qty FROM medicines')[0]['stock_qty'], 9)

    def test_signed_identity_snapshot_survives_profile_changes(self):
        self.login_as('doctor', 1)
        self.post('/doctor/prescriptions/1', self.rx_form(0, 'sign'))
        with self.app.app_context():
            c=get_db_connection()
            c.execute("UPDATE patients SET name='Changed Patient',age=99 WHERE id=1")
            c.execute("UPDATE doctors SET name='Changed Doctor' WHERE id=1")
            c.execute("UPDATE hospitals SET name='Changed Hospital' WHERE id=1")
            c.commit(); c.close()
        page = self.client.get('/doctor/print_prescription/1').data
        self.assertIn(b'Patient 1', page)
        self.assertIn(b'Doctor 1', page)
        self.assertIn(b'Hospital 1', page)
        self.assertNotIn(b'Changed Patient', page)
        self.assertNotIn(b'Changed Doctor', page)

    def test_multiple_admins_share_hospital_and_ownership_is_independent(self):
        with self.app.app_context():
            c=get_db_connection()
            actor = create_administrator(c, 'Colleague', 'colleague@example.test', generate_password_hash(PASSWORD), hospital_id=1)
            c.commit(); c.close()
        with self.client.session_transaction() as session:
            session.clear(); session['_csrf_token']='test-csrf'
        response=self.post('/login', {'username':'colleague@example.test','password':PASSWORD,'user_type':'admin'}, follow=False)
        self.assertEqual(response.status_code, 302)
        with self.client.session_transaction() as session:
            self.assertEqual(session['hospital_id'], 1)
            self.assertEqual(session['user_id'], actor)
        patients=self.client.get('/admin/lookup/patients?q=Patient').get_json()
        self.assertEqual(len(patients), 2)

    def test_request_connection_is_reused_and_uncommitted_changes_rollback(self):
        from careblue.routes.blueprints import access
        @self.app.post('/test/uncommitted')
        @access('admin')
        def uncommitted():
            c=get_db_connection()
            self.assertIs(c,get_db_connection())
            c.execute("UPDATE patients SET name='Uncommitted' WHERE id=1")
            c.close()
            self.assertIs(c,get_db_connection())
            return '',204
        self.assertEqual(self.post('/test/uncommitted', follow=False).status_code,204)
        self.assertEqual(self.rows('SELECT name FROM patients WHERE id=1')[0]['name'],'Patient 1')

    def test_undeclared_endpoints_fail_closed_and_doctor_lookup_is_limited(self):
        @self.app.get('/undeclared')
        def undeclared():
            return 'private'
        self.assertEqual(self.client.get('/undeclared').status_code,403)
        self.login_as('doctor',1)
        self.assertEqual(self.client.get('/admin/lookup/patients').status_code,403)
        self.assertEqual(self.client.get('/admin/lookup/medicines').status_code,200)

    def test_mfa_survives_session_key_rotation(self):
        import pyotp
        from careblue.accounts import encrypt_secret, verify_mfa
        with self.app.app_context():
            secret=pyotp.random_base32()
            c=get_db_connection()
            c.execute('UPDATE staff SET totp_secret=? WHERE id=1',(encrypt_secret(secret),))
            c.commit()
            account=c.execute('SELECT * FROM staff WHERE id=1').fetchone(); c.close()
            self.app.config['SECRET_KEY']='rotated-session-key'
            self.assertTrue(verify_mfa(account,pyotp.TOTP(secret).now(),'admin'))

    def test_database_failure_returns_error_instead_of_empty_success(self):
        self.login_as('doctor',1)
        with patch('careblue.routes.doctor.page_query',side_effect=RuntimeError('database unavailable')):
            self.assertEqual(self.client.get('/doctor/appointments').status_code,500)

    def test_payment_token_cannot_be_reused_for_a_different_amount(self):
        bill=self.create_bill()
        self.post(f'/admin/billing/{bill}/pay', {'amount':'10','method':'Cash','idempotency_key':'request'})
        response=self.post(f'/admin/billing/{bill}/pay', {'amount':'20','method':'Cash','idempotency_key':'request'})
        self.assertIn(b'different payment',response.data)
        self.assertEqual(len(self.rows('SELECT * FROM payments')),1)

    def settings_form(self, **changes):
        hospital=self.rows('SELECT * FROM hospitals WHERE id=1')[0]
        fields=('name','brand_color','contact_email','phone','address','timezone','opening_time','closing_time','document_style','document_footer')
        return {**{key:hospital[key] for key in fields},'version':str(hospital['settings_version']),**changes}

    def test_onboarding_creates_isolated_workspace_and_owner(self):
        self.app.config['REGISTRATION_ENABLED']=True
        response=self.post('/register',dict(name='New Owner',email='owner@example.test',password=PASSWORD,confirm_password=PASSWORD,hospital_name='New Hospital',slug='new-hospital'),follow=False)
        self.assertEqual(response.status_code,302)
        self.assertIn('/h/new-hospital',response.headers['Location'])
        hospital=self.rows("SELECT * FROM hospitals WHERE slug='new-hospital'")[0]
        self.assertEqual(hospital['status'],'Trial')
        owner=self.rows('SELECT * FROM staff WHERE hospital_id=?',(hospital['id'],))[0]
        self.assertEqual(owner['role'],'admin')
        self.assertEqual(self.rows('SELECT * FROM patients WHERE hospital_id=?',(hospital['id'],)),[])
        with self.client.session_transaction() as session:
            session['_csrf_token']='test-csrf'
        duplicate=self.post('/register',dict(name='Other',email='other@example.test',password=PASSWORD,confirm_password=PASSWORD,hospital_name='Duplicate',slug='new-hospital'),follow=False)
        self.assertEqual(duplicate.status_code,422)
        self.assertEqual(len(self.rows('SELECT * FROM hospitals')),3)

    def test_workspace_login_scopes_reused_emails_and_usernames(self):
        with self.app.app_context():
            c=get_db_connection()
            create_administrator(c,'Other Admin','admin1@example.test',generate_password_hash(PASSWORD),hospital_id=2)
            c.execute("UPDATE doctors SET username='doctor1' WHERE id=3")
            c.commit();c.close()
        for role,username in [('admin','admin1@example.test'),('doctor','doctor1')]:
            with self.client.session_transaction() as session:
                session.clear();session['_csrf_token']='test-csrf'
            self.assertEqual(self.post('/login',{'username':username,'password':PASSWORD,'user_type':role},follow=False).status_code,401)
            self.assertEqual(self.post('/h/hospital-2',{'username':username,'password':PASSWORD,'user_type':role},follow=False).status_code,302)
            with self.client.session_transaction() as session:
                self.assertEqual(session['hospital_id'],2)
            self.assertNotIn(b'Patient 1',self.client.get('/doctor/patients' if role=='doctor' else '/admin/view_patients').data)

    def test_chosen_workspace_address_does_not_block_later_hospitals(self):
        from careblue.tenancy import onboard
        with self.app.app_context():
            c=get_db_connection()
            first=onboard(c,'Owner','owner@example.test',PASSWORD,'Chosen Address','hospital-4')
            second=onboard(c,'Other','other@example.test',PASSWORD,'Other Address','other-hospital')
            c.commit()
            self.assertEqual(c.execute('SELECT slug FROM hospitals WHERE id=?',(first,)).fetchone()[0],'hospital-4')
            self.assertEqual(c.execute('SELECT slug FROM hospitals WHERE id=?',(second,)).fetchone()[0],'other-hospital')
            # A CLI-created hospital also chooses an available generated address.
            c.execute("UPDATE hospitals SET slug='hospital-5' WHERE id=?",(second,))
            actor=create_administrator(c,'CLI Owner','cli@example.test',generate_password_hash(PASSWORD),hospital_name='CLI Hospital')
            c.commit()
            generated=c.execute('SELECT h.slug FROM hospitals h JOIN staff s ON s.hospital_id=h.id WHERE s.id=?',(actor,)).fetchone()[0]
            self.assertEqual(generated,'hospital-5-1')
            c.close()

    def test_hospital_branding_is_scoped_and_invoices_render_it(self):
        response=self.post('/hospital/settings',self.settings_form(name='Branded Hospital',brand_color='#7431AD',address='Branded Street',phone='12345',document_footer='Thank you from Hospital One',document_style='compact'))
        self.assertEqual(response.status_code,200)
        page=self.client.get('/admin/dashboard').data
        self.assertIn(b'Branded Hospital',page)
        self.assertIn(b'#7431AD',page)
        bill=self.create_bill()
        invoice=self.client.get(f'/admin/billing/{bill}/invoice').data
        self.assertIn(b'Branded Street',invoice)
        self.assertIn(b'Thank you from Hospital One',invoice)
        self.assertIn(b'document-compact',invoice)
        self.login_as('doctor',3)
        other=self.client.get('/doctor/dashboard').data
        self.assertNotIn(b'Branded Hospital',other)
        self.assertNotIn(b'#7431AD',other)
        self.assertEqual(self.rows('SELECT name FROM hospitals WHERE id=2')[0]['name'],'Hospital 2')

    def test_invalid_settings_and_stale_writes_are_rejected(self):
        self.assertEqual(self.post('/hospital/settings',self.settings_form(brand_color='bad'),follow=False).status_code,422)
        self.assertEqual(self.post('/hospital/settings',self.settings_form(timezone='Not/AZone'),follow=False).status_code,422)
        self.assertEqual(self.post('/hospital/settings',self.settings_form(version='0'),follow=False).status_code,409)
        self.assertEqual(self.rows('SELECT settings_version FROM hospitals WHERE id=1')[0]['settings_version'],1)
        response=self.post('/hospital/settings',self.settings_form(timezone='UTC'),follow=False)
        self.assertEqual(response.status_code,422) # Scheduled visits retain their original timezone.

    def test_hospital_timezone_controls_clock_after_safe_update(self):
        from careblue.routes.blueprints import access
        @self.app.get('/test/tenant-clock')
        @access('admin')
        def clock():
            return str(facility_now().tzinfo)
        with self.app.app_context():
            c=get_db_connection();c.execute("UPDATE appointments SET status='Cancelled' WHERE hospital_id=1");c.commit();c.close()
        self.assertEqual(self.post('/hospital/settings',self.settings_form(timezone='America/New_York'),follow=False).status_code,302)
        self.assertEqual(self.client.get('/test/tenant-clock').data,b'America/New_York')

    def test_staff_permissions_and_navigation_follow_database_role(self):
        allowed={'reception':'/admin/view_appointments','billing':'/admin/billing','pharmacy':'/admin/pharmacy'}
        for role,path in allowed.items():
            with self.app.app_context():
                c=get_db_connection();c.execute('UPDATE staff SET role=? WHERE id=1',(role,));c.commit();c.close()
            self.assertEqual(self.client.get(path).status_code,200)
            for forbidden in ['/hospital/settings','/hospital/team','/admin/audit_log','/admin/view_doctors']:
                self.assertEqual(self.client.get(forbidden).status_code,403,(role,forbidden))
            for other_role,other_path in allowed.items():
                if role!=other_role:
                    self.assertEqual(self.client.get(other_path).status_code,403,(role,other_path))
            page=self.client.get(path).data
            self.assertNotIn(b'href="/hospital/settings"',page)
            self.assertNotIn(b'href="/hospital/team"',page)
        self.assertEqual(self.client.get('/admin/lookup/patients').status_code,403)

    def test_team_management_cannot_touch_other_tenant_or_disable_last_admin(self):
        self.assertEqual(self.client.get('/hospital/team').status_code,200)
        self.post('/hospital/team/1',{'role':'reception'})
        self.assertEqual(self.rows('SELECT active,role FROM staff WHERE id=1')[0]['role'],'admin')
        self.post('/hospital/team/2',{'role':'pharmacy'})
        self.assertEqual(self.rows('SELECT active,role FROM staff WHERE id=2')[0]['role'],'admin')
        self.post('/hospital/team',{'name':'Cashier','email':'cashier@example.test','role':'billing','password':PASSWORD})
        member=self.rows("SELECT * FROM staff WHERE email='cashier@example.test'")[0]
        self.assertEqual(member['hospital_id'],1)
        client=self.app.test_client()
        with client.session_transaction() as session:
            session.update(user_id=member['id'],user_type='admin',hospital_id=1,session_version=1,authenticated_at=int(time.time()),_csrf_token='test-csrf')
        self.assertEqual(client.get('/admin/billing').status_code,200)
        self.post(f"/hospital/team/{member['id']}",{'role':'billing'})
        self.assertEqual(client.get('/admin/billing').status_code,302)
        self.assertEqual(self.post('/hospital/doctors/3/access',{'active':'1'},follow=False).status_code,404)

    def test_operator_access_is_separate_from_tenant_data(self):
        with self.app.app_context():
            c=get_db_connection()
            c.execute('INSERT INTO platform_admins(name,email,password) VALUES(?,?,?)',('Operator','operator@example.test',generate_password_hash(PASSWORD)))
            c.commit();c.close()
        self.assertEqual(self.client.get('/platform').status_code,403)
        self.assertEqual(self.post('/login',{'user_type':'platform','username':'operator@example.test','password':PASSWORD},follow=False).status_code,400)
        self.assertEqual(self.post('/platform/login',{'username':'operator@example.test','password':PASSWORD},follow=False).status_code,302)
        with self.client.session_transaction() as session:
            self.assertEqual(session['user_type'],'platform')
            self.assertNotIn('hospital_id',session)
            session['_csrf_token']='test-csrf'
        self.assertEqual(self.client.get('/platform').status_code,200)
        self.assertEqual(self.client.get('/platform/hospitals/1').status_code,200)
        for path in ['/admin/view_patients','/admin/billing','/doctor/prescriptions/1','/hospital/settings','/admin/lookup/medicines']:
            self.assertEqual(self.client.get(path).status_code,403,path)
        response=self.post('/platform/hospitals/1',{'plan':'Growth','status':'Suspended','reason':'Subscription paused','version':'1'},follow=False)
        self.assertEqual(response.status_code,302)
        self.assertEqual(self.rows('SELECT status,plan FROM hospitals WHERE id=1')[0]['status'],'Suspended')
        self.assertEqual(self.rows("SELECT actor_type FROM audit_log WHERE action='subscription_update'")[0]['actor_type'],'platform')
        self.login_as('admin',1)
        self.assertEqual(self.client.get('/admin/dashboard').status_code,403)
        with self.client.session_transaction() as session:
            self.assertNotIn('user_id',session)
            session['_csrf_token']='test-csrf'
        self.assertEqual(self.post('/h/hospital-1',{'user_type':'admin','username':'admin1@example.test','password':PASSWORD},follow=False).status_code,403)
        self.login_as('doctor',3)
        self.assertEqual(self.client.get('/doctor/dashboard').status_code,200)

    def test_platform_admin_bootstrap_validates_password_and_duplicate_email(self):
        runner=self.app.test_cli_runner()
        command=['create-platform-admin','--email','operator@example.test','--name','Operator','--password']
        self.assertNotEqual(runner.invoke(args=command+['short']).exit_code,0)
        self.assertEqual(self.rows('SELECT id FROM platform_admins'),[])
        result=runner.invoke(args=command+[PASSWORD])
        self.assertEqual(result.exit_code,0,result.output)
        self.assertEqual(len(self.rows('SELECT id FROM platform_admins')),1)
        duplicate=runner.invoke(args=command+[PASSWORD])
        self.assertNotEqual(duplicate.exit_code,0)
        self.assertIn('already exists',duplicate.output)
        self.assertEqual(self.rows('SELECT hospital_id FROM audit_log WHERE actor_type=\'platform\'')[0]['hospital_id'],None)

    def test_expired_trial_blocks_workspace(self):
        with self.app.app_context():
            c=get_db_connection();c.execute("UPDATE hospitals SET status='Trial',trial_ends_at='2000-01-01' WHERE id=1");c.commit();c.close()
        self.assertEqual(self.client.get('/admin/dashboard').status_code,403)

    def test_logo_upload_is_validated_and_doctor_images_are_private(self):
        from io import BytesIO
        from PIL import Image
        output=BytesIO();Image.new('RGB',(32,32),'blue').save(output,'PNG');output.seek(0)
        form=self.settings_form()
        form['logo']=(output,'logo.png')
        self.assertEqual(self.post('/hospital/settings',form,follow=False).status_code,302)
        logo=self.rows('SELECT logo_path FROM hospitals WHERE id=1')[0]['logo_path']
        self.assertTrue(logo.startswith('private:tenants/1/logos/'))
        anon=self.app.test_client()
        self.assertEqual(anon.get('/h/hospital-1/logo').status_code,200)
        image=BytesIO();Image.new('RGB',(32,32),'red').save(image,'PNG');image.seek(0)
        self.post('/admin/add_doctor',{'name':'Photo Doctor','specialization':'General','experience':'1','consultation_fee':'10','contact':'123','image':(image,'doctor.png')})
        path=self.rows("SELECT image_path FROM doctors WHERE name='Photo Doctor'")[0]['image_path']
        from urllib.parse import quote
        asset='/hospital/assets/'+quote(path,safe='/')
        self.assertEqual(self.client.get(asset).status_code,200)
        self.assertEqual(anon.get(asset).status_code,302)
        self.login_as('doctor',3)
        self.assertEqual(self.client.get(asset).status_code,404)
        self.assertEqual(anon.get('/static/images/doctors/anything.jpg').status_code,404)

    def test_new_prescriptions_snapshot_custom_document_footer(self):
        self.post('/hospital/settings',self.settings_form(document_footer='Original footer',document_style='compact'))
        self.login_as('doctor',1)
        self.post('/doctor/prescriptions/1',self.rx_form(0,'sign'))
        self.login_as('admin',1)
        self.post('/hospital/settings',self.settings_form(document_footer='Changed footer'))
        self.login_as('doctor',1)
        page=self.client.get('/doctor/print_prescription/1').data
        self.assertIn(b'Original footer',page)
        self.assertNotIn(b'Changed footer',page)
        self.assertIn(b'document-compact',page)

    def test_minimal_patient_and_edit_details_with_conflict_protection(self):
        form={'name':'Minimal Patient','idempotency_key':'register-once','next':'visit'}
        response=self.post('/admin/add_patient',form,follow=False)
        patient=self.rows("SELECT * FROM patients WHERE name='Minimal Patient'")[0]
        self.assertIsNone(patient['age'])
        self.assertEqual(patient['gender'],'Not recorded')
        self.assertIn('patient_id='+str(patient['id']),response.location)
        self.post('/admin/add_patient',form)
        self.assertEqual(len(self.rows("SELECT * FROM patients WHERE name='Minimal Patient'")),1)
        edit={'name':'Minimal Patient','age':'0','version':'1','allergies':'Latex'}
        self.assertEqual(self.post('/admin/edit_patient/'+str(patient['id']),edit,follow=False).status_code,302)
        self.assertEqual(self.rows('SELECT age FROM patients WHERE id=?',(patient['id'],))[0]['age'],0)
        self.assertEqual(self.post('/admin/edit_patient/'+str(patient['id']),edit,follow=False).status_code,409)
        self.assertEqual(self.client.get('/admin/edit_patient/3').status_code,404)

    def test_inline_patient_walk_in_atomic_and_retry_safe(self):
        form={'booking_mode':'walk_in','patient_mode':'new','patient_name':'Inline Patient','doctor_id':'2','idempotency_key':'walk-in-once'}
        self.post('/admin/schedule_appointment',form)
        self.post('/admin/schedule_appointment',form)
        patient=self.rows("SELECT * FROM patients WHERE name='Inline Patient'")
        self.assertEqual(len(patient),1)
        visits=self.rows('SELECT * FROM appointments WHERE patient_id=?',(patient[0]['id'],))
        self.assertEqual(len(visits),1)
        self.assertIsNone(visits[0]['time_slot'])
        with self.app.app_context():
            self.assertEqual(visits[0]['date'],facility_now().strftime('%Y-%m-%d'))
        self.post('/admin/schedule_appointment',{**form,'patient_name':'Orphan Patient','doctor_id':'3','idempotency_key':'wrong-hospital'})
        self.assertEqual(self.rows("SELECT * FROM patients WHERE name='Orphan Patient'"),[])

    def test_walk_in_queue_and_custom_retrospective_times(self):
        for _ in range(2):
            self.post('/admin/schedule_appointment',{'patient_id':'1','doctor_id':'2','booking_mode':'walk_in'})
        self.assertEqual(len(self.rows('SELECT * FROM appointments WHERE time_slot IS NULL')),2)
        self.post('/admin/schedule_appointment',{'patient_id':'1','doctor_id':'2','booking_mode':'timed','date':'2000-01-01','time_slot':'14:07'})
        self.assertEqual(len(self.rows("SELECT * FROM appointments WHERE date='2000-01-01' AND time_slot='14:07'")),1)
        self.assertEqual(self.client.get('/admin/get_doctor_slots/2/'+self.date).status_code,200)
        self.assertTrue(self.client.get('/admin/get_doctor_slots/2/'+self.date).get_json()['slots'])
        self.assertEqual(self.client.get('/admin/get_doctor_slots/3/'+self.date).status_code,404)

    def test_weekly_hours_save_is_atomic_and_keeps_existing_visits(self):
        form={'action':'save_week'}
        for day in ('monday','tuesday','wednesday','thursday','friday','saturday','sunday'):
            form[day+'_start_time']='08:00'
            form[day+'_end_time']='18:00'
        self.post('/admin/set_doctor_slots/1',form)
        self.assertEqual(len(self.rows('SELECT * FROM doctor_slots WHERE doctor_id=1')),7)
        invalid={**form,'monday_start_time':'07:00','sunday_end_time':'06:00'}
        self.post('/admin/set_doctor_slots/1',invalid)
        self.assertEqual(self.rows("SELECT start_time FROM doctor_slots WHERE doctor_id=1 AND day_of_week='Monday'")[0]['start_time'],'08:00')
        self.assertEqual(self.rows('SELECT time_slot FROM appointments WHERE id=1')[0]['time_slot'],'09:00')

    def test_fee_only_bill_collects_once_and_supports_partial_payment(self):
        form={'patient_id':'1','appointment_id':'1','item_count':'1','pay_now':'1','payment_method':'Cash','idempotency_key':'instant-bill'}
        self.post('/admin/billing/create',form)
        self.post('/admin/billing/create',form)
        self.assertEqual(len(self.rows('SELECT * FROM bills')),1)
        self.assertEqual(len(self.rows('SELECT * FROM bill_items')),1)
        self.assertEqual(self.rows('SELECT amount_cents FROM payments')[0]['amount_cents'],12345)
        self.post('/admin/billing/create',{**form,'patient_id':'2','appointment_id':'2','payment_amount':'20','idempotency_key':'partial-bill'})
        bill=self.rows('SELECT id FROM bills WHERE appointment_id=2')[0]['id']
        with self.app.app_context():
            c=get_db_connection()
            self.assertEqual(str(bill_totals(c,bill)['balance']),'103.45')
            c.close()

    def test_instant_billing_rolls_back_invalid_payment_and_partial_items(self):
        form={'patient_id':'1','appointment_id':'1','item_count':'0','pay_now':'1','payment_amount':'200','payment_method':'Cash'}
        self.post('/admin/billing/create',form)
        self.assertEqual(self.rows('SELECT * FROM bills'),[])
        self.assertEqual(self.rows('SELECT * FROM bill_items'),[])
        self.post('/admin/billing/create',{**form,'payment_amount':'20','payment_method':'Invalid'})
        self.assertEqual(self.rows('SELECT * FROM bills'),[])
        self.post('/admin/billing/create',{'patient_id':'1','appointment_id':'1','item_count':'1','item_label_1':'Partially entered'})
        self.assertEqual(self.rows('SELECT * FROM bills'),[])

    def test_prescription_templates_are_personal_and_tenant_scoped(self):
        self.login_as('doctor',1)
        form={**self.rx_form(),'template_name':'Personal Template'}
        response=self.post('/doctor/prescription-templates',form)
        self.assertEqual(response.status_code,200)
        self.assertEqual(response.get_json()['medicines'][0]['name'],'Example | medicine')
        self.assertEqual(self.post('/doctor/prescription-templates',form).status_code,422)
        self.assertIn(b'Personal Template',self.client.get('/doctor/prescriptions/1').data)
        self.login_as('doctor',2)
        self.assertNotIn(b'Personal Template',self.client.get('/doctor/prescriptions/2').data)
        self.login_as('doctor',3)
        self.assertNotIn(b'Personal Template',self.client.get('/doctor/prescriptions/3').data)
        self.login_as('admin',1)
        self.assertEqual(self.post('/doctor/prescription-templates',form).status_code,403)

    def test_advice_only_prescription_updates_history_and_completes_visit(self):
        self.login_as('doctor',1)
        form={'diagnosis':'Clinical findings','instructions':'Follow-up advice','medicine_count':'1','version':'0','action':'sign',
              'reviewed':'1','complete_visit':'1','patient_version':'1','patient_allergies':'Latex','patient_medical_history':'Updated history'}
        self.post('/doctor/prescriptions/1',form)
        self.assertEqual(self.rows('SELECT status FROM appointments WHERE id=1')[0]['status'],'Completed')
        self.assertEqual(self.rows('SELECT medicines FROM prescriptions')[0]['medicines'],'[]')
        patient=self.rows('SELECT * FROM patients WHERE id=1')[0]
        self.assertEqual(patient['medical_history'],'Updated history')
        self.assertEqual(patient['clinical_version'],2)
        self.assertIn('Latex',self.rows('SELECT identity_snapshot FROM prescription_versions')[0]['identity_snapshot'])
        self.assertIn(b'Follow-up advice',self.client.get('/doctor/print_prescription/1').data)

    def test_clinical_history_conflict_does_not_save_prescription(self):
        self.login_as('doctor',1)
        form={**self.rx_form(),'patient_version':'0','patient_allergies':'Overwrite'}
        self.assertEqual(self.post('/doctor/prescriptions/1',form).status_code,409)
        self.assertEqual(self.rows('SELECT * FROM prescriptions'),[])
        self.assertEqual(self.rows('SELECT allergies FROM patients WHERE id=1')[0]['allergies'],'Penicillin')

    def test_sign_next_patient_uses_own_queue_and_completes_current_visit(self):
        self.post('/admin/schedule_appointment',{'patient_id':'2','doctor_id':'2','booking_mode':'walk_in'})
        self.post('/admin/schedule_appointment',{'patient_id':'2','doctor_id':'1','booking_mode':'walk_in'})
        next_id=self.rows('SELECT id FROM appointments WHERE doctor_id=1 AND time_slot IS NULL')[0]['id']
        self.login_as('doctor',1)
        response=self.post('/doctor/prescriptions/1',{**self.rx_form(0,'next'),'complete_visit':'1'},follow=False)
        self.assertTrue(response.location.endswith('/doctor/prescriptions/'+str(next_id)))
        self.assertEqual(self.rows('SELECT status FROM appointments WHERE id=1')[0]['status'],'Completed')
        self.assertEqual(self.rows('SELECT status FROM prescriptions')[0]['status'],'Signed')

    def test_unchanged_user_access_keeps_current_sessions(self):
        self.post('/hospital/team/1',{'role':'admin','active':'1'})
        self.assertEqual(self.client.get('/admin/dashboard').status_code,200)
        self.assertEqual(self.rows('SELECT session_version FROM staff WHERE id=1')[0]['session_version'],1)
        self.post('/hospital/doctors/1/access',{'active':'1'})
        self.assertEqual(self.rows('SELECT session_version FROM doctors WHERE id=1')[0]['session_version'],1)
        self.assertEqual(self.post('/hospital/team/2',{'role':'admin','active':'1'},follow=False).status_code,404)

    def test_single_day_absence_needs_only_start_and_preserves_booked_visits(self):
        self.post('/admin/doctors/1/absences',{'start_date':self.date})
        absence=self.rows('SELECT * FROM doctor_absences')[0]
        self.assertEqual(absence['end_date'],self.date)
        self.assertEqual(absence['reason'],'Unavailable')
        self.assertEqual(self.rows('SELECT status FROM appointments WHERE id=1')[0]['status'],'Scheduled')
        self.post('/admin/schedule_appointment',{'patient_id':'2','doctor_id':'1','date':self.date,'time_slot':'14:07'})
        self.assertEqual(len(self.rows('SELECT * FROM appointments')),3)

    def test_doctor_username_changes_without_forced_password_reset(self):
        original=self.rows('SELECT password FROM doctors WHERE id=1')[0]['password']
        self.post('/admin/set_doctor_credentials/1',{'username':'doctor1'})
        self.assertEqual(self.rows('SELECT session_version FROM doctors WHERE id=1')[0]['session_version'],1)
        self.post('/admin/set_doctor_credentials/1',{'username':'renameddoctor'})
        doctor=self.rows('SELECT * FROM doctors WHERE id=1')[0]
        self.assertEqual(doctor['username'],'renameddoctor')
        self.assertEqual(doctor['password'],original)
        self.assertEqual(doctor['session_version'],2)

    def test_doctor_profile_and_access_create_together_with_optional_fields(self):
        form={'name':'Quick Doctor','specialization':'General','username':'quickdoctor','password':PASSWORD}
        self.post('/admin/add_doctor',form)
        doctor=self.rows("SELECT * FROM doctors WHERE username='quickdoctor'")[0]
        self.assertEqual(doctor['consultation_fee_cents'],0)
        self.assertEqual(doctor['contact'],'')
        self.post('/admin/add_doctor',{**form,'name':'Duplicate Doctor'})
        self.assertEqual(self.rows("SELECT * FROM doctors WHERE name='Duplicate Doctor'"),[])
        self.assertIn(b'Quick Doctor',self.client.get('/hospital/team?search=QUICKDOCTOR').data)
        self.assertNotIn(b'Quick Doctor',self.client.get('/hospital/team?search=not-a-match').data)

    def test_filtered_patient_list_survives_edit_and_registration_to_booking(self):
        from urllib.parse import urlencode, urlsplit, parse_qs
        context='/admin/view_patients?search=Patient&sort=name&direction=desc&page=2'
        query=urlencode({'return_to':context})
        response=self.client.get('/admin/edit_patient/1?'+query)
        self.assertIn(b'Back to results',response.data)
        response=self.post('/admin/edit_patient/1?'+query,{'name':'Updated Patient','version':'1'},follow=False)
        self.assertEqual(response.location,context)
        response=self.post('/admin/add_patient?'+query,{'name':'New Patient','next':'visit'},follow=False)
        self.assertEqual(urlsplit(response.location).path,'/admin/schedule_appointment')
        self.assertEqual(parse_qs(urlsplit(response.location).query)['return_to'],[context])

    def test_collection_booking_links_keep_preselection_and_context_separate(self):
        from html.parser import HTMLParser
        from urllib.parse import parse_qs,urlsplit
        class Links(HTMLParser):
            def __init__(self): super().__init__();self.hrefs=[]
            def handle_starttag(self,tag,attrs):
                if tag=='a': self.hrefs.extend(value for name,value in attrs if name=='href')
        for path,field,value in [('/admin/view_patients?search=Patient','patient_id','1'),('/admin/view_doctors?search=Doctor','doctor_id','1')]:
            parser=Links();parser.feed(self.client.get(path).get_data(as_text=True))
            queries=[parse_qs(urlsplit(link).query) for link in parser.hrefs if urlsplit(link).path=='/admin/schedule_appointment']
            selected=next(query for query in queries if query.get(field)==[value])
            self.assertEqual(selected['return_to'],[path])

    def test_lookup_matches_phone_and_specialty_with_helpful_tenant_scoped_details(self):
        patients=self.client.get('/admin/lookup/patients?q=555').get_json()
        self.assertEqual({row['id'] for row in patients},{1,2})
        self.assertTrue(all(row['detail']=='555-0123' for row in patients))
        doctors=self.client.get('/admin/lookup/doctors?q=General').get_json()
        self.assertEqual({row['id'] for row in doctors},{1,2})
        self.assertTrue(all(row['detail']=='General' for row in doctors))

    def test_collection_mutations_preserve_filtered_return_destination(self):
        from urllib.parse import urlencode
        context='/admin/view_patients?search=Temporary&sort=name&direction=desc'
        self.post('/admin/add_patient',{'name':'Temporary Patient'})
        patient=self.rows("SELECT id FROM patients WHERE name='Temporary Patient'")[0]['id']
        response=self.post(f'/admin/delete_patient/{patient}?'+urlencode({'return_to':context}),follow=False)
        self.assertEqual(response.location,context)
        self.assertEqual(self.rows('SELECT id FROM patients WHERE id=?',(patient,)),[])
        context='/admin/pharmacy?low=1&search=Example&sort=stock_qty'
        response=self.post('/admin/medicines/add?'+urlencode({'return_to':context}),{'name':'Example','stock_qty':'3','reorder_level':'5','unit_price':'1'},follow=False)
        self.assertEqual(response.location,context)

    def test_return_destinations_reject_external_malformed_and_wrong_role_routes(self):
        from urllib.parse import urlencode
        for candidate in ['https://example.test/admin/view_patients','//example.test/admin/view_patients',
                          'https://[invalid','/admin/view_patients\\evil','/admin/view_patients\r\nLocation: evil',
                          '/doctor/appointments','/platform','/admin/view_patients'+'x'*2001]:
            with self.subTest(candidate=candidate):
                response=self.post('/admin/add_patient?'+urlencode({'return_to':candidate}),{'name':'Patient'},follow=False)
                self.assertEqual(response.location,'/admin/view_patients')
        with self.app.app_context():
            c=get_db_connection()
            c.execute("UPDATE staff SET role='reception' WHERE id=1")
            c.commit();c.close()
        response=self.post('/admin/add_patient?'+urlencode({'return_to':'/admin/billing?status=Unpaid'}),{'name':'Patient'},follow=False)
        self.assertEqual(response.location,'/admin/view_patients')

    def test_doctor_draft_preserves_visit_filters_and_does_not_accept_admin_context(self):
        from urllib.parse import urlencode,parse_qs,urlsplit
        self.login_as('doctor',1)
        context='/doctor/appointments?search=Patient&status=Scheduled&period=upcoming&sort=date&direction=desc'
        response=self.post('/doctor/prescriptions/1?'+urlencode({'return_to':context}),self.rx_form(),follow=False)
        self.assertEqual(parse_qs(urlsplit(response.location).query)['return_to'],[context])
        self.assertIn(b'Back to results',self.client.get(response.location).data)
        response=self.post('/doctor/prescriptions/1?'+urlencode({'return_to':'/admin/view_patients'}),self.rx_form(1),follow=False)
        self.assertEqual(response.location,'/doctor/prescriptions/1')

    def test_visit_date_scopes_are_tenant_scoped_and_match_dashboard_past_count(self):
        from flask import template_rendered
        with self.app.app_context():
            today=facility_now().date()
            c=get_db_connection()
            c.execute('UPDATE appointments SET date=? WHERE id=1',(today.isoformat(),))
            c.execute('UPDATE appointments SET date=? WHERE id=2',((today-timedelta(days=1)).isoformat(),))
            c.execute("INSERT INTO appointments (patient_id,doctor_id,date,time_slot,hospital_id) VALUES (2,1,?,'11:00',1)",(self.date,))
            c.commit();c.close()
        captured=[]
        def record(sender,template,context,**extra): captured.append(context)
        template_rendered.connect(record,self.app)
        try:
            for role,path,expected in [('admin','/admin/view_appointments',{'today':{1},'past':{2},'upcoming':{4},'all':{1,2,4}}),
                                      ('doctor','/doctor/appointments',{'today':{1},'past':set(),'upcoming':{4},'all':{1,4}})]:
                self.login_as(role,1)
                for period,ids in expected.items():
                    self.assertEqual(self.client.get(path+'?period='+period).status_code,200)
                    self.assertEqual({row['id'] for row in captured[-1]['appointments']},ids)
            self.login_as('admin',1)
            self.client.get('/admin/dashboard')
            self.assertEqual(captured[-1]['operations']['overdue_visits'],1)
        finally:
            template_rendered.disconnect(record,self.app)

    def test_linked_bill_keeps_selected_visit_and_patient_beyond_initial_options(self):
        from flask import template_rendered
        from urllib.parse import urlencode, parse_qs, urlsplit
        with self.app.app_context():
            c=get_db_connection()
            for number in range(60):
                c.execute("INSERT INTO patients (name,gender,hospital_id) VALUES (?,'Not recorded',1)",(f'A patient {number:02d}',))
            c.execute("UPDATE appointments SET date='2000-01-01' WHERE id=1")
            for number in range(110):
                c.execute("INSERT INTO appointments (patient_id,doctor_id,date,time_slot,hospital_id) VALUES (2,1,?,NULL,1)",(self.date,))
            c.commit();c.close()
        captured=[]
        def record(sender,template,context,**extra): captured.append(context)
        template_rendered.connect(record,self.app)
        try:
            self.assertEqual(self.client.get('/admin/billing/new?appointment_id=1&patient_id=2').status_code,200)
            form=captured[-1]
            self.assertEqual(form['preselect_visit'],'1')
            self.assertEqual(form['preselect_patient'],'1')
            self.assertEqual(len(form['visits']),101)
            self.assertEqual(len(form['patients']),51)
            self.assertIn(1,{row['id'] for row in form['visits']})
            self.assertIn(1,{row['id'] for row in form['patients']})
            self.assertEqual(self.client.get('/admin/billing/new?patient_id=2').status_code,200)
            self.assertEqual(captured[-1]['preselect_patient'],'2')
            self.assertEqual(len(captured[-1]['patients']),51)
            self.assertEqual(self.client.get('/admin/billing/new?appointment_id=3').status_code,404)
            self.client.get('/admin/billing/new?appointment_id='+('9'*100))
            self.assertEqual(captured[-1]['preselect_visit'],'')
        finally:
            template_rendered.disconnect(record,self.app)
        bill=self.create_bill()
        context='/admin/billing?search=Patient&status=Unpaid'
        response=self.client.get('/admin/billing/new?'+urlencode({'appointment_id':1,'return_to':context}))
        self.assertEqual(response.status_code,302)
        self.assertEqual(urlsplit(response.location).path,f'/admin/billing/{bill}')
        self.assertEqual(parse_qs(urlsplit(response.location).query)['return_to'],[context])

    def test_visit_export_contains_filtered_values_and_keeps_notes_literal(self):
        from io import BytesIO
        from zipfile import ZipFile
        from xml.etree import ElementTree as ET
        with self.app.app_context():
            c=get_db_connection()
            c.execute("UPDATE appointments SET date=?,notes='=1+1' WHERE id=1",(facility_now().date().isoformat(),))
            c.execute("UPDATE appointments SET status='Cancelled' WHERE id=2")
            c.commit();c.close()
        response=self.client.get('/admin/export_appointments?search=Patient+1&status=Scheduled&period=today&page=99')
        self.assertEqual(response.status_code,200)
        self.assertIn('appointments.xlsx',response.headers['Content-Disposition'])
        namespace={'s':'http://schemas.openxmlformats.org/spreadsheetml/2006/main'}
        with ZipFile(BytesIO(response.data)) as archive:
            strings=ET.fromstring(archive.read('xl/sharedStrings.xml'))
            values=[''.join(node.itertext()) for node in strings.findall('s:si',namespace)]
            self.assertIn('Patient 1',values)
            self.assertIn('Doctor 1',values)
            self.assertIn('=1+1',values)
            self.assertNotIn('Patient 2',values)
            self.assertNotIn('Patient 3',values)
            sheet=ET.fromstring(archive.read('xl/worksheets/sheet1.xml'))
            self.assertEqual(len(sheet.findall('.//s:row',namespace)),2)
            self.assertEqual(sheet.findall('.//s:f',namespace),[])
            self.assertEqual(sheet.findall('.//s:hyperlink',namespace),[])
        response=self.client.get('/admin/export_appointments?search=not-a-match&period=today')
        with ZipFile(BytesIO(response.data)) as archive:
            sheet=ET.fromstring(archive.read('xl/worksheets/sheet1.xml'))
            self.assertEqual(len(sheet.findall('.//s:row',namespace)),1)

@unittest.skipUnless(os.getenv("TEST_POSTGRES_URL"), "Set TEST_POSTGRES_URL for PostgreSQL integration tests")
class PostgreSQLWorkflows(SQLiteWorkflows):
    postgres = True

if __name__ == "__main__":
    unittest.main()
