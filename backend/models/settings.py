from extensions import db

class DropdownOption(db.Model):
    __tablename__ = 'dropdown_options'

    id = db.Column(db.Integer, primary_key=True)
    category = db.Column(db.String(50), nullable=False) # e.g. 'delivery_mode', 'frame_type'
    value = db.Column(db.String(100), nullable=False)
    
    # Ensures no duplicate options per category
    __table_args__ = (
        db.UniqueConstraint('category', 'value', name='uq_dropdown_category_value'),
    )

    def __repr__(self):
        return f"<DropdownOption {self.category}: {self.value}>"
