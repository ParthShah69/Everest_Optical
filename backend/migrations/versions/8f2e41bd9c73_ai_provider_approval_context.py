"""Persistent AI provider settings, approval actions and chat summaries."""

from alembic import op
import sqlalchemy as sa

revision = '8f2e41bd9c73'
down_revision = '5c7e2b1f4a90'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table('ai_provider_configs',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('provider', sa.String(32), nullable=False),
        sa.Column('model', sa.String(120), nullable=False),
        sa.Column('encrypted_key', sa.Text(), nullable=False),
        sa.Column('priority', sa.Integer(), nullable=False),
        sa.Column('enabled', sa.Boolean(), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=False))
    op.create_index('ix_ai_provider_configs_provider', 'ai_provider_configs', ['provider'])
    op.create_table('ai_pending_actions',
        sa.Column('id', sa.String(36), primary_key=True),
        sa.Column('user_id', sa.Integer(), sa.ForeignKey('users.id'), nullable=False),
        sa.Column('session_id', sa.String(80), nullable=False),
        sa.Column('tool_name', sa.String(80), nullable=False),
        sa.Column('encrypted_args', sa.Text(), nullable=False),
        sa.Column('summary', sa.Text(), nullable=False),
        sa.Column('critical', sa.Boolean(), nullable=False),
        sa.Column('status', sa.String(20), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('expires_at', sa.DateTime(), nullable=False))
    op.create_index('ix_ai_pending_actions_user_id', 'ai_pending_actions', ['user_id'])
    op.create_index('ix_ai_pending_actions_session_id', 'ai_pending_actions', ['session_id'])
    op.create_table('ai_chat_summaries',
        sa.Column('user_id', sa.Integer(), sa.ForeignKey('users.id'), primary_key=True),
        sa.Column('session_id', sa.String(80), primary_key=True),
        sa.Column('content', sa.Text(), nullable=False),
        sa.Column('through_message_id', sa.Integer(), nullable=False),
        sa.Column('updated_at', sa.DateTime(), nullable=False))


def downgrade():
    op.drop_table('ai_chat_summaries')
    op.drop_index('ix_ai_pending_actions_session_id', table_name='ai_pending_actions')
    op.drop_index('ix_ai_pending_actions_user_id', table_name='ai_pending_actions')
    op.drop_table('ai_pending_actions')
    op.drop_index('ix_ai_provider_configs_provider', table_name='ai_provider_configs')
    op.drop_table('ai_provider_configs')
