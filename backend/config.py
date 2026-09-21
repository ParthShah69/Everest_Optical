import os
from dotenv import load_dotenv

basedir = os.path.abspath(os.path.dirname(__file__))
load_dotenv(os.path.join(basedir, '.env'))

class Config:
    SECRET_KEY = os.environ.get('SECRET_KEY') or 'dev-secret-key-change-this'
    SQLALCHEMY_DATABASE_URI = os.environ.get('DATABASE_URL') or 'sqlite:///site.db'
    
    # Prefix postgresql:// if needed for SQLAlchemy 2.0+
    if SQLALCHEMY_DATABASE_URI.startswith('postgres://'):
        SQLALCHEMY_DATABASE_URI = SQLALCHEMY_DATABASE_URI.replace('postgres://', 'postgresql://', 1)

    SQLALCHEMY_TRACK_MODIFICATIONS = False
    
    # SQLAlchemy engine options tuned for Neon/Render free tier (drops idle conns aggressively)
    SQLALCHEMY_ENGINE_OPTIONS = {
        'pool_pre_ping': True,      # validate connection before checkout
        'pool_recycle': 280,        # recycle just under Neon's 300s idle limit
        'pool_size': 5,
        'max_overflow': 5,
        'pool_timeout': 30,
        'connect_args': {
            'sslmode': 'require',
            'connect_timeout': 10,
            'keepalives': 1,
            'keepalives_idle': 30,
            'keepalives_interval': 10,
            'keepalives_count': 5,
        } if 'sqlite' not in SQLALCHEMY_DATABASE_URI else {}
    }

    # Resend Email API
    RESEND_API_KEY = os.environ.get('RESEND_API_KEY')
    MAIL_DEFAULT_SENDER = os.environ.get('MAIL_DEFAULT_SENDER', 'Optical ERP <onboarding@resend.dev>')

    # Google OAuth
    GOOGLE_CLIENT_ID = os.environ.get('GOOGLE_CLIENT_ID')
    GOOGLE_CLIENT_SECRET = os.environ.get('GOOGLE_CLIENT_SECRET')

    # ── AI Assistant Configuration ─────────────────────────────────────────────
    # Ollama local LLM settings
    AI_OLLAMA_HOST = os.environ.get('AI_OLLAMA_HOST', 'http://localhost:11434')
    AI_LLM_MODEL = os.environ.get('AI_LLM_MODEL', 'qwen3:4b')

    # Speech-to-Text: 'whisper_local' | 'sarvam_api' | 'browser'
    AI_STT_BACKEND = os.environ.get('AI_STT_BACKEND', 'whisper_local')
    # Whisper model size: tiny | base | small | medium | large-v3
    AI_STT_MODEL_SIZE = os.environ.get('AI_STT_MODEL_SIZE', 'small')

    # Optional Sarvam AI API key for Indian-language STT fallback
    AI_SARVAM_API_KEY = os.environ.get('AI_SARVAM_API_KEY', '')

    # Max ReAct tool-call iterations before giving up
    AI_MAX_TOOL_ITERATIONS = int(os.environ.get('AI_MAX_TOOL_ITERATIONS', 6))

    # Default assistant response language: 'en' | 'hi' | 'gu' | 'auto'
    AI_DEFAULT_LANGUAGE = os.environ.get('AI_DEFAULT_LANGUAGE', 'auto')

    # SocketIO async mode
    SOCKETIO_ASYNC_MODE = os.environ.get('SOCKETIO_ASYNC_MODE', 'threading')


