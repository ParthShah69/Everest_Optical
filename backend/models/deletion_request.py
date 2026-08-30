from extensions import db

class DeletionRequest(db.Model):
    __tablename__ = 'deletion_requests'

    id = db.Column(db.Integer, primary_key=True)
    entity_type = db.Column(db.String(50), nullable=False)  # 'customer', 'order', 'prescription'
    entity_id = db.Column(db.Integer, nullable=False)
    entity_identifier = db.Column(db.String(150), nullable=False)  # Name, Order No, etc.
    
    reason = db.Column(db.Text, nullable=False)
    status = db.Column(db.String(20), default='Pending')  # 'Pending', 'Approved', 'Rejected'
    admin_notes = db.Column(db.Text)
    
    requested_by_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    reviewed_by_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=True)
    
    created_at = db.Column(db.DateTime, server_default=db.func.now())
    reviewed_at = db.Column(db.DateTime)

    # Relationships
    requested_by = db.relationship('User', foreign_keys=[requested_by_id])
    reviewed_by = db.relationship('User', foreign_keys=[reviewed_by_id])

    def __repr__(self):
        return f'<DeletionRequest {self.entity_type}:{self.entity_id} status={self.status}>'
