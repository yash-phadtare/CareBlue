"""Registration regressions use an empty temporary database, never local accounts."""
from concurrent.futures import ThreadPoolExecutor
from html.parser import HTMLParser
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch

from careblue import create_app
from careblue.migrations import migrate
from database import get_db_connection

PASSWORD = 'Registration-test-password-42'


class Inputs(HTMLParser):
    def __init__(self, html):
        super().__init__()
        self.fields = {}
        self.feed(html)

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == 'input' and attrs.get('name'):
            self.fields[attrs['name']] = attrs


class Registration(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.app = create_app({'TESTING': True, 'SECRET_KEY': 'registration-tests',
            'DATABASE_URL': None, 'DATABASE_PATH': str(Path(self.temp.name) / 'test.db'),
            'REGISTRATION_ENABLED': True, 'SESSION_COOKIE_SECURE': False})
        with self.app.app_context():
            migrate()
        self.client = self.app.test_client()
        self.form = dict(name='New Owner', email='owner@example.test', hospital_name='New Hospital',
                         slug='new-hospital', password=PASSWORD, confirm_password=PASSWORD)

    def tearDown(self):
        self.temp.cleanup()

    def rows(self, sql):
        with self.app.app_context():
            conn = get_db_connection()
            try:
                return conn.execute(sql).fetchall()
            finally:
                conn.close()

    def submit(self, client=None, ajax=False, **changes):
        client = client or self.client
        page = client.get('/register')
        token = Inputs(page.get_data(as_text=True)).fields['csrf_token']['value']
        return client.post('/register', data={**self.form, **changes, 'csrf_token': token},
                           headers={'X-Requested-With': 'CareBlue'} if ajax else {})

    def test_ajax_signup_redirects_to_workspace_login_and_new_owner_can_sign_in(self):
        response = self.submit(ajax=True)
        self.assertEqual(response.status_code, 200)
        result = response.get_json()
        self.assertEqual(result, {'redirect': '/h/new-hospital/login'})
        page = self.client.get(result['redirect'])
        self.assertIn(b'New Hospital', page.data)
        token = Inputs(page.get_data(as_text=True)).fields['csrf_token']['value']
        login = self.client.post(result['redirect'], data={'username': self.form['email'],
            'password': PASSWORD, 'user_type': 'admin', 'csrf_token': token},
            headers={'X-Requested-With': 'CareBlue'})
        self.assertEqual(login.get_json(), {'redirect': '/admin/dashboard'})
        self.assertEqual(self.client.get('/hospital/settings').status_code, 200)

    def test_validation_retains_non_password_fields_without_reflecting_passwords(self):
        form = {**self.form, 'name': 'Owner "<example>"', 'confirm_password': 'different'}
        response = self.submit(**form)
        self.assertEqual(response.status_code, 422)
        fields = Inputs(response.get_data(as_text=True)).fields
        for name in ('name', 'email', 'hospital_name', 'slug'):
            self.assertEqual(fields[name]['value'], form[name])
        self.assertNotIn(PASSWORD, response.get_data(as_text=True))
        self.assertNotIn('value', fields['password'])
        self.assertEqual(self.rows('SELECT id FROM hospitals'), [])
        self.assertEqual(self.rows('SELECT id FROM staff'), [])

    def test_invalid_addresses_are_rejected_without_creating_accounts(self):
        for slug in ('', 'ab', '-hospital', 'hospital-', 'bad/address', 'a' * 51):
            with self.subTest(slug=slug):
                self.assertEqual(self.submit(slug=slug).status_code, 422)
        self.assertEqual(self.rows('SELECT id FROM hospitals'), [])
        self.assertEqual(self.rows('SELECT id FROM staff'), [])

    def test_duplicate_workspace_keeps_details_and_does_not_create_another_owner(self):
        self.assertEqual(self.submit().status_code, 302)
        response = self.submit(name='Other Owner', email='other@example.test')
        self.assertEqual(response.status_code, 422)
        self.assertIn(b'already taken', response.data)
        fields = Inputs(response.get_data(as_text=True)).fields
        self.assertEqual(fields['name']['value'], 'Other Owner')
        self.assertEqual(fields['email']['value'], 'other@example.test')
        self.assertEqual(len(self.rows('SELECT id FROM hospitals')), 1)
        self.assertEqual(len(self.rows('SELECT id FROM staff')), 1)

    def test_stale_session_does_not_block_public_registration(self):
        with self.client.session_transaction() as session:
            session.update(user_id=999, user_type='admin', hospital_id=999,
                           session_version=1, authenticated_at=int(time.time()))
        self.assertEqual(self.client.get('/register').status_code, 200)
        # A previously opened form can be submitted after its account session expires.
        with self.client.session_transaction() as session:
            session.update(user_id=999, user_type='admin', hospital_id=999, session_version=1,
                           authenticated_at=int(time.time()), _csrf_token='test-csrf')
        response = self.client.post('/register', data={**self.form, 'csrf_token': 'test-csrf'},
                                    headers={'X-Requested-With': 'CareBlue'})
        self.assertEqual(response.get_json(), {'redirect': '/h/new-hospital/login'})
        self.assertEqual(len(self.rows('SELECT id FROM staff')), 1)

    def test_suspended_session_cannot_block_registration_or_access_clinical_pages(self):
        self.submit()
        owner = self.rows('SELECT id,hospital_id FROM staff')[0]
        with self.app.app_context():
            conn = get_db_connection()
            conn.execute("UPDATE hospitals SET status='Suspended'")
            conn.commit(); conn.close()
        for path, expected in (('/admin/dashboard', 403), ('/register', 200)):
            with self.client.session_transaction() as session:
                session.update(user_id=owner['id'], user_type='admin', hospital_id=owner['hospital_id'],
                               session_version=1, authenticated_at=int(time.time()))
            self.assertEqual(self.client.get(path).status_code, expected)

    def test_actual_session_expiry_is_marked_explicitly_for_ajax(self):
        with self.client.session_transaction() as session:
            session['_csrf_token'] = 'test-csrf'
        response = self.client.post('/admin/add_patient', data={'name': 'Unsaved', 'csrf_token': 'test-csrf'},
                                    headers={'X-Requested-With': 'CareBlue'})
        self.assertTrue(response.get_json()['session_expired'])
        self.assertEqual(self.rows('SELECT id FROM patients'), [])

    def test_registration_is_atomic_when_audit_write_fails(self):
        with patch('careblue.tenancy.log_audit', side_effect=RuntimeError('audit unavailable')):
            self.assertEqual(self.submit().status_code, 500)
        self.assertEqual(self.rows('SELECT id FROM hospitals'), [])
        self.assertEqual(self.rows('SELECT id FROM staff'), [])

    def test_competing_submissions_create_only_one_workspace(self):
        with ThreadPoolExecutor(max_workers=2) as pool:
            responses = list(pool.map(lambda _: self.submit(client=self.app.test_client()), range(2)))
        self.assertEqual(sorted(response.status_code for response in responses), [302, 422])
        self.assertEqual(len(self.rows('SELECT id FROM hospitals')), 1)
        self.assertEqual(len(self.rows('SELECT id FROM staff')), 1)
