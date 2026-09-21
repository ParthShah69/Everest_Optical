from extensions import db


class Sequence(db.Model):
    __tablename__ = 'sequences'

    id = db.Column(db.Integer, primary_key=True)
    prefix = db.Column(db.String(20), unique=True, nullable=False)  # 'ORD', 'INV', 'RCP'
    current_value = db.Column(db.Integer, default=0, nullable=False)

    def __repr__(self):
        return f'<Sequence {self.prefix}: {self.current_value}>'


def get_next_number(prefix):
    """Get the next sequential number for a given prefix.
    Returns formatted string like 'ORD-0001', 'RCP-0042', etc.
    """
    seq = Sequence.query.filter_by(prefix=prefix).first()
    if not seq:
        seq = Sequence(prefix=prefix, current_value=0)
        db.session.add(seq)
    seq.current_value += 1
    db.session.flush()
    return f"{prefix}-{seq.current_value:04d}"
