"""Reset database tables only after an authenticated admin confirms the action.

This function runs in the caller's Flask application context. It preserves the
approving admin's credentials; it never creates a known default password.
"""

from extensions import db
from models.user import User


def reset_database(admin_snapshot):
    if not isinstance(admin_snapshot, dict) or admin_snapshot.get('role') != 'admin':
        raise ValueError('An authenticated admin snapshot is required.')
    if not admin_snapshot.get('username') or not (admin_snapshot.get('password_hash') or admin_snapshot.get('google_id')):
        raise ValueError('The admin account must have an existing sign-in method.')
    db.session.remove()
    db.drop_all()
    db.create_all()
    admin = User(**admin_snapshot)
    db.session.add(admin)
    db.session.commit()


if __name__ == '__main__':
    raise SystemExit('Use the confirmed admin action in Manage Users to reset the database.')
