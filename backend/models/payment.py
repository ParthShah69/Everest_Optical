from extensions import db

class Payment(db.Model):
    __tablename__ = 'payments'

    id = db.Column(db.Integer, primary_key=True)
    order_id = db.Column(db.Integer, db.ForeignKey('orders.id'), nullable=False)
    receipt_no = db.Column(db.String(50), unique=True, nullable=True)

    amount = db.Column(db.Numeric(10, 2), nullable=False)
    payment_method = db.Column(db.String(30), nullable=False)  # Cash, Card, UPI, Paytm, Bank
    remark = db.Column(db.String(255))
    payment_type = db.Column(db.String(20), default='advance')  # 'advance', 'final', 'partial'

    created_by = db.Column(db.Integer, db.ForeignKey('users.id'))
    created_at = db.Column(db.DateTime, server_default=db.func.now())

    # Relationships
    order = db.relationship('Order', backref=db.backref('payments', lazy=True, order_by='Payment.created_at.desc()'))
    creator = db.relationship('User')

    def __repr__(self):
        return f'<Payment {self.receipt_no} ₹{self.amount} via {self.payment_method}>'
