"""Add relative delivery hours to orders.

Revision ID: 4a1c8bd9e0f2
Revises: 2f68fd399e07
Create Date: 2026-09-22
"""
from alembic import op
import sqlalchemy as sa


revision = '4a1c8bd9e0f2'
down_revision = '2f68fd399e07'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('orders', schema=None) as batch_op:
        batch_op.add_column(sa.Column('delivery_in_hours', sa.Integer(), nullable=True))


def downgrade():
    with op.batch_alter_table('orders', schema=None) as batch_op:
        batch_op.drop_column('delivery_in_hours')
