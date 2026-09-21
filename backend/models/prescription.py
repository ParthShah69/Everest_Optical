from extensions import db

class Prescription(db.Model):
    __tablename__ = 'prescriptions'

    id = db.Column(db.Integer, primary_key=True)
    customer_id = db.Column(db.Integer, db.ForeignKey('customers.id'), nullable=False)
    
    # ── Distant Vision (DV) — Right Eye (RE / OD) ──
    re_sph = db.Column(db.Numeric(5, 2))
    re_cyl = db.Column(db.Numeric(5, 2))
    re_axis = db.Column(db.Integer)
    re_visual_acuity = db.Column(db.String(20))  # e.g., "6/6", "6/9"
    
    # ── Distant Vision (DV) — Left Eye (LE / OS) ──
    le_sph = db.Column(db.Numeric(5, 2))
    le_cyl = db.Column(db.Numeric(5, 2))
    le_axis = db.Column(db.Integer)
    le_visual_acuity = db.Column(db.String(20))

    # ── Near Vision (NV) — Right Eye ──
    re_nv_sph = db.Column(db.Numeric(5, 2))
    re_nv_cyl = db.Column(db.Numeric(5, 2))
    re_nv_axis = db.Column(db.Integer)

    # ── Near Vision (NV) — Left Eye ──
    le_nv_sph = db.Column(db.Numeric(5, 2))
    le_nv_cyl = db.Column(db.Numeric(5, 2))
    le_nv_axis = db.Column(db.Integer)

    # Addition (reading add / near vision)
    addition = db.Column(db.Numeric(5, 2))

    # ── Pupillary Distance (PD) — Critical for lens fitting ──
    pd_right = db.Column(db.Numeric(4, 1))
    pd_left = db.Column(db.Numeric(4, 1))
    pd_total = db.Column(db.Numeric(4, 1))

    # ── Referring Doctor ──
    referred_by = db.Column(db.String(100))

    # ── Scheduling ──
    next_visit_date = db.Column(db.Date)
    lens_expiry_date = db.Column(db.Date)

    # ── Lens Classification & Type ──
    lens_classification = db.Column(db.String(20))   # 'Ready Stock' or 'Rx'
    lens_type_tags = db.Column(db.String(255))        # Comma-separated: "AR,Tint,Multi"

    image_path = db.Column(db.String(255)) # Path to uploaded image
    
    notes = db.Column(db.Text)
    created_by = db.Column(db.Integer, db.ForeignKey('users.id'))
    created_at = db.Column(db.DateTime, server_default=db.func.now())

    # Relationships
    customer = db.relationship('Customer', backref=db.backref('prescriptions', lazy=True, order_by='Prescription.created_at.desc()'))
    creator = db.relationship('User')

    def __repr__(self):
        return f'<Prescription {self.id} for Customer {self.customer_id}>'
