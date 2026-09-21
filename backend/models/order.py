from extensions import db
from datetime import datetime

# Extended status lifecycle matching the video
ORDER_STATUSES = [
    ('Pending', 'warning'),
    ('Approved', 'info'),
    ('Sent For Workshop', 'primary'),
    ('Processing', 'secondary'),
    ('Ready', 'success'),
    ('Out for Shipment', 'primary'),
    ('Delivered', 'success'),
    ('Exchange-In', 'warning'),
    ('Exchange-Out', 'warning'),
    ('Cancelled', 'danger'),
]

STATUS_CHOICES = [s[0] for s in ORDER_STATUSES]
STATUS_COLORS = {s[0]: s[1] for s in ORDER_STATUSES}


class Order(db.Model):
    __tablename__ = 'orders'

    id = db.Column(db.Integer, primary_key=True)
    order_no = db.Column(db.String(50), unique=True, nullable=False)
    customer_id = db.Column(db.Integer, db.ForeignKey('customers.id'), nullable=False)
    prescription_id = db.Column(db.Integer, db.ForeignKey('prescriptions.id'))
    
    status = db.Column(db.String(20), default='Pending')
    delivery_mode = db.Column(db.String(50), default='Self') # Self, Courier, Home Delivery
    issue_date = db.Column(db.Date, nullable=False, default=datetime.utcnow)
    delivery_date = db.Column(db.Date)
    delivery_time = db.Column(db.Time)
    delivery_in_days = db.Column(db.Integer)  # "Delivery in X days" helper
    delivery_in_hours = db.Column(db.Integer)  # "Delivery in X hours" helper
    
    # Financial
    advance_amount = db.Column(db.Numeric(10, 2), default=0.00)
    discount = db.Column(db.Numeric(10, 2), default=0.00)
    total_amount = db.Column(db.Numeric(10, 2), nullable=False)
    
    # Tax / GST
    tax_mode = db.Column(db.String(20), default='not_apply')  # 'calculated', 'not_calculated', 'not_apply'
    tax_percent = db.Column(db.Numeric(5, 2), default=0.00)
    tax_amount = db.Column(db.Numeric(10, 2), default=0.00)
    
    created_by = db.Column(db.Integer, db.ForeignKey('users.id'))
    created_at = db.Column(db.DateTime, server_default=db.func.now())
    updated_at = db.Column(db.DateTime, server_default=db.func.now(), onupdate=db.func.now())

    # Relationships
    customer = db.relationship('Customer', backref=db.backref('orders', lazy=True, order_by='Order.created_at.desc()'))
    prescription = db.relationship('Prescription')
    creator = db.relationship('User')
    items = db.relationship('OrderItem', backref='order', cascade='all, delete-orphan')

    @property
    def balance_amount(self):
        return (self.total_amount or 0) - (self.advance_amount or 0)

    @property
    def total_paid(self):
        """Return the paid total, retaining advances entered before receipts existed."""
        receipt_total = sum(float(p.amount or 0) for p in (self.payments or []))
        # ``advance_amount`` was the original payment store.  Retain it as a
        # compatibility floor for historic orders that do not have Payment rows.
        return max(receipt_total, float(self.advance_amount or 0))

    @property
    def remaining_due(self):
        """Remaining amount after all payments."""
        return float(self.total_amount or 0) - self.total_paid

    @property
    def status_color(self):
        return STATUS_COLORS.get(self.status, 'secondary')


class OrderItem(db.Model):
    __tablename__ = 'order_items'

    id = db.Column(db.Integer, primary_key=True)
    order_id = db.Column(db.Integer, db.ForeignKey('orders.id'), nullable=False)
    inventory_id = db.Column(db.Integer, db.ForeignKey('inventory.id'), nullable=True)

    description = db.Column(db.String(255), nullable=True)   # Item name / description
    quantity = db.Column(db.Integer, nullable=False, default=1)
    unit_price = db.Column(db.Numeric(10, 2), nullable=False)
    
    # Per-item discount
    discount_percent = db.Column(db.Numeric(5, 2), default=0.00)
    discount_amount = db.Column(db.Numeric(10, 2), default=0.00)

    inventory = db.relationship('Inventory', lazy=True)

    @property
    def gross_total(self):
        """Total before item-level discount."""
        return (self.quantity or 0) * (self.unit_price or 0)

    @property
    def total_price(self):
        """Total after item-level discount."""
        gross = self.gross_total
        if self.discount_amount and float(self.discount_amount) > 0:
            return gross - float(self.discount_amount)
        if self.discount_percent and float(self.discount_percent) > 0:
            return gross - (gross * float(self.discount_percent) / 100)
        return gross

    @property
    def display_name(self):
        if self.description:
            return self.description
        if self.inventory:
            return f"{self.inventory.brand or ''} {self.inventory.model_name}".strip()
        return 'Item'
