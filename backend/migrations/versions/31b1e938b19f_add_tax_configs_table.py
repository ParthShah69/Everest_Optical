"""Add reusable GST configuration table.

Revision ID: 31b1e938b19f
Revises: 4a1c8bd9e0f2
Create Date: 2026-09-22
"""

from alembic import op
import sqlalchemy as sa


revision = '31b1e938b19f'
down_revision = '4a1c8bd9e0f2'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'tax_configs',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('name', sa.String(length=100), nullable=False),
        sa.Column('rate', sa.Numeric(precision=5, scale=2), nullable=False, server_default='0.00'),
        sa.Column('is_active', sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column('is_default', sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column('created_at', sa.DateTime(), nullable=False, server_default=sa.text('CURRENT_TIMESTAMP')),
        sa.Column('updated_at', sa.DateTime(), nullable=False, server_default=sa.text('CURRENT_TIMESTAMP')),
        sa.CheckConstraint('rate >= 0 AND rate <= 100', name='ck_tax_configs_rate_range'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('name'),
    )


def downgrade():
    op.drop_table('tax_configs')
