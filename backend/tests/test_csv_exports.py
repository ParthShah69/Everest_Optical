"""Focused coverage for authenticated, spreadsheet-safe CSV exports."""

import csv
import io
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
from models.user import User


class ExportTestConfig(Config):
    TESTING = True
    SECRET_KEY = 'export-test-secret'
    SQLALCHEMY_DATABASE_URI = 'sqlite://'
    SQLALCHEMY_ENGINE_OPTIONS = {}


class CsvExportTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = create_app(ExportTestConfig)

    def setUp(self):
        with self.app.app_context():
            db.drop_all()
            db.create_all()
            user = User(username='export-admin', role='admin')
            customer = Customer(name='=Formula Customer', phone='9999999999', city='Surat')
            item = Inventory(model_name='Export Frame', location='A1', cost_price=100, selling_price=200, quantity=2)
            db.session.add_all((user, customer, item))
            db.session.commit()
            order = Order(order_no='ORD-0001', customer_id=customer.id, total_amount=200, status='Pending')
            db.session.add(order)
            db.session.commit()
            self.user_id = user.id

    def _client(self):
        client = self.app.test_client()
        with client.session_transaction() as session:
            session['_user_id'] = str(self.user_id)
            session['_fresh'] = True
        return client

    def test_exports_require_login(self):
        self.assertEqual(self.app.test_client().get('/exports/customers.csv').status_code, 302)

    def test_customer_export_is_csv_and_formula_safe(self):
        response = self._client().get('/exports/customers.csv?search=Formula')
        self.assertEqual(response.status_code, 200)
        self.assertIn('text/csv', response.content_type)
        self.assertIn('attachment; filename="customers.csv"', response.headers['Content-Disposition'])
        rows = list(csv.reader(io.StringIO(response.get_data(as_text=True).lstrip('\ufeff'))))
        self.assertEqual(rows[0][:2], ['ID', 'Name'])
        self.assertEqual(rows[1][1], "'=Formula Customer")

    def test_filtered_order_and_inventory_exports(self):
        client = self._client()
        orders = list(csv.reader(io.StringIO(client.get('/exports/orders.csv?status=Pending').get_data(as_text=True).lstrip('\ufeff'))))
        inventory = list(csv.reader(io.StringIO(client.get('/exports/inventory.csv?search=Export').get_data(as_text=True).lstrip('\ufeff'))))
        self.assertEqual(orders[1][0], 'ORD-0001')
        self.assertEqual(inventory[1][2], 'Export Frame')


if __name__ == '__main__':
    unittest.main()
