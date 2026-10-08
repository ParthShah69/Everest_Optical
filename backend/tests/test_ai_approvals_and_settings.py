"""Security and persistence checks for chat write approval and AI key settings."""

import os
import sys
import unittest
from unittest.mock import patch, Mock

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from app import create_app
from config import Config
from extensions import db, bcrypt
from models.ai_config import AIProviderConfig, AIPendingAction
from models.customer import Customer
from models.inventory import Inventory
from models.order import Order
from models.user import User
from services.ai_service import get_assistant, _safe_context_text, _cooldown_until
from services.ai_secrets import decrypt, encrypt


class TestConfig(Config):
    TESTING = True
    SECRET_KEY = 'approval-test-secret'
    SQLALCHEMY_DATABASE_URI = 'sqlite://'
    SQLALCHEMY_ENGINE_OPTIONS = {}
    AI_PROVIDER = 'ollama'
    AI_STT_BACKEND = 'browser'


class ApprovalAndSettingsTests(unittest.TestCase):
    def test_older_context_redacts_credentials(self):
        self.assertNotIn('private-value', _safe_context_text('create admin with password: private-value'))

    def setUp(self):
        self.app = create_app(TestConfig)
        with self.app.app_context():
            db.create_all()
            admin = User(username='admin-test', role='admin',
                         password_hash=bcrypt.generate_password_hash('test-password').decode())
            staff = User(username='staff-test', role='staff')
            customer = Customer(name='Before Name', phone='9876543210')
            db.session.add_all([admin, staff, customer])
            db.session.commit()
            self.admin_id, self.staff_id, self.customer_id = admin.id, staff.id, customer.id
        self.client = self.app.test_client()
        self._login(self.admin_id)

    def tearDown(self):
        with self.app.app_context():
            db.session.remove()
            db.drop_all()

    def _login(self, user_id):
        with self.client.session_transaction() as sess:
            sess['_user_id'] = str(user_id)
            sess['_fresh'] = True

    def test_mutation_requires_one_time_approval_bound_to_chat(self):
        with self.app.app_context():
            pending = get_assistant()._stage_action(
                'edit_customer', {'customer_id': self.customer_id, 'name': 'After Name'},
                'test-session', self.admin_id)
            action_id = pending.id
            self.assertEqual(db.session.get(Customer, self.customer_id).name, 'Before Name')
        url = f'/api/ai/actions/{action_id}/approve'
        wrong = self.client.post(url, json={'session_id': 'other-session'})
        self.assertEqual(wrong.status_code, 403)
        ok = self.client.post(url, json={'session_id': 'test-session'})
        self.assertEqual(ok.status_code, 200)
        self.assertEqual(self.client.post(url, json={'session_id': 'test-session'}).status_code, 409)
        with self.app.app_context():
            self.assertEqual(db.session.get(Customer, self.customer_id).name, 'After Name')
            self.assertEqual(db.session.get(AIPendingAction, action_id).status, 'completed')

    def test_staff_cannot_approve_admin_action(self):
        with self.app.app_context():
            pending = get_assistant()._stage_action(
                'create_user', {'username': 'new-admin', 'password': 'secret123', 'role': 'admin'},
                'test-session', self.admin_id)
            action_id = pending.id
        self._login(self.staff_id)
        response = self.client.post(f'/api/ai/actions/{action_id}/approve', json={'session_id': 'test-session'})
        self.assertEqual(response.status_code, 409)
        with self.app.app_context():
            self.assertIsNone(User.query.filter_by(username='new-admin').first())

    def test_customer_delete_waits_for_approval(self):
        with self.app.app_context():
            pending = get_assistant()._stage_action(
                'delete_customer', {'customer_id': self.customer_id}, 'test-session', self.admin_id)
            action_id = pending.id
            self.assertFalse(pending.critical)
            self.assertIsNotNone(db.session.get(Customer, self.customer_id))
        response = self.client.post(f'/api/ai/actions/{action_id}/approve', json={'session_id': 'test-session'})
        self.assertEqual(response.status_code, 200)
        with self.app.app_context():
            self.assertIsNone(db.session.get(Customer, self.customer_id))

    def test_inventory_delete_waits_for_approval(self):
        with self.app.app_context():
            item = Inventory(model_name='Test Frame', location='A1', cost_price=1, selling_price=2)
            db.session.add(item)
            db.session.commit()
            item_id = item.id
            pending = get_assistant()._stage_action(
                'delete_inventory_item', {'inventory_id': item_id}, 'test-session', self.admin_id)
            action_id = pending.id
            self.assertIsNotNone(db.session.get(Inventory, item_id))
        response = self.client.post(f'/api/ai/actions/{action_id}/approve', json={'session_id': 'test-session'})
        self.assertEqual(response.status_code, 200)
        with self.app.app_context():
            self.assertIsNone(db.session.get(Inventory, item_id))

    def test_customer_with_orders_needs_typed_critical_confirmation(self):
        with self.app.app_context():
            db.session.add(Order(order_no='ORD-CRITICAL', customer_id=self.customer_id,
                                 total_amount=100))
            db.session.commit()
            pending = get_assistant()._stage_action(
                'delete_customer', {'customer_id': self.customer_id}, 'test-session', self.admin_id)
            action_id = pending.id
            self.assertTrue(pending.critical)
            self.assertEqual(pending.public()['confirmation_phrase'], 'DELETE CUSTOMER')
        url = f'/api/ai/actions/{action_id}/approve'
        self.assertEqual(self.client.post(url, json={'session_id': 'test-session'}).status_code, 400)
        with self.app.app_context():
            self.assertIsNotNone(db.session.get(Customer, self.customer_id))
        response = self.client.post(url, json={'session_id': 'test-session',
                                                'confirmation': 'DELETE CUSTOMER'})
        self.assertEqual(response.status_code, 200)
        with self.app.app_context():
            self.assertEqual(Order.query.count(), 0)

    def test_admin_provider_key_is_encrypted_and_not_displayed(self):
        page = self.client.get('/api/ai/settings')
        self.assertEqual(page.status_code, 200)
        with self.client.session_transaction() as sess:
            token = sess['ai_settings_csrf']
        key = 'test-api-key-never-display'
        response = self.client.post('/api/ai/settings', data={
            'csrf_token': token, 'provider': 'gemini', 'model': 'gemini-test-model',
            'api_key': key, 'priority': '10',
        }, follow_redirects=True)
        self.assertEqual(response.status_code, 200)
        self.assertNotIn(key.encode(), response.data)
        with self.app.app_context():
            row = AIProviderConfig.query.one()
            self.assertNotIn(key, row.encrypted_key)
            self.assertEqual(decrypt(row.encrypted_key), key)
        self._login(self.staff_id)
        self.assertEqual(self.client.get('/api/ai/settings').status_code, 403)

    def test_rate_limited_provider_falls_back_once(self):
        with self.app.app_context():
            db.session.add_all([
                AIProviderConfig(provider='gemini', model='gemini-test', encrypted_key=encrypt('gemini-key'), priority=1),
                AIProviderConfig(provider='groq', model='groq-test', encrypted_key=encrypt('groq-key'), priority=2),
            ])
            db.session.commit()
            _cooldown_until.clear()
            limited = Mock(status_code=429, ok=False, headers={'Retry-After': '5'})
            success = Mock(status_code=200, ok=True)
            success.json.return_value = {'choices': [{'message': {'role': 'assistant', 'content': 'ready'}}]}
            with patch('services.ai_service.requests.post', side_effect=[limited, success]) as post:
                assistant = get_assistant()
                reply = assistant._complete([{'role': 'user', 'content': 'hello'}])
            self.assertEqual(reply['content'], 'ready')
            self.assertEqual(assistant.provider, 'groq')
            self.assertEqual(post.call_count, 2)

    def test_database_reset_requires_typed_confirmation_and_keeps_admin_password(self):
        self.assertEqual(self.client.post('/reset-database', data={}).status_code, 302)
        with self.app.app_context():
            self.assertIsNotNone(db.session.get(Customer, self.customer_id))
        self.client.get('/users')
        with self.client.session_transaction() as sess:
            token = sess['reset_database_csrf']
        response = self.client.post('/reset-database', data={
            'reset_token': token, 'confirmation': 'RESET DATABASE', 'username': 'admin-test',
        })
        self.assertEqual(response.status_code, 302)
        with self.app.app_context():
            self.assertEqual(Customer.query.count(), 0)
            admin = User.query.filter_by(username='admin-test').one()
            self.assertTrue(bcrypt.check_password_hash(admin.password_hash, 'test-password'))
            self.assertIsNone(User.query.filter_by(username='admin').first())


if __name__ == '__main__':
    unittest.main()
