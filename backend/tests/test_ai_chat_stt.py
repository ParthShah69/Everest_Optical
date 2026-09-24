"""Focused smoke tests for AI chat/STT safeguards (run with unittest)."""

import io
import json
import os
import sys
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from app import create_app
from config import Config
from extensions import db
from models.customer import Customer
from models.inventory import Inventory
from models.order import Order
from models.payment import Payment
from models.user import User
from models.chat_history import ChatMessage
from services.ai_service import AIAssistant
from services.ai_tools import (
    create_order, get_customers_details, get_order_details, search_inventory,
    search_orders, update_order_status,
)
from services.stt_service import STTService


class TestConfig(Config):
    TESTING = True
    SECRET_KEY = 'test-secret'
    SQLALCHEMY_DATABASE_URI = 'sqlite://'
    SQLALCHEMY_ENGINE_OPTIONS = {}
    AI_PROVIDER = 'ollama'
    AI_STT_BACKEND = 'browser'


class AiChatSttSmokeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = create_app(TestConfig)

    def setUp(self):
        with self.app.app_context():
            db.drop_all()
            db.create_all()
            self.user = User(username='chat-test', role='admin')
            self.customer = Customer(name='Chat Customer', phone='9876543210')
            self.inventory = Inventory(model_name='Chat Frame', location='A1', cost_price=100, selling_price=200, quantity=3)
            db.session.add_all([self.user, self.customer, self.inventory])
            db.session.commit()
            self.user_id, self.customer_id, self.inventory_id = self.user.id, self.customer.id, self.inventory.id

    def test_order_tool_uses_sequence_tax_and_delivery_stock_rule(self):
        with self.app.app_context():
            created = json.loads(create_order(
                self.customer_id,
                [{'description': 'Chat Frame', 'quantity': 2, 'unit_price': 200, 'inventory_id': self.inventory_id}],
                status='Delivered', tax_mode='calculated', tax_percent=5,
            ))
            self.assertTrue(created['success'])
            self.assertEqual(created['order']['order_no'], 'ORD-0001')
            self.assertEqual(created['order']['tax_amount'], 20.0)
            self.assertEqual(db.session.get(Inventory, self.inventory_id).quantity, 1)

            status = json.loads(update_order_status(created['order']['id'], 'Processing'))
            self.assertTrue(status['success'])
            self.assertEqual(db.session.get(Inventory, self.inventory_id).quantity, 3)

    def test_read_only_bill_customer_and_filter_tools(self):
        with self.app.app_context():
            created = json.loads(create_order(
                self.customer_id,
                [{'description': 'Chat Frame', 'quantity': 1, 'unit_price': 200, 'inventory_id': self.inventory_id}],
                tax_mode='calculated', tax_percent=5,
            ))
            order = db.session.get(Order, created['order']['id'])
            db.session.add(Payment(
                order_id=order.id, receipt_no='RCP-0001', amount=100,
                payment_method='UPI', payment_type='partial', remark='Test receipt',
            ))
            db.session.commit()

            bill = json.loads(get_order_details(order_no=created['order']['order_no']))
            self.assertTrue(bill['success'])
            self.assertEqual(bill['order']['tax_amount'], 10.0)
            self.assertEqual(bill['order']['remaining_due'], 110.0)
            self.assertEqual(bill['items'][0]['inventory_id'], self.inventory_id)
            self.assertEqual(bill['payments'][0]['receipt_no'], 'RCP-0001')

            due = json.loads(search_orders(due_only=True, customer_id=self.customer_id))
            self.assertTrue(due['success'])
            self.assertEqual(due['orders'][0]['id'], order.id)
            self.assertEqual(due['orders'][0]['remaining_due'], 110.0)

            compared = json.loads(get_customers_details([self.customer_id, 999999]))
            self.assertTrue(compared['success'])
            self.assertEqual(compared['customers'][0]['total_due'], 110.0)
            self.assertFalse(compared['customers'][1]['found'])

            inventory = json.loads(search_inventory(item_type='frame', in_stock_only=True))
            self.assertTrue(inventory['success'])
            self.assertEqual(inventory['items'][0]['item_type'], 'Frame')

    def test_route_validation_and_stt_temp_suffix(self):
        client = self.app.test_client()
        with client.session_transaction() as session:
            session['_user_id'] = str(self.user_id)
            session['_fresh'] = True
        self.assertEqual(client.post('/api/ai/chat', json={'message': 'x', 'session_id': '../bad'}).status_code, 400)
        self.assertEqual(client.get('/api/ai/history?session_id=x&limit=bad').status_code, 400)
        response = client.post('/api/ai/transcribe', data={'audio': (io.BytesIO(b'a' * 700), 'clip.txt')})
        self.assertEqual(response.status_code, 415)

        with self.app.app_context(), patch.object(STTService, '_transcribe_whisper') as transcribe:
            transcribe.side_effect = lambda path, lang: {'text': 'ok', 'language': lang or 'en', 'confidence': 1, 'error': None}
            result = STTService(backend='whisper_local').transcribe(b'a' * 700, 'hi-IN', filename='clip.wav')
            self.assertEqual(result['text'], 'ok')
            self.assertEqual(transcribe.call_args.args[1], 'hi')
            self.assertTrue(transcribe.call_args.args[0].endswith('.wav'))

    def test_saved_sessions_are_private_and_history_can_be_resumed(self):
        with self.app.app_context():
            other = User(username='other-chat-user', role='staff')
            db.session.add(other)
            db.session.flush()
            db.session.add_all([
                ChatMessage(session_id='sess-old', user_id=self.user_id, role='user', content='Show Priya bill'),
                ChatMessage(session_id='sess-old', user_id=self.user_id, role='assistant', content='Here is the bill.'),
                ChatMessage(session_id='sess-new', user_id=self.user_id, role='user', content='Check low stock frames'),
                ChatMessage(session_id='sess-other', user_id=other.id, role='user', content='Private conversation'),
            ])
            db.session.commit()

        client = self.app.test_client()
        with client.session_transaction() as session:
            session['_user_id'] = str(self.user_id)
            session['_fresh'] = True
        response = client.get('/api/ai/sessions')
        self.assertEqual(response.status_code, 200)
        sessions = response.get_json()['sessions']
        self.assertEqual({item['session_id'] for item in sessions}, {'sess-old', 'sess-new'})
        old = next(item for item in sessions if item['session_id'] == 'sess-old')
        self.assertEqual(old['title'], 'Show Priya bill')
        self.assertEqual(old['message_count'], 2)
        history = client.get('/api/ai/history?session_id=sess-old&limit=20').get_json()['messages']
        self.assertEqual([message['content'] for message in history], ['Show Priya bill', 'Here is the bill.'])

    def test_llm_availability_requires_configured_model_when_list_is_available(self):
        with self.app.app_context():
            assistant = AIAssistant()
            assistant.model = 'qwen3:4b'
            assistant._client = SimpleNamespace(list=lambda: SimpleNamespace(models=[SimpleNamespace(model='other:latest')]))
            self.assertFalse(assistant.is_available())
            assistant._client = SimpleNamespace(list=lambda: SimpleNamespace(models=[SimpleNamespace(model='qwen3:4b')]))
            self.assertTrue(assistant.is_available())

    def test_groq_adapter_checks_health_and_builds_openai_tools(self):
        with self.app.app_context(), patch('services.ai_service.requests.get') as get_request, \
                patch('services.ai_service.requests.post') as post_request:
            assistant = AIAssistant()
            assistant.provider = 'groq'
            assistant.model = 'demo-model'
            assistant.groq_api_key = 'test-key'
            assistant.groq_base_url = 'https://api.groq.test/openai/v1'

            get_request.return_value = SimpleNamespace(ok=True)
            self.assertTrue(assistant.is_available())
            self.assertIn('/models/demo-model', get_request.call_args.args[0])

            post_request.return_value = SimpleNamespace(
                ok=True,
                json=lambda: {'choices': [{'message': {'role': 'assistant', 'content': 'Hello'}}]},
            )
            message = assistant._complete([{'role': 'user', 'content': 'Hello'}])
            self.assertEqual(message['content'], 'Hello')
            payload = post_request.call_args.kwargs['json']
            self.assertTrue(any(tool['function']['name'] == 'create_order' for tool in payload['tools']))

    def test_groq_stt_uses_server_side_key(self):
        with self.app.app_context(), patch('services.stt_service.requests.post') as post_request:
            post_request.return_value = SimpleNamespace(
                status_code=200,
                raise_for_status=lambda: None,
                json=lambda: {'text': 'hello from voice'},
            )
            stt = STTService(backend='groq_api')
            stt.groq_key = 'test-key'
            result = stt.transcribe(b'a' * 700, 'hi-IN', filename='recording.webm')
            self.assertEqual(result['text'], 'hello from voice')
            self.assertEqual(result['language'], 'hi')
            self.assertEqual(post_request.call_args.kwargs['data']['model'], 'whisper-large-v3-turbo')


if __name__ == '__main__':
    unittest.main()
