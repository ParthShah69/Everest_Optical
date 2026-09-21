"""Bring databases created before the Phase 1 migrations to the current schema.

This revision is intentionally defensive.  Some deployed databases were stamped at
``2f68fd399e07`` before the model changes were migrated, so the normal autogenerate
revision only accounted for the handful of changes it saw at generation time.  The
column checks below make this revision safe for both those databases and databases
which have already received part of the Phase 1 work.
"""

from alembic import op
import sqlalchemy as sa


revision = "5c7e2b1f4a90"
down_revision = "31b1e938b19f"
branch_labels = None
depends_on = None


def _inspector():
    return sa.inspect(op.get_bind())


def _add_columns(table, columns):
    """Add only columns absent from *table* (works on SQLite and PostgreSQL)."""
    inspector = _inspector()
    if table not in inspector.get_table_names():
        return
    existing = {column["name"] for column in inspector.get_columns(table)}
    for column in columns:
        if column.name not in existing:
            op.add_column(table, column)
            existing.add(column.name)


def upgrade():
    _add_columns(
        "customers",
        [
            sa.Column("email", sa.String(length=120), nullable=True),
            sa.Column("address", sa.Text(), nullable=True),
            sa.Column("city", sa.String(length=50), nullable=True),
            sa.Column("state", sa.String(length=50), nullable=True),
            sa.Column("pincode", sa.String(length=10), nullable=True),
            sa.Column("credit_limit", sa.Numeric(precision=10, scale=2), nullable=True, server_default="0"),
            sa.Column("loyalty_points", sa.Integer(), nullable=True, server_default="0"),
        ],
    )

    _add_columns(
        "prescriptions",
        [
            sa.Column("re_visual_acuity", sa.String(length=20), nullable=True),
            sa.Column("le_visual_acuity", sa.String(length=20), nullable=True),
            sa.Column("re_nv_sph", sa.Numeric(precision=5, scale=2), nullable=True),
            sa.Column("re_nv_cyl", sa.Numeric(precision=5, scale=2), nullable=True),
            sa.Column("re_nv_axis", sa.Integer(), nullable=True),
            sa.Column("le_nv_sph", sa.Numeric(precision=5, scale=2), nullable=True),
            sa.Column("le_nv_cyl", sa.Numeric(precision=5, scale=2), nullable=True),
            sa.Column("le_nv_axis", sa.Integer(), nullable=True),
            sa.Column("pd_right", sa.Numeric(precision=4, scale=1), nullable=True),
            sa.Column("pd_left", sa.Numeric(precision=4, scale=1), nullable=True),
            sa.Column("pd_total", sa.Numeric(precision=4, scale=1), nullable=True),
            sa.Column("referred_by", sa.String(length=100), nullable=True),
            sa.Column("next_visit_date", sa.Date(), nullable=True),
            sa.Column("lens_expiry_date", sa.Date(), nullable=True),
            sa.Column("lens_classification", sa.String(length=20), nullable=True),
            sa.Column("lens_type_tags", sa.String(length=255), nullable=True),
        ],
    )

    _add_columns(
        "orders",
        [
            sa.Column("delivery_time", sa.Time(), nullable=True),
            sa.Column("delivery_in_days", sa.Integer(), nullable=True),
            sa.Column("delivery_in_hours", sa.Integer(), nullable=True),
            sa.Column("tax_mode", sa.String(length=20), nullable=True, server_default="not_apply"),
            sa.Column("tax_percent", sa.Numeric(precision=5, scale=2), nullable=True, server_default="0"),
            sa.Column("tax_amount", sa.Numeric(precision=10, scale=2), nullable=True, server_default="0"),
        ],
    )

    _add_columns(
        "order_items",
        [
            sa.Column("description", sa.String(length=255), nullable=True),
            sa.Column("discount_percent", sa.Numeric(precision=5, scale=2), nullable=True, server_default="0"),
            sa.Column("discount_amount", sa.Numeric(precision=10, scale=2), nullable=True, server_default="0"),
        ],
    )

    _add_columns(
        "inventory",
        [
            sa.Column("barcode", sa.String(length=50), nullable=True),
            sa.Column("image_path", sa.String(length=255), nullable=True),
            sa.Column("item_type", sa.String(length=30), nullable=True, server_default="Frame"),
        ],
    )

    inspector = _inspector()
    tables = set(inspector.get_table_names())
    if "payments" not in tables:
        op.create_table(
            "payments",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("order_id", sa.Integer(), nullable=False),
            sa.Column("receipt_no", sa.String(length=50), nullable=True),
            sa.Column("amount", sa.Numeric(precision=10, scale=2), nullable=False),
            sa.Column("payment_method", sa.String(length=30), nullable=False),
            sa.Column("remark", sa.String(length=255), nullable=True),
            sa.Column("payment_type", sa.String(length=20), nullable=True, server_default="advance"),
            sa.Column("created_by", sa.Integer(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=True, server_default=sa.text("CURRENT_TIMESTAMP")),
            sa.ForeignKeyConstraint(["order_id"], ["orders.id"]),
            sa.ForeignKeyConstraint(["created_by"], ["users.id"]),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("receipt_no"),
        )

    if "sequences" not in tables:
        op.create_table(
            "sequences",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("prefix", sa.String(length=20), nullable=False),
            sa.Column("current_value", sa.Integer(), nullable=False, server_default="0"),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("prefix"),
        )


def downgrade():
    inspector = _inspector()
    tables = set(inspector.get_table_names())
    if "sequences" in tables:
        op.drop_table("sequences")
    if "payments" in tables:
        op.drop_table("payments")

    # These are the columns introduced by this compatibility revision.  Batch
    # operations are required for SQLite and are also valid on PostgreSQL.
    for table, names in {
        "inventory": ["barcode", "image_path", "item_type"],
        "order_items": ["description", "discount_percent", "discount_amount"],
        "orders": ["delivery_time", "delivery_in_days", "delivery_in_hours", "tax_mode", "tax_percent", "tax_amount"],
        "prescriptions": [
            "re_visual_acuity", "le_visual_acuity", "re_nv_sph", "re_nv_cyl", "re_nv_axis",
            "le_nv_sph", "le_nv_cyl", "le_nv_axis", "pd_right", "pd_left", "pd_total",
            "referred_by", "next_visit_date", "lens_expiry_date", "lens_classification", "lens_type_tags",
        ],
        "customers": ["email", "address", "city", "state", "pincode", "credit_limit", "loyalty_points"],
    }.items():
        if table not in tables:
            continue
        existing = {column["name"] for column in _inspector().get_columns(table)}
        with op.batch_alter_table(table) as batch_op:
            for name in names:
                if name in existing:
                    batch_op.drop_column(name)
