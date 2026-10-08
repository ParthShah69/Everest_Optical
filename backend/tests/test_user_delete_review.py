"""A user-delete link must show a review page before any mutation."""

import os
import sys
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from app import create_app
from config import Config
from extensions import db
from models.user import User


class TestConfig(Config):
    TESTING = True
    SECRET_KEY = 'user-delete-review-test'
    SQLALCHEMY_DATABASE_URI = 'sqlite://'
    SQLALCHEMY_ENGINE_OPTIONS = {}


class UserDeleteReviewTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = create_app(TestConfig)

    def setUp(self):
        with self.app.app_context():
            db.drop_all()
            db.create_all()
            admin = User(username='review-admin', role='admin')
            staff = User(username='review-staff', role='staff')
            db.session.add_all([admin, staff])
            db.session.commit()
            self.admin_id = admin.id
            self.staff_id = staff.id

    def _admin_client(self):
        client = self.app.test_client()
        with client.session_transaction() as session:
            session['_user_id'] = str(self.admin_id)
            session['_fresh'] = True
        return client

    def test_get_shows_confirmation_without_deleting(self):
        client = self._admin_client()
        response = client.get(f'/users/delete/{self.staff_id}')
        self.assertEqual(response.status_code, 200)
        self.assertIn(b'Approve deletion', response.data)
        self.assertIn(b'review-staff', response.data)
        with self.app.app_context():
            self.assertIsNotNone(db.session.get(User, self.staff_id))

    def test_post_deletes_after_review(self):
        client = self._admin_client()
        denied = client.post(f'/users/delete/{self.staff_id}')
        self.assertEqual(denied.status_code, 302)
        with self.app.app_context():
            self.assertIsNotNone(db.session.get(User, self.staff_id))
        client.get(f'/users/delete/{self.staff_id}')
        with client.session_transaction() as browser_session:
            token = browser_session[f'delete_user_{self.staff_id}']
        response = client.post(f'/users/delete/{self.staff_id}', data={'delete_token': token})
        self.assertEqual(response.status_code, 302)
        with self.app.app_context():
            self.assertIsNone(db.session.get(User, self.staff_id))

    def test_missing_user_returns_to_user_list(self):
        response = self._admin_client().get('/users/delete/99999')
        self.assertEqual(response.status_code, 302)
        self.assertIn('/users', response.headers['Location'])


if __name__ == '__main__':
    unittest.main()
