import sqlite3
from contextlib import closing
import tempfile
import unittest
from pathlib import Path
from werkzeug.security import generate_password_hash
from careblue import create_app
from careblue.migrations import migrate
from database import init_db, get_db_connection
from unittest.mock import patch

class Migrations(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = str(Path(self.temp.name) / "legacy.db")
        self.app = create_app({"TESTING":True, "DATABASE_URL":None, "DATABASE_PATH":self.path, "SECRET_KEY":"test"})
    def tearDown(self):
        self.temp.cleanup()
    def test_no_default_admin_or_import_writes(self):
        self.assertFalse(Path(self.path).exists())
        with self.app.app_context():
            migrate()
            c = get_db_connection()
            self.assertEqual(c.execute("SELECT COUNT(*) FROM staff").fetchone()[0], 0)
            indexes = {r['name'] for r in c.execute("PRAGMA index_list(appointments)").fetchall()}
            self.assertIn("idx_appointments_slot", indexes)
            c.close()

    def test_failed_upgrade_rolls_back_base_and_legacy_changes(self):
        with self.app.app_context():
            with patch('careblue.migrations.architecture', side_effect=RuntimeError('upgrade interrupted')):
                with self.assertRaises(RuntimeError):
                    migrate()
            c=get_db_connection()
            self.assertEqual(c.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall(), [])
            c.close()
            migrate()

    def test_populated_version_three_upgrade_preserves_relationships_and_history(self):
        from careblue.migrations import foundation, clinical, operations
        with self.app.app_context():
            init_db()
            c=get_db_connection()
            foundation(c); clinical(c); operations(c)
            c.execute('CREATE TABLE schema_migrations(version INTEGER PRIMARY KEY)')
            for version in (1,2,3):
                c.execute('INSERT INTO schema_migrations(version) VALUES (?)',(version,))
            c.execute("INSERT INTO staff(id,name,email,password,hospital_name) VALUES(7,'Owner','owner@example.test','hash','Original Hospital')")
            c.execute("INSERT INTO doctors(id,name,specialization,hospital_id,consultation_fee_cents) VALUES(8,'Doctor','General',7,12345)")
            c.execute("INSERT INTO patients(id,name,age,hospital_id) VALUES(9,'Patient',40,7)")
            c.execute("INSERT INTO appointments(id,doctor_id,patient_id,hospital_id,date,time_slot) VALUES(10,8,9,7,'2020-01-01','09:00')")
            c.execute("INSERT INTO prescriptions(id,appointment_id,diagnosis,medicines,hospital_id,version,status) VALUES(11,10,'Diagnosis','[]',7,1,'Signed')")
            c.execute("INSERT INTO prescription_versions(id,prescription_id,version,doctor_id,status,diagnosis,medicines) VALUES(12,11,1,8,'Signed','Diagnosis','[]')")
            c.execute("INSERT INTO wards(id,name,hospital_id) VALUES(13,'Ward',7)")
            c.execute("INSERT INTO beds(id,ward_id,bed_number,hospital_id,status,patient_id) VALUES(14,13,'A1',7,'Occupied',9)")
            c.execute("INSERT INTO admissions(id,bed_id,patient_id,hospital_id) VALUES(15,14,9,7)")
            c.execute("INSERT INTO audit_log(action,entity,hospital_id) VALUES('sign','prescription',7)")
            c.commit(); c.close()
            migrate(); migrate()
            c=get_db_connection()
            self.assertEqual(c.execute('SELECT hospital_id FROM staff WHERE id=7').fetchone()[0],7)
            self.assertEqual(c.execute('SELECT name FROM hospitals WHERE id=7').fetchone()[0],'Original Hospital')
            self.assertEqual(c.execute('SELECT consultation_fee_cents FROM doctors WHERE id=8').fetchone()[0],12345)
            self.assertEqual(c.execute('SELECT bed_id FROM admissions WHERE id=15').fetchone()[0],14)
            self.assertIn('Original Hospital',c.execute('SELECT identity_snapshot FROM prescription_versions WHERE id=12').fetchone()[0])
            for table in ('doctors','patients','appointments','prescriptions','doctor_slots','wards','beds','medicines','bills','admissions','doctor_absences'):
                fk=[r for r in c.execute(f'PRAGMA foreign_key_list({table})').fetchall() if r['from']=='hospital_id']
                self.assertEqual(fk[0]['table'],'hospitals',table)
            self.assertEqual(c.execute('PRAGMA foreign_key_check').fetchall(),[])
            with self.assertRaises(sqlite3.IntegrityError):
                c.execute("UPDATE prescription_versions SET diagnosis='changed' WHERE id=12")
            c.rollback(); c.close()

    def test_seed_demo_uses_current_schema_and_hospital_id(self):
        from careblue.accounts import create_administrator
        from seed_demo import ensure_demo_data
        with self.app.app_context():
            migrate()
            c=get_db_connection()
            actor=create_administrator(c,'Admin','demo@example.test',generate_password_hash('Long-password-123'),hospital_name='Demo')
            hospital=c.execute('SELECT hospital_id FROM staff WHERE id=?',(actor,)).fetchone()[0]
            c.commit(); c.close()
            self.assertTrue(ensure_demo_data(hospital,enabled=True))
            self.assertFalse(ensure_demo_data(hospital,enabled=True))
            c=get_db_connection()
            self.assertEqual(c.execute('SELECT consultation_fee_cents FROM doctors ORDER BY id LIMIT 1').fetchone()[0],80000)
            self.assertEqual(c.execute('SELECT amount_cents FROM payments ORDER BY id LIMIT 1').fetchone()[0],115000)
            self.assertEqual(c.execute('PRAGMA foreign_key_check').fetchall(),[])
            c.close()

    def prepare_version_four(self):
        from careblue.migrations import foundation, clinical, operations, architecture
        init_db()
        c=get_db_connection()
        try:
            c.execute('PRAGMA foreign_keys=OFF')
            c.execute('PRAGMA legacy_alter_table=ON')
            c.execute('BEGIN IMMEDIATE')
            foundation(c); clinical(c); operations(c); architecture(c)
            c.execute('CREATE TABLE schema_migrations(version INTEGER PRIMARY KEY)')
            for version in (1,2,3,4):
                c.execute('INSERT INTO schema_migrations(version) VALUES (?)',(version,))
            c.execute("INSERT INTO hospitals(id,name) VALUES(7,'Existing Hospital')")
            c.execute("INSERT INTO staff(id,name,email,password,hospital_id,active,session_version) VALUES(8,'Owner','owner@example.test','hash',7,0,3)")
            c.execute("INSERT INTO doctors(id,name,specialization,username,hospital_id) VALUES(9,'Doctor','General','existing',7)")
            c.execute("INSERT INTO patients(id,name,age,hospital_id) VALUES(10,'Patient',40,7)")
            c.execute("INSERT INTO appointments(id,doctor_id,patient_id,hospital_id,date,time_slot) VALUES(11,9,10,7,'2020-01-01','09:00')")
            c.commit()
        finally:
            c.close()

    def test_version_four_upgrade_preserves_accounts_and_scopes_login_uniqueness(self):
        with self.app.app_context():
            self.prepare_version_four()
            migrate(); migrate()
            c=get_db_connection()
            owner=c.execute('SELECT * FROM staff WHERE id=8').fetchone()
            self.assertEqual((owner['hospital_id'],owner['active'],owner['session_version'],owner['role']),(7,0,3,'admin'))
            hospital=c.execute('SELECT * FROM hospitals WHERE id=7').fetchone()
            self.assertEqual((hospital['slug'],hospital['status'],hospital['name']),('hospital-7','Active','Existing Hospital'))
            self.assertEqual(c.execute('SELECT patient_id FROM appointments WHERE id=11').fetchone()[0],10)
            c.execute("INSERT INTO hospitals(id,name,slug) VALUES(12,'Other Hospital','other')")
            c.execute("INSERT INTO staff(name,email,password,hospital_id) VALUES('Other','owner@example.test','hash',12)")
            c.execute("INSERT INTO doctors(name,specialization,username,hospital_id) VALUES('Other Doctor','General','existing',12)")
            c.commit()
            for sql in ("INSERT INTO staff(name,email,password,hospital_id) VALUES('Duplicate','owner@example.test','hash',7)",
                        "INSERT INTO doctors(name,specialization,username,hospital_id) VALUES('Duplicate','General','existing',7)"):
                with self.assertRaises(sqlite3.IntegrityError):
                    c.execute(sql)
                c.rollback()
            self.assertEqual(c.execute('PRAGMA foreign_key_check').fetchall(),[])
            c.close()

    def test_failed_version_five_upgrade_rolls_back_and_can_retry(self):
        from careblue.migrations import multitenancy, columns
        with self.app.app_context():
            self.prepare_version_four()
            def interrupted(conn):
                multitenancy(conn)
                raise RuntimeError('upgrade interrupted')
            with patch('careblue.migrations.multitenancy',side_effect=interrupted):
                with self.assertRaises(RuntimeError):
                    migrate()
            c=get_db_connection()
            self.assertNotIn('slug',columns(c,'hospitals'))
            self.assertNotIn('role',columns(c,'staff'))
            self.assertEqual(c.execute('SELECT MAX(version) FROM schema_migrations').fetchone()[0],4)
            self.assertEqual(c.execute('SELECT active FROM staff WHERE id=8').fetchone()[0],0)
            c.close()
            migrate()
            c=get_db_connection()
            from careblue.migrations import LATEST_VERSION
            self.assertEqual(c.execute('SELECT MAX(version) FROM schema_migrations').fetchone()[0],LATEST_VERSION)
            self.assertEqual(c.execute('PRAGMA foreign_key_check').fetchall(),[])
            c.close()

    def test_failed_version_six_upgrade_preserves_schema_and_records(self):
        from careblue.migrations import efficient_workflows, columns
        with self.app.app_context():
            self.prepare_version_four()
            with patch('careblue.migrations.efficient_workflows',side_effect=RuntimeError('defer version six')):
                with self.assertRaises(RuntimeError):
                    migrate()
            # First reach version five to exercise a populated incremental upgrade.
            with patch('careblue.migrations.efficient_workflows'):
                migrate()
            c=get_db_connection()
            c.execute('DELETE FROM schema_migrations WHERE version=6')
            c.commit();c.close()
            def interrupted(conn):
                efficient_workflows(conn)
                raise RuntimeError('upgrade interrupted')
            with patch('careblue.migrations.efficient_workflows',side_effect=interrupted):
                with self.assertRaises(RuntimeError):
                    migrate()
            c=get_db_connection()
            self.assertEqual(c.execute('SELECT MAX(version) FROM schema_migrations').fetchone()[0],5)
            self.assertNotIn('clinical_version',columns(c,'patients'))
            self.assertEqual(c.execute('SELECT patient_id FROM appointments WHERE id=11').fetchone()[0],10)
            c.close()
            migrate();migrate()
            c=get_db_connection()
            try:
                self.assertEqual(c.execute('SELECT age FROM patients WHERE id=10').fetchone()[0],40)
                c.execute("INSERT INTO patients(name,age,hospital_id) VALUES('Unknown age',NULL,7)")
                for _ in range(2):
                    c.execute("INSERT INTO appointments(doctor_id,patient_id,hospital_id,date,time_slot) VALUES(9,10,7,'2020-01-01',NULL)")
                self.assertEqual(c.execute('PRAGMA foreign_key_check').fetchall(),[])
                with self.assertRaises(sqlite3.IntegrityError):
                    c.execute("INSERT INTO appointments(doctor_id,patient_id,hospital_id,date,time_slot) VALUES(9,10,7,'2020-01-01','09:00')")
                c.rollback()
            finally:
                c.close()

    def test_upgrade_does_not_reuse_deleted_patient_or_visit_numbers(self):
        with self.app.app_context():
            self.prepare_version_four()
            c=get_db_connection()
            c.execute("INSERT INTO patients(id,name,age,hospital_id) VALUES(1000,'Deleted patient',40,7)")
            c.execute('DELETE FROM patients WHERE id=1000')
            c.execute("INSERT INTO appointments(id,doctor_id,patient_id,hospital_id,date,time_slot) VALUES(1000,9,10,7,'2020-01-01','10:00')")
            c.execute('DELETE FROM appointments WHERE id=1000')
            c.commit();c.close()
            migrate()
            c=get_db_connection()
            try:
                patient=c.insert("INSERT INTO patients(name,age,hospital_id) VALUES('Next patient',NULL,7)").lastrowid
                visit=c.insert("INSERT INTO appointments(doctor_id,patient_id,hospital_id,date,time_slot) VALUES(9,10,7,'2020-01-01',NULL)").lastrowid
                self.assertEqual(patient,1001)
                self.assertEqual(visit,1001)
            finally:
                c.rollback();c.close()

    def test_postgres_marker_translation_preserves_literals_and_comments(self):
        from database import postgres_sql
        self.assertEqual(postgres_sql("SELECT '?' AS label, ? AS value -- ?\n/* ? */"), "SELECT '?' AS label, %s AS value -- ?\n/* ? */")
    def test_populated_legacy_prescriptions_and_money(self):
        with self.app.app_context():
            init_db()
        with closing(sqlite3.connect(self.path)) as c, c:
            c.execute("INSERT INTO staff (id,name,email,password,hospital_name) VALUES (1,'Admin','admin@hospital.com',?,'Hospital')", (generate_password_hash("admin123"),))
            c.execute("INSERT INTO doctors (id,name,specialization,hospital_id,consultation_fee) VALUES (1,'Doctor','General',1,123.45)")
            c.execute("INSERT INTO patients (id,name,age,hospital_id) VALUES (1,'Patient',30,1)")
            c.execute("INSERT INTO appointments (id,patient_id,doctor_id,date,time_slot,hospital_id) VALUES (1,1,1,'2025-01-01','10:00',1)")
            c.execute("DROP TABLE prescriptions")
            c.execute("CREATE TABLE prescriptions (id INTEGER PRIMARY KEY, appointment_id INTEGER UNIQUE, diagnosis TEXT, medicines TEXT, instructions TEXT, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)")
            c.execute("INSERT INTO prescriptions (id,appointment_id,diagnosis,medicines,instructions) VALUES (1,1,'Original diagnosis','Example|1 tablet|daily|1|0|0|after','Original instructions')")
        with self.app.app_context():
            migrate(); migrate()
            c = get_db_connection()
            prescription = c.execute("SELECT * FROM prescriptions").fetchone()
            self.assertEqual(prescription['hospital_id'], 1)
            self.assertEqual(prescription['diagnosis'], "Original diagnosis")
            self.assertEqual(prescription['version'], 1)
            self.assertEqual(c.execute("SELECT * FROM doctors").fetchone()['consultation_fee_cents'], 12345)
            self.assertEqual(c.execute("SELECT active FROM staff").fetchone()[0], 0)
            self.assertEqual(c.execute("SELECT COUNT(*) FROM prescription_items").fetchone()[0], 1)
            self.assertEqual(c.execute("PRAGMA foreign_key_check").fetchall(), [])
            c.close()

if __name__ == '__main__':
    unittest.main()

