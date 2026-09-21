"""GST/tax rate configuration used when preparing customer orders."""

from decimal import Decimal

from extensions import db


class TaxConfig(db.Model):
    """A reusable, administrator-managed GST rate.

    An order deliberately stores a snapshot of the selected rate in
    ``Order.tax_percent``.  Changing a configuration therefore never changes
    tax on historic invoices.
    """

    __tablename__ = 'tax_configs'

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False, unique=True)
    rate = db.Column(db.Numeric(5, 2), nullable=False, default=Decimal('0.00'))
    is_active = db.Column(db.Boolean, nullable=False, default=True)
    is_default = db.Column(db.Boolean, nullable=False, default=False)
    created_at = db.Column(db.DateTime, nullable=False, server_default=db.func.now())
    updated_at = db.Column(
        db.DateTime, nullable=False, server_default=db.func.now(), onupdate=db.func.now()
    )

    __table_args__ = (
        db.CheckConstraint('rate >= 0 AND rate <= 100', name='ck_tax_configs_rate_range'),
    )

    @classmethod
    def active(cls):
        """Return active rates in the order most useful for billing forms."""
        return cls.query.filter_by(is_active=True).order_by(
            cls.is_default.desc(), cls.rate.asc(), cls.name.asc()
        )

    @property
    def display_name(self):
        return f'{self.name} ({self.rate:g}%)'

    def __repr__(self):
        return f'<TaxConfig {self.name}: {self.rate}%>'
