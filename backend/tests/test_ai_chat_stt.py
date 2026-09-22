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
from models.user import User
from services.ai_service import AIAssistant
from services.ai_tools import create_order, update_order_status
from services.stt_service import STTService


class TestConfig(Config):
    TESTING = True
    SECRET_KEY = 'test-secret'
    SQLALCHEMY_DATABASE_URI = 'sqlite://'
    SQLALCHEMY_ENGINE_OPTIONS = {}
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

    def test_llm_availability_requires_configured_model_when_list_is_available(self):
        with self.app.app_context():
            assistant = AIAssistant()
            assistant.model = 'qwen3:4b'
            assistant._client = SimpleNamespace(list=lambda: SimpleNamespace(models=[SimpleNamespace(model='other:latest')]))
            self.assertFalse(assistant.is_available())
            assistant._client = SimpleNamespace(list=lambda: SimpleNamespace(models=[SimpleNamespace(model='qwen3:4b')]))
            self.assertTrue(assistant.is_available())


if __name__ == '__main__':
    unittest.main()
