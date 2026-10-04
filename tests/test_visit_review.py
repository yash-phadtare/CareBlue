"""Visit resolution and same-account form recovery use disposable hospital fixtures."""
import json
import re
import unittest
from urllib.parse import urlencode
from tests.test_workflows import SQLiteWorkflows, PASSWORD
from careblue.workflows import visit_schedule_revision
from database import get_db_connection


class VisitReviewTests(unittest.TestCase):
    postgres = False
    setUp = SQLiteWorkflows.setUp
    tearDown = SQLiteWorkflows.tearDown
    rows = SQLiteWorkflows.rows
    post = SQLiteWorkflows.post
    login_as = SQLiteWorkflows.login_as
    create_bill = SQLiteWorkflows.create_bill
    rx_form = SQLiteWorkflows.rx_form

    def revision(self, visit=1):
        return visit_schedule_revision(self.rows('SELECT * FROM appointments WHERE id=?', (visit,))[0])

    def change(self, **values):
        data = {'action': 'reschedule', 'reason': 'Patient requested a later time',
                'revision': self.revision(), 'date': self.date, 'time_slot': '10:30'}
        data.update(values)
        return self.post('/admin/visits/1', data, follow=False)

    def test_reschedule_preserves_record_and_audits_reason(self):
        before = self.rows('SELECT * FROM appointments WHERE id=1')[0]
        response = self.change()
        self.assertEqual(response.status_code, 302)
        after = self.rows('SELECT * FROM appointments WHERE id=1')[0]
        self.assertEqual(after['time_slot'], '10:30')
        for key in ('patient_id', 'doctor_id', 'status'):
            self.assertEqual(after[key], before[key])
        audit = self.rows("SELECT * FROM audit_log WHERE action='visit_reschedule'")[0]
        detail = json.loads(audit['detail'])
        self.assertEqual(detail['reason'], 'Patient requested a later time')
        self.assertEqual(detail['before']['time'], '09:00')
        self.assertEqual(detail['after']['time'], '10:30')

    def test_cancel_retains_record_and_stale_or_clinical_changes_are_rejected(self):
        revision = self.revision()
        self.assertEqual(self.change(action='complete').status_code, 422)
        self.assertEqual(self.change(reason='').status_code, 422)
        self.assertEqual(self.change(action='cancel').status_code, 302)
        self.assertEqual(self.rows('SELECT status FROM appointments WHERE id=1')[0]['status'], 'Cancelled')
        self.assertEqual(self.change(revision=revision).status_code, 422)
        self.assertEqual(len(self.rows("SELECT id FROM audit_log WHERE action='visit_cancel'")), 1)

    def test_conflicts_absences_past_dates_and_stale_edits_do_not_change_visit(self):
        with self.app.app_context():
            c = get_db_connection()
            c.execute("INSERT INTO appointments(patient_id,doctor_id,hospital_id,date,time_slot) VALUES (2,1,1,?,'10:30')", (self.date,))
            c.commit(); c.close()
        self.assertEqual(self.change().status_code, 422)
        self.assertEqual(self.change(date='2000-01-01').status_code, 422)
        self.assertEqual(self.change(revision='stale').status_code, 422)
        with self.app.app_context():
            c = get_db_connection()
            c.execute('INSERT INTO doctor_absences(doctor_id,hospital_id,start_date,end_date,reason) VALUES (1,1,?,?,?)', (self.date, self.date, 'Away'))
            c.commit(); c.close()
        self.assertEqual(self.change(time_slot='11:30').status_code, 422)
        self.assertEqual(self.rows('SELECT time_slot FROM appointments WHERE id=1')[0]['time_slot'], '09:00')

    def test_linked_bill_or_prescription_protects_schedule(self):
        self.create_bill()
        self.assertEqual(self.change().status_code, 422)
        self.assertEqual(self.change(action='cancel').status_code, 422)
        page = self.client.get('/admin/visits/1')
        self.assertEqual(page.status_code, 200)
        self.assertIn(b'Open bill', page.data)
        self.assertNotIn(b'id="visitDate"', page.data)
        self.login_as('doctor', 2)
        self.post('/doctor/prescriptions/2', self.rx_form(0, 'save'))
        self.login_as('admin', 1)
        response = self.post('/admin/visits/2', {'action': 'cancel', 'reason': 'Request', 'revision': self.revision(2)}, follow=False)
        self.assertEqual(response.status_code, 422)

    def test_role_tenant_and_return_context(self):
        self.assertEqual(self.client.get('/admin/visits/3').status_code, 404)
        self.assertEqual(self.post('/admin/visits/3', {'action': 'cancel'}, follow=False).status_code, 404)
        context = '/admin/dashboard?schedule=Scheduled'
        page = self.client.get('/admin/visits/1?' + urlencode({'return_to': context}))
        self.assertIn(b'/admin/dashboard?schedule=Scheduled', page.data)
        response = self.post('/admin/visits/1?' + urlencode({'return_to': context}),
                             {'action': 'cancel', 'reason': 'Patient cancelled', 'revision': self.revision()}, follow=False)
        self.assertIn('return_to=', response.location)
        with self.app.app_context():
            c = get_db_connection()
            c.execute("UPDATE staff SET role='reception' WHERE id=1")
            c.commit(); c.close()
        self.assertEqual(self.client.get('/admin/visits/2').status_code, 200)
        with self.app.app_context():
            c = get_db_connection()
            c.execute("UPDATE staff SET role='billing' WHERE id=1")
            c.commit(); c.close()
        self.assertEqual(self.client.get('/admin/visits/2').status_code, 403)
        self.assertEqual(self.post('/admin/visits/2', {'action': 'cancel'}, follow=False).status_code, 403)

    def identity(self):
        page = self.client.get('/admin/dashboard').get_data(as_text=True)
        return re.search(r'name="form-identity" content="([^"]+)"', page)[1]

    def test_recovery_requires_original_identity_and_refreshes_csrf(self):
        identity = self.identity()
        with self.client.session_transaction() as session:
            session.clear()
        headers = {'X-Form-Identity': identity, 'X-Requested-With': 'CareBlue'}
        response = self.client.post('/admin/add_patient', data={'name': 'Must not save', 'csrf_token': 'test-csrf'}, headers=headers)
        self.assertTrue(response.get_json()['session_expired'])
        self.assertEqual(self.client.get('/session/recover', headers=headers).status_code, 401)
        page = self.client.get('/h/hospital-1/login').get_data(as_text=True)
        token = re.search(r'name="csrf-token" content="([^"]+)"', page)[1]
        self.client.post('/h/hospital-1/login', data={'csrf_token': token, 'username': 'admin1@example.test', 'password': PASSWORD, 'user_type': 'admin'})
        restored = self.client.get('/session/recover', headers=headers)
        self.assertEqual(restored.status_code, 200)
        token = restored.get_json()['csrf_token']
        self.assertNotEqual(token, 'test-csrf')
        self.assertFalse(self.rows("SELECT id FROM patients WHERE name='Must not save'"))
        response = self.client.post('/admin/add_patient', data={'csrf_token': token, 'name': 'Recovered form'}, headers=headers)
        self.assertEqual(response.status_code, 200)
        self.assertTrue(self.rows("SELECT id FROM patients WHERE name='Recovered form'"))

    def test_recovery_rejects_different_hospital_account_and_forged_token(self):
        identity = self.identity()
        self.login_as('admin', 2)
        # Fixture helper is intended for actor 1/3; explicitly set hospital 2 for staff 2.
        with self.client.session_transaction() as session:
            session['hospital_id'] = 2
        headers = {'X-Form-Identity': identity, 'X-Requested-With': 'CareBlue'}
        self.assertEqual(self.client.get('/session/recover', headers=headers).status_code, 403)
        response = self.client.post('/admin/add_patient', data={'name': 'Wrong hospital', 'csrf_token': 'test-csrf'}, headers=headers)
        self.assertEqual(response.status_code, 403)
        self.assertFalse(self.rows("SELECT id FROM patients WHERE name='Wrong hospital'"))
        self.login_as('admin', 1)
        self.assertEqual(self.client.get('/session/recover', headers={'X-Form-Identity': 'forged'}).status_code, 403)


if __name__ == '__main__':
    unittest.main()
