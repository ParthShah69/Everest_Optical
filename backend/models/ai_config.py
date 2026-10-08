"""Persistent AI provider configuration and approval state."""

from datetime import datetime, timedelta, timezone
from extensions import db


class AIProviderConfig(db.Model):
    __tablename__ = 'ai_provider_configs'

    id = db.Column(db.Integer, primary_key=True)
    provider = db.Column(db.String(32), nullable=False, index=True)
    model = db.Column(db.String(120), nullable=False)
    encrypted_key = db.Column(db.Text, nullable=False)
    priority = db.Column(db.Integer, nullable=False, default=100)
    enabled = db.Column(db.Boolean, nullable=False, default=True)
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)


class AIPendingAction(db.Model):
    __tablename__ = 'ai_pending_actions'

    id = db.Column(db.String(36), primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False, index=True)
    session_id = db.Column(db.String(80), nullable=False, index=True)
    tool_name = db.Column(db.String(80), nullable=False)
    encrypted_args = db.Column(db.Text, nullable=False)
    summary = db.Column(db.Text, nullable=False)
    critical = db.Column(db.Boolean, nullable=False, default=False)
    status = db.Column(db.String(20), nullable=False, default='pending')
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    expires_at = db.Column(db.DateTime, nullable=False,
                           default=lambda: datetime.utcnow() + timedelta(minutes=15))

    def public(self):
        phrase = {
            'delete_all': 'DELETE ALL',
            'reset_database': 'DELETE ALL',
            'delete_customer': 'DELETE CUSTOMER',
            'delete_order': 'DELETE ORDER',
        }.get(self.tool_name, 'CONFIRM')
        return {
            'id': self.id, 'summary': self.summary, 'critical': self.critical,
            'tool_name': self.tool_name, 'confirmation_phrase': phrase if self.critical else None,
            'expires_at': self.expires_at.isoformat() + 'Z',
        }


class AIChatSummary(db.Model):
    __tablename__ = 'ai_chat_summaries'

    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), primary_key=True)
    session_id = db.Column(db.String(80), primary_key=True)
    content = db.Column(db.Text, nullable=False, default='')
    through_message_id = db.Column(db.Integer, nullable=False, default=0)
    updated_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
