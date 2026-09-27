"""Unit tests for the new AI operational tools and rate limit / overspam cooldown."""

import json
import os
import sys
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from app import create_app
from config import Config
from extensions import db
from models.customer import Customer
from models.inventory import Inventory
from models.order import Order
from models.payment import Payment
from models.prescription import Prescription
from models.tax_config import TaxConfig
from models.user import User
from services.ai_service import AIAssistant, AIRateLimitError
from services.ai_tools import (
    create_order,
    record_payment,
    get_prescriptions,
    get_tax_rates,
    get_low_stock_inventory,
    get_order_document_links,
    navigate_to_page,
    TOOLS,
)


class TestConfig(Config):
    TESTING = True
    SECRET_KEY = 'test-secret'
    SQLALCHEMY_DATABASE_URI = 'sqlite://'
    SQLALCHEMY_ENGINE_OPTIONS = {}
    AI_PROVIDER = 'ollama'
    AI_STT_BACKEND = 'browser'


class NewToolsAndCooldownTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = create_app(TestConfig)

    def setUp(self):
        with self.app.app_context():
            db.drop_all()
            db.create_all()
            self.user = User(username='optician-tester', role='admin')
            self.customer = Customer(name='Amit Verma', phone='9812345678')
            self.item1 = Inventory(model_name='RayBan Wayfarer', location='Rack A', cost_price=1500, selling_price=3000, quantity=2)
            self.item2 = Inventory(model_name='Fastrack Sport', location='Rack B', cost_price=800, selling_price=1600, quantity=10)
            self.tax1 = TaxConfig(name='GST 12%', rate=12, is_active=True, is_default=True)
            self.tax2 = TaxConfig(name='GST 5%', rate=5, is_active=True, is_default=False)
            db.session.add_all([self.user, self.customer, self.item1, self.item2, self.tax1, self.tax2])
            db.session.commit()
            self.user_id = self.user.id
            self.customer_id = self.customer.id

    def test_record_payment_tool(self):
        with self.app.app_context():
            # Create an order first
            order_res = json.loads(create_order(
                customer_id=self.customer_id,
                items=[{'description': 'RayBan Wayfarer', 'quantity': 1, 'unit_price': 3000}],
                advance_amount=1000,
            ))
            self.assertTrue(order_res['success'])
            order_id = order_res['order']['id']
            order_no = order_res['order']['order_no']

            # Record payment of 1000 via UPI
            pay_res = json.loads(record_payment(
                order_id=order_id,
                amount=1000,
                payment_method='UPI',
                remark='GPay Ref 998877'
            ))
            self.assertTrue(pay_res['success'])
            self.assertTrue(pay_res['receipt_no'].startswith('RCP-'))
            self.assertEqual(pay_res['remaining_due'], 1000.0)

            # Check that payment cannot exceed remaining balance
            excess_res = json.loads(record_payment(
                order_no=order_no,
                amount=1500,
            ))
            self.assertFalse(excess_res['success'])
            self.assertIn('exceeds the remaining balance', excess_res['error'])

    def test_get_prescriptions_tool(self):
        with self.app.app_context():
            # Add a prescription for Amit
            rx = Prescription(
                customer_id=self.customer_id,
                re_sph=-1.50,
                re_cyl=-0.50,
                re_axis=90,
                le_sph=-2.00,
                le_cyl=0.0,
                le_axis=0,
                addition=1.25,
                pd_total=64,
                referred_by='Dr. Mehta'
            )
            db.session.add(rx)
            db.session.commit()

            # Retrieve by customer_id
            res = json.loads(get_prescriptions(customer_id=self.customer_id))
            self.assertTrue(res['success'])
            self.assertEqual(res['count'], 1)
            rx_data = res['prescriptions'][0]
            self.assertEqual(rx_data['right_eye']['sph'], -1.5)
            self.assertEqual(rx_data['right_eye']['axis'], 90)
            self.assertEqual(rx_data['pd'], 64.0)
            self.assertEqual(rx_data['doctor_name'], 'Dr. Mehta')

    def test_get_tax_rates_tool(self):
        with self.app.app_context():
            res = json.loads(get_tax_rates())
            self.assertTrue(res['success'])
            self.assertEqual(res['count'], 2)
            self.assertEqual(res['default_rate_percent'], 12.0)

    def test_get_low_stock_inventory_tool(self):
        with self.app.app_context():
            res = json.loads(get_low_stock_inventory(threshold=5))
            self.assertTrue(res['success'])
            self.assertEqual(res['count'], 1)
            self.assertEqual(res['low_stock_items'][0]['model_name'], 'RayBan Wayfarer')
            self.assertEqual(res['low_stock_items'][0]['quantity'], 2)

    def test_get_order_document_links_tool(self):
        with self.app.app_context():
            order_res = json.loads(create_order(
                customer_id=self.customer_id,
                items=[{'description': 'Test Glasses', 'quantity': 1, 'unit_price': 1000}],
            ))
            oid = order_res['order']['id']
            links = json.loads(get_order_document_links(order_id=oid))
            self.assertTrue(links['success'])
            self.assertIn(f'/orders/invoice/{oid}', links['printable_invoice'])
            self.assertIn(f'/orders/workshop/{oid}', links['workshop_slip'])

    def test_rate_limit_and_cooldown_error_structure(self):
        err = AIRateLimitError(retry_after=5, message="Groq rate limit")
        self.assertEqual(err.retry_after, 5.0)
        self.assertIn("Groq rate limit", str(err))


if __name__ == '__main__':
    unittest.main()
