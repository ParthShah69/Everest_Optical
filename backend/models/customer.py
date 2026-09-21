from extensions import db

class Customer(db.Model):
    __tablename__ = 'customers'

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    care_of = db.Column(db.String(100))
    phone = db.Column(db.String(20), nullable=False)
    email = db.Column(db.String(120))
    
    # Address fields
    address = db.Column(db.Text)
    city = db.Column(db.String(50))
    state = db.Column(db.String(50))
    pincode = db.Column(db.String(10))

    # Financial
    credit_limit = db.Column(db.Numeric(10, 2), default=0)
    loyalty_points = db.Column(db.Integer, default=0)

    created_at = db.Column(db.DateTime, server_default=db.func.now())
    updated_at = db.Column(db.DateTime, server_default=db.func.now(), onupdate=db.func.now())

    def __repr__(self):
        return f'<Customer {self.name}>'

    @property
    def full_address(self):
        parts = [p for p in [self.address, self.city, self.state, self.pincode] if p]
        return ', '.join(parts) if parts else ''
