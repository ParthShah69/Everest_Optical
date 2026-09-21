from extensions import db

class Inventory(db.Model):
    __tablename__ = 'inventory'

    id = db.Column(db.Integer, primary_key=True)
    model_name = db.Column(db.String(100), nullable=False)
    brand = db.Column(db.String(50))
    frame_type = db.Column(db.String(50))
    barcode = db.Column(db.String(50), unique=True, nullable=True)  # Barcode / Item Code
    quantity = db.Column(db.Integer, default=0)
    location = db.Column(db.String(100), nullable=False) # Rack/Drawer/Shelf
    shop_branch = db.Column(db.String(100))
    
    cost_price = db.Column(db.Numeric(10, 2), nullable=False)
    selling_price = db.Column(db.Numeric(10, 2), nullable=False)
    low_stock_threshold = db.Column(db.Integer, default=5)
    color_stock = db.Column(db.Text, nullable=True)  # e.g. "black-10, white-15, grey-5"
    image_path = db.Column(db.String(255), nullable=True)
    
    # Lens classification for optical items
    item_type = db.Column(db.String(30), default='Frame')  # Frame, Lens, Contact Lens, Accessory
    
    created_at = db.Column(db.DateTime, server_default=db.func.now())
    updated_at = db.Column(db.DateTime, server_default=db.func.now(), onupdate=db.func.now())

    def __repr__(self):
        return f'<Inventory {self.model_name}>'

    @property
    def is_low_stock(self):
        return self.quantity <= self.low_stock_threshold

    @property
    def display_name(self):
        return f"{self.brand or ''} {self.model_name}".strip()
