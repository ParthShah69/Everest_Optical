"""Focused checks for AI tool target selection and transactional side effects."""

import json
import os
import sys
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from flask_login import login_user

from app import create_app
from config import Config
from extensions import db
from models.customer import Customer
from models.inventory import Inventory
from models.order import Order, OrderItem
from models.payment import Payment
from models.prescription import Prescription
from models.user import User
from services.ai_tools import (
    add_inventory_item, create_order, create_user, delete_customer,
    get_dashboard_stats, get_order_details, get_order_document_links,
    record_payment, search_orders, update_inventory_stock,
)


class TestConfig(Config):
    TESTING = True
    SECRET_KEY = 'tool-integrity-test'
    SQLALCHEMY_DATABASE_URI = 'sqlite://'
    SQLALCHEMY_ENGINE_OPTIONS = {}
    AI_PROVIDER = 'ollama'
    AI_STT_BACKEND = 'browser'


class ToolIntegrityTests(unittest.TestCase):
    def setUp(self):
        self.app = create_app(TestConfig)
        with self.app.app_context():
            db.create_all()
            admin = User(username='tool-admin', role='admin')
            staff = User(username='tool-staff', role='staff')
            customer = Customer(name='Primary Customer', phone='9876543210')
            other = Customer(name='Other Customer', phone='9876543211')
            db.session.add_all([admin, staff, customer, other])
            db.session.commit()
            self.admin_id, self.staff_id = admin.id, staff.id
            self.customer_id, self.other_id = customer.id, other.id

    def tearDown(self):
        with self.app.app_context():
            db.session.remove()
            db.drop_all()

    def call_as(self, user_id, tool, **kwargs):
        with self.app.test_request_context():
            if user_id is not None:
                login_user(db.session.get(User, user_id))
            return json.loads(tool(**kwargs))

    def test_user_creation_requires_authenticated_admin(self):
        with self.app.app_context():
            args = {'username': 'unexpected-admin', 'password': 'password123', 'role': 'admin'}
            self.assertFalse(self.call_as(None, create_user, **args)['success'])
            self.assertFalse(self.call_as(self.staff_id, create_user, **args)['success'])
            self.assertIsNone(User.query.filter_by(username='unexpected-admin').first())
            self.assertTrue(self.call_as(self.admin_id, create_user, **args)['success'])

    def test_payment_requires_matching_exact_order_and_valid_method(self):
        with self.app.app_context():
            first = Order(order_no='ORD-ONE', customer_id=self.customer_id, total_amount=100)
            second = Order(order_no='ORD-TWO', customer_id=self.customer_id, total_amount=100)
            db.session.add_all([first, second])
            db.session.commit()
            first_id = first.id
            self.assertFalse(self.call_as(self.admin_id, record_payment, order_id=first_id,
                                          order_no='ORD-TWO', amount=20)['success'])
            self.assertFalse(self.call_as(self.admin_id, record_payment, order_id=first_id,
                                          amount=20, payment_method='Unknown')['success'])
            self.assertFalse(self.call_as(self.admin_id, record_payment, order_id=first_id,
                                          amount='NaN')['success'])
            self.assertEqual(Payment.query.count(), 0)
            self.assertTrue(self.call_as(self.admin_id, record_payment, order_id=first_id,
                                         order_no='ORD-ONE', amount=20, payment_method='upi')['success'])
            self.assertEqual(Payment.query.one().payment_method, 'UPI')

    def test_order_reads_reject_conflicting_id_and_number(self):
        with self.app.app_context():
            first = Order(order_no='ORD-READ-ONE', customer_id=self.customer_id, total_amount=100)
            second = Order(order_no='ORD-READ-TWO', customer_id=self.customer_id, total_amount=100)
            db.session.add_all([first, second])
            db.session.commit()
            for tool in (get_order_details, get_order_document_links):
                result = self.call_as(self.admin_id, tool, order_id=first.id,
                                      order_no='ORD-READ-TWO')
                self.assertFalse(result['success'])

    def test_due_search_does_not_drop_older_order_after_fifty_paid_bills(self):
        with self.app.app_context():
            due = Order(order_no='ORD-OLDER-DUE', customer_id=self.customer_id, total_amount=100)
            db.session.add(due)
            db.session.commit()
            for number in range(55):
                order = Order(order_no=f'ORD-PAID-{number}', customer_id=self.customer_id,
                              total_amount=100, advance_amount=100)
                db.session.add(order)
            db.session.commit()
            result = self.call_as(self.admin_id, search_orders, due_only=True)
            self.assertEqual([order['order_no'] for order in result['orders']], ['ORD-OLDER-DUE'])

    def test_order_rejects_invalid_finances_and_foreign_prescription(self):
        with self.app.app_context():
            prescription = Prescription(customer_id=self.other_id)
            db.session.add(prescription)
            db.session.commit()
            base = {'customer_id': self.customer_id,
                    'items': [{'description': 'Frame', 'quantity': 1, 'unit_price': 100}]}
            for extra in ({'discount': -1}, {'discount': 101}, {'advance_amount': -1},
                          {'delivery_date': 'bad date'}, {'prescription_id': prescription.id},
                          {'discount': 'NaN'}):
                self.assertFalse(self.call_as(self.admin_id, create_order, **{**base, **extra})['success'])
            self.assertEqual(Order.query.count(), 0)

    def test_customer_deletion_restores_delivered_stock_and_removes_receipts(self):
        with self.app.app_context():
            item = Inventory(model_name='Frame', location='Rack A', cost_price=10,
                             selling_price=25, quantity=7)
            order = Order(order_no='ORD-DELIVERED', customer_id=self.customer_id,
                          total_amount=75, status='Delivered')
            db.session.add_all([item, order])
            db.session.flush()
            db.session.add_all([
                OrderItem(order_id=order.id, inventory_id=item.id, quantity=3, unit_price=25),
                Payment(order_id=order.id, amount=20, payment_method='Cash'),
            ])
            db.session.commit()
            item_id = item.id
            self.assertTrue(self.call_as(self.admin_id, delete_customer,
                                         customer_id=self.customer_id)['success'])
            self.assertEqual(db.session.get(Inventory, item_id).quantity, 10)
            self.assertEqual(Order.query.count(), 0)
            self.assertEqual(Payment.query.count(), 0)
            self.assertEqual(OrderItem.query.count(), 0)

    def test_stock_mutations_reject_invalid_quantities(self):
        with self.app.app_context():
            args = {'model_name': 'Frame', 'location': 'Rack A', 'cost_price': 10,
                    'selling_price': 20}
            self.assertFalse(self.call_as(self.admin_id, add_inventory_item,
                                          **args, quantity=-2)['success'])
            self.assertFalse(self.call_as(self.admin_id, add_inventory_item,
                                          **args, low_stock_threshold=-1)['success'])
            self.assertEqual(Inventory.query.count(), 0)
            self.assertTrue(self.call_as(self.admin_id, add_inventory_item,
                                         **args, quantity=3)['success'])
            item = Inventory.query.one()
            self.assertFalse(self.call_as(self.admin_id, update_inventory_stock,
                                          inventory_id=item.id, quantity_change=0)['success'])
            self.assertEqual(item.quantity, 3)

    def test_dashboard_due_includes_payments_after_advance(self):
        with self.app.app_context():
            order = Order(order_no='ORD-DUE', customer_id=self.customer_id,
                          total_amount=100, advance_amount=20)
            db.session.add(order)
            db.session.flush()
            db.session.add(Payment(order_id=order.id, amount=50, payment_method='Cash'))
            db.session.commit()
            stats = self.call_as(self.admin_id, get_dashboard_stats)['stats']
            self.assertEqual(stats['total_advance_collected'], 20)
            self.assertEqual(stats['total_collected'], 50)
            self.assertEqual(stats['outstanding_balance'], 50)


if __name__ == '__main__':
    unittest.main()
