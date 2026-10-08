"""Admin review must clean up orders and stock when deleting a customer."""

import os
import sys
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from app import create_app
from config import Config
from extensions import db
from models.customer import Customer
from models.deletion_request import DeletionRequest
from models.inventory import Inventory
from models.order import Order, OrderItem
from models.payment import Payment
from models.user import User


class TestConfig(Config):
    TESTING = True
    SECRET_KEY = 'deletion-request-test'
    SQLALCHEMY_DATABASE_URI = 'sqlite://'
    SQLALCHEMY_ENGINE_OPTIONS = {}
    AI_PROVIDER = 'ollama'
    AI_STT_BACKEND = 'browser'


class DeletionRequestIntegrityTests(unittest.TestCase):
    def setUp(self):
        self.app = create_app(TestConfig)
        with self.app.app_context():
            db.create_all()
            admin = User(username='admin-review', role='admin')
            customer = Customer(name='Delete Me', phone='9876543210')
            stock = Inventory(model_name='Frame', location='A1',
                              cost_price=10, selling_price=20, quantity=7)
            db.session.add_all([admin, customer, stock])
            db.session.flush()
            order = Order(order_no='ORD-DELETE-CUSTOMER', customer_id=customer.id,
                          total_amount=60, status='Delivered')
            db.session.add(order)
            db.session.flush()
            line = OrderItem(order_id=order.id, inventory_id=stock.id,
                             quantity=3, unit_price=20)
            payment = Payment(order_id=order.id, amount=60, payment_method='Cash')
            request = DeletionRequest(entity_type='customer', entity_id=customer.id,
                                      entity_identifier=customer.name, reason='duplicate',
                                      requested_by_id=admin.id)
            db.session.add_all([line, payment, request])
            db.session.commit()
            self.ids = (admin.id, customer.id, stock.id, order.id,
                        line.id, payment.id, request.id)
        self.client = self.app.test_client()
        with self.client.session_transaction() as sess:
            sess['_user_id'] = str(self.ids[0])
            sess['_fresh'] = True

    def tearDown(self):
        with self.app.app_context():
            db.session.remove()
            db.drop_all()

    def test_approval_removes_receipts_and_restores_delivered_stock(self):
        _, customer_id, stock_id, order_id, line_id, payment_id, request_id = self.ids
        response = self.client.post(f'/deletion-requests/{request_id}/approve')
        self.assertEqual(response.status_code, 302)
        with self.app.app_context():
            self.assertIsNone(db.session.get(Customer, customer_id))
            self.assertIsNone(db.session.get(Order, order_id))
            self.assertIsNone(db.session.get(OrderItem, line_id))
            self.assertIsNone(db.session.get(Payment, payment_id))
            self.assertEqual(db.session.get(Inventory, stock_id).quantity, 10)
            self.assertEqual(db.session.get(DeletionRequest, request_id).status, 'Approved')


if __name__ == '__main__':
    unittest.main()
