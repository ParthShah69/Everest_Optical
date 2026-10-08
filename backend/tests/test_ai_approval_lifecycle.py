"""Approval lifecycle regressions for stale reviews and one-time decisions."""

import os
import sys
import unittest
from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import patch
import json

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from app import create_app
from config import Config
from extensions import db
from models.ai_config import AIPendingAction
from models.customer import Customer
from models.inventory import Inventory
from models.order import Order, OrderItem
from models.payment import Payment
from models.prescription import Prescription
from models.user import User
from services.ai_service import get_assistant, AIProviderError, _openai_tools


class TestConfig(Config):
    TESTING = True
    SECRET_KEY = 'approval-lifecycle-test-secret'
    SQLALCHEMY_DATABASE_URI = 'sqlite://'
    SQLALCHEMY_ENGINE_OPTIONS = {}
    AI_PROVIDER = 'ollama'
    AI_STT_BACKEND = 'browser'


class ApprovalLifecycleTests(unittest.TestCase):
    def setUp(self):
        self.app = create_app(TestConfig)
        with self.app.app_context():
            db.create_all()
            user = User(username='approval-admin', role='admin')
            customer = Customer(name='Review Customer', phone='9999999999')
            db.session.add_all([user, customer])
            db.session.commit()
            self.user_id, self.customer_id = user.id, customer.id
        self.client = self.app.test_client()
        with self.client.session_transaction() as sess:
            sess['_user_id'] = str(self.user_id)
            sess['_fresh'] = True

    def tearDown(self):
        with self.app.app_context():
            db.session.remove()
            db.drop_all()

    def _decide(self, action_id, decision='approve', **extra):
        return self.client.post(f'/api/ai/actions/{action_id}/{decision}',
                                json={'session_id': 'review-chat', **extra})

    def test_customer_delete_rejects_dependents_added_after_review(self):
        with self.app.app_context():
            pending = get_assistant()._stage_action(
                'delete_customer', {'customer_id': self.customer_id},
                'review-chat', self.user_id)
            self.assertFalse(pending.critical)
            action_id = pending.id
            order = Order(order_no='ORD-NEW-DEPENDENT', customer_id=self.customer_id,
                          total_amount=25)
            db.session.add(order)
            db.session.commit()
            order_id = order.id
        response = self._decide(action_id)
        self.assertEqual(response.status_code, 422)
        self.assertIn('changed after review', response.json['text'])
        with self.app.app_context():
            self.assertIsNotNone(db.session.get(Customer, self.customer_id))
            self.assertIsNotNone(db.session.get(Order, order_id))
            self.assertEqual(db.session.get(AIPendingAction, action_id).status, 'failed')

    def test_inventory_delete_rejects_newly_linked_order_line(self):
        with self.app.app_context():
            item = Inventory(model_name='Review Frame', location='A1', quantity=4,
                             cost_price=5, selling_price=10)
            db.session.add(item)
            db.session.commit()
            item_id = item.id
            pending = get_assistant()._stage_action(
                'delete_inventory_item', {'inventory_id': item_id},
                'review-chat', self.user_id)
            action_id = pending.id
            order = Order(order_no='ORD-NEW-LINE', customer_id=self.customer_id,
                          total_amount=10)
            db.session.add(order)
            db.session.flush()
            line = OrderItem(order_id=order.id, inventory_id=item_id,
                             quantity=1, unit_price=10)
            db.session.add(line)
            db.session.commit()
            line_id = line.id
        self.assertEqual(self._decide(action_id).status_code, 422)
        with self.app.app_context():
            self.assertIsNotNone(db.session.get(Inventory, item_id))
            self.assertEqual(db.session.get(OrderItem, line_id).inventory_id, item_id)

    def test_new_review_supersedes_old_review_in_same_chat(self):
        with self.app.app_context():
            assistant = get_assistant()
            first = assistant._stage_action('edit_customer',
                                            {'customer_id': self.customer_id, 'name': 'Old Choice'},
                                            'review-chat', self.user_id)
            first_id = first.id
            second = assistant._stage_action('edit_customer',
                                             {'customer_id': self.customer_id, 'name': 'New Choice'},
                                             'review-chat', self.user_id)
            second_id = second.id
            self.assertEqual(db.session.get(AIPendingAction, first_id).status, 'superseded')
        self.assertEqual(self._decide(first_id).status_code, 409)
        self.assertEqual(self._decide(second_id).status_code, 200)
        with self.app.app_context():
            self.assertEqual(db.session.get(Customer, self.customer_id).name, 'New Choice')

    def test_expired_action_never_executes(self):
        with self.app.app_context():
            pending = get_assistant()._stage_action(
                'edit_customer', {'customer_id': self.customer_id, 'name': 'Too Late'},
                'review-chat', self.user_id)
            action_id = pending.id
            pending.expires_at = datetime.utcnow() - timedelta(seconds=1)
            db.session.commit()
        self.assertEqual(self._decide(action_id).status_code, 410)
        with self.app.app_context():
            self.assertEqual(db.session.get(Customer, self.customer_id).name, 'Review Customer')

    def test_cancel_is_final_and_cannot_be_approved(self):
        with self.app.app_context():
            pending = get_assistant()._stage_action(
                'edit_customer', {'customer_id': self.customer_id, 'name': 'Cancelled Name'},
                'review-chat', self.user_id)
            action_id = pending.id
        self.assertEqual(self._decide(action_id, 'cancel').status_code, 200)
        self.assertEqual(self._decide(action_id).status_code, 409)
        with self.app.app_context():
            self.assertEqual(db.session.get(Customer, self.customer_id).name, 'Review Customer')

    def test_provider_tool_arguments_accept_only_objects(self):
        with self.app.app_context():
            assistant = get_assistant()
            for raw in ({'customer_id': 1}, '{"customer_id": 1}'):
                call = SimpleNamespace(function=SimpleNamespace(name='edit_customer',
                                                                 arguments=raw))
                self.assertEqual(assistant._tool_call_parts(call)[1], {'customer_id': 1})
            for raw in ('[]', 'not json', ['customer_id']):
                call = SimpleNamespace(function=SimpleNamespace(name='edit_customer',
                                                                 arguments=raw))
                self.assertEqual(assistant._tool_call_parts(call)[1], {})

    def test_internal_order_approval_token_is_not_in_provider_schema(self):
        schemas = {row['function']['name']: row['function']['parameters']
                   for row in _openai_tools()}
        self.assertIn('delete_order', schemas)
        self.assertNotIn('approval_snapshot', schemas['delete_order']['properties'])
        self.assertEqual(schemas['delete_order']['properties']['order_id']['type'], 'integer')

    def test_incomplete_write_is_not_offered_for_approval(self):
        with self.app.app_context():
            with self.assertRaisesRegex(AIProviderError, 'valid details'):
                get_assistant()._stage_action('edit_customer', {},
                                              'review-chat', self.user_id)
            self.assertEqual(AIPendingAction.query.count(), 0)

    def test_staff_does_not_receive_admin_action_approval(self):
        with self.app.app_context():
            staff = User(username='approval-staff', role='staff')
            db.session.add(staff)
            db.session.commit()
            with self.assertRaisesRegex(AIProviderError, 'Only admins'):
                get_assistant()._stage_action('delete_customer',
                                              {'customer_id': self.customer_id},
                                              'review-chat', staff.id)
            self.assertEqual(AIPendingAction.query.count(), 0)
            assistant = get_assistant()
            message = {'role': 'assistant', 'content': None,
                       'tool_calls': [{'id': 'admin-only', 'type': 'function',
                                       'function': {'name': 'delete_customer',
                                                    'arguments': json.dumps({'customer_id': self.customer_id})}}]}
            with patch.object(assistant, '_complete', return_value=message):
                reply = assistant._react_loop([], 'review-chat', staff.id)
            self.assertEqual(reply['error'], 'action_not_ready')
            self.assertIn('Only admins', reply['text'])

    def test_every_model_write_call_stages_without_domain_mutation(self):
        with self.app.app_context():
            item = Inventory(model_name='Original Frame', location='A1', quantity=6,
                             cost_price=5, selling_price=10)
            order = Order(order_no='ORD-MATRIX', customer_id=self.customer_id,
                          total_amount=100, status='Pending')
            db.session.add_all([item, order])
            db.session.commit()
            item_id, order_id = item.id, order.id
            calls = {
                'create_customer': {'name': 'New Customer', 'phone': '8888888888'},
                'edit_customer': {'customer_id': self.customer_id, 'name': 'Changed'},
                'create_order': {'customer_id': self.customer_id,
                                 'items': [{'description': 'Frame', 'quantity': 1, 'unit_price': 10}]},
                'update_order_status': {'order_id': order_id, 'status': 'Delivered'},
                'record_payment': {'order_id': order_id, 'amount': 10},
                'add_prescription': {'customer_id': self.customer_id, 're_sph': -1},
                'add_inventory_item': {'model_name': 'New Frame', 'location': 'B1',
                                       'cost_price': 5, 'selling_price': 10},
                'update_inventory_stock': {'inventory_id': item_id, 'quantity_change': 3},
                'create_user': {'username': 'new-staff', 'password': 'password123'},
                'delete_customer': {'customer_id': self.customer_id},
                'delete_inventory_item': {'inventory_id': item_id},
                'delete_order': {'order_id': order_id},
            }
            assistant = get_assistant()
            for name, args in calls.items():
                with self.subTest(name=name):
                    message = {'role': 'assistant', 'content': None,
                               'tool_calls': [{'id': f'call-{name}', 'type': 'function',
                                               'function': {'name': name,
                                                            'arguments': json.dumps(args)}}]}
                    with patch.object(assistant, '_complete', return_value=message):
                        result = assistant._react_loop([], 'review-chat', self.user_id)
                    self.assertEqual(result['action'], 'pending_approval')
                    self.assertEqual(result['pending_action']['tool_name'], name)
                    self.assertEqual(result['tool_calls'], [])
                    self.assertEqual(Customer.query.count(), 1)
                    self.assertEqual(Order.query.count(), 1)
                    self.assertEqual(Payment.query.count(), 0)
                    self.assertEqual(Prescription.query.count(), 0)
                    self.assertEqual(Inventory.query.count(), 1)
                    self.assertEqual(User.query.count(), 1)
                    self.assertEqual(db.session.get(Customer, self.customer_id).name,
                                     'Review Customer')
                    self.assertEqual(db.session.get(Order, order_id).status, 'Pending')
                    self.assertEqual(db.session.get(Inventory, item_id).quantity, 6)


if __name__ == '__main__':
    unittest.main()
