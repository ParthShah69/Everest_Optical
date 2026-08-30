"""
Script to reset the database transactional data while preserving (or creating)
the Super Admin account (`username: admin`, `password: admin123`).
"""
from app import create_app
from extensions import db, bcrypt
from models.user import User
from models.customer import Customer
from models.order import Order, OrderItem
from models.prescription import Prescription
from models.inventory import Inventory
from models.deletion_request import DeletionRequest
from models.audit_log import AuditLog

def reset_database():
    app = create_app()
    with app.app_context():
        print("Resetting database tables while retaining Super Admin...")

        # Drop and recreate all tables to align columns (google_id, image_path, deletion_requests, etc.)
        db.drop_all()
        db.create_all()

        # Ensure Super Admin exists
        hashed_password = bcrypt.generate_password_hash('admin123').decode('utf-8')
        admin_user = User(username='admin', password_hash=hashed_password, role='admin')
        db.session.add(admin_user)
        db.session.commit()
        
        print("Super Admin created (admin / admin123).")
        print("Database reset completed successfully!")

if __name__ == '__main__':
    reset_database()
