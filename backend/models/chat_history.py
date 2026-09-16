from extensions import db
from datetime import datetime


class ChatMessage(db.Model):
    """Persists every chat exchange between a user and the AI assistant."""
    __tablename__ = 'chat_messages'

    id = db.Column(db.Integer, primary_key=True)

    # Session identifier (UUID generated client-side per page-load, or per login)
    session_id = db.Column(db.String(80), nullable=False, index=True)

    # The authenticated user who sent/received this message
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)

    # 'user' | 'assistant' | 'tool'
    role = db.Column(db.String(20), nullable=False)

    # Displayed / stored content
    content = db.Column(db.Text, nullable=False)

    # Language as detected: 'en', 'hi', 'gu', 'romanized_hi', 'romanized_gu'
    original_language = db.Column(db.String(20), nullable=True)

    # Original text before any transliteration / normalisation
    original_text = db.Column(db.Text, nullable=True)

    # When role='tool': which tool was called
    tool_name = db.Column(db.String(80), nullable=True)

    # JSON-serialised arguments passed to the tool
    tool_args = db.Column(db.Text, nullable=True)

    # JSON-serialised result returned by the tool
    tool_result = db.Column(db.Text, nullable=True)

    # High-level action: 'navigate' | 'create' | 'update' | 'query' | 'delete'
    action_type = db.Column(db.String(30), nullable=True)

    created_at = db.Column(db.DateTime, server_default=db.func.now(), nullable=False)

    # Relationships
    user = db.relationship('User', backref=db.backref('chat_messages', lazy=True))

    def __repr__(self):
        return f'<ChatMessage {self.id} role={self.role} session={self.session_id[:8]}>'
