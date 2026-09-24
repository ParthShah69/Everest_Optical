"""
ai_routes.py
============
Flask Blueprint exposing AI assistant endpoints:

  POST  /api/ai/chat        – Text chat with the LLM assistant
  POST  /api/ai/transcribe  – Audio blob → transcribed text
  GET   /api/ai/health      – Ollama + STT availability check
  GET   /api/ai/config      – Fetch current AI configuration
  POST  /api/ai/config      – Update AI configuration (admin only)
  GET   /api/ai/history     – Retrieve chat history for session
  GET   /api/ai/sessions    – List this user's saved conversations

SocketIO events (handled in this file via socketio.on decorators):
  connect        – Authenticate + register session
  disconnect     – Cleanup
  chat_message   – Bidirectional chat; emits 'chat_response'
"""

import json
import logging
import re
import uuid

from flask import Blueprint, request, jsonify, current_app
from flask_login import current_user, login_required

try:
    from flask_socketio import emit, join_room, leave_room
except ImportError:
    def emit(*args, **kwargs): pass
    def join_room(*args, **kwargs): pass
    def leave_room(*args, **kwargs): pass

from extensions import socketio, db
from services.ai_service import get_assistant
from services.stt_service import STTService
from models.chat_history import ChatMessage


log = logging.getLogger(__name__)

ai_bp = Blueprint('ai', __name__, url_prefix='/api/ai')

_SESSION_ID_RE = re.compile(r'^[A-Za-z0-9_-]{1,80}$')
_SUPPORTED_STT_CONTENT_TYPES = {
    'audio/webm', 'audio/wav', 'audio/x-wav', 'audio/mpeg', 'audio/mp4',
    'audio/ogg', 'audio/opus', 'application/ogg',
}


def _session_id(value):
    """Return a safe client session id, or generate one when it is omitted."""
    if not value:
        return str(uuid.uuid4())
    value = str(value).strip()
    return value if _SESSION_ID_RE.fullmatch(value) else None


def _room_name(user_id, session_id):
    """Keep Socket.IO rooms private even when a session id is guessed."""
    return f'ai:{user_id}:{session_id}'

# ---------------------------------------------------------------------------
# REST Endpoints
# ---------------------------------------------------------------------------

@ai_bp.route('/health', methods=['GET'])
@login_required
def health():
    """Check if Ollama + STT backends are available."""
    assistant = get_assistant()
    stt = STTService()
    return jsonify({
        "llm": {
            "available": assistant.is_available(),
            "model": assistant.model,
            "provider": assistant.provider,
        },
        "stt": {
            "available": stt.is_available(),
            "backend": stt.backend,
            "model_size": stt.model_size,
        },
        "realtime": {"enabled": not current_app.config.get('AI_DISABLE_SOCKETIO', False)},
    })


@ai_bp.route('/chat', methods=['POST'])
@login_required
def chat():
    """
    Text-based chat with the AI assistant.
    Body: { "message": str, "session_id": str (optional) }
    Returns: { text, action, navigate_to, language, tool_calls, error }
    """
    data = request.get_json(silent=True) or {}
    if not isinstance(data, dict):
        return jsonify({"error": "Request body must be a JSON object."}), 400
    user_message = str(data.get("message") or "").strip()
    session_id = _session_id(data.get("session_id"))

    if not user_message:
        return jsonify({"error": "Message cannot be empty."}), 400
    if len(user_message) > current_app.config.get('AI_MAX_MESSAGE_CHARS', 4000):
        return jsonify({"error": "Message is too long. Please keep it under 4,000 characters."}), 400
    if not session_id:
        return jsonify({"error": "Invalid chat session."}), 400

    assistant = get_assistant()

    if not assistant.is_available():
        model = current_app.config.get('AI_LLM_MODEL', 'qwen3:8b')
        provider = current_app.config.get('AI_PROVIDER', 'ollama')
        offline_text = (
                f"⚠️ The AI assistant is offline. "
                f"The configured {provider} model '{model}' is unavailable."
        )
        # Keep the operator's attempted conversation visible and resumable even
        # during a temporary provider outage.  A later message in this same
        # session will still receive the bounded previous context.
        assistant._save_message(session_id, current_user.id, 'user', user_message, original_text=user_message)
        assistant._save_message(session_id, current_user.id, 'assistant', offline_text)
        return jsonify({
            "text": offline_text,
            "action": None,
            "navigate_to": None,
            "language": "en",
            "tool_calls": [],
            "error": "provider_unavailable",
        }), 200  # 200 so the chat widget renders the message

    try:
        result = assistant.chat(user_message, session_id, current_user.id)
        return jsonify(result)
    except Exception as exc:
        log.exception("[AI Route] /chat error")
        return jsonify({"error": str(exc)}), 500


@ai_bp.route('/transcribe', methods=['POST'])
@login_required
def transcribe():
    """
    Transcribe uploaded audio to text.
    Accepts multipart/form-data with field 'audio' (webm/wav/mp3).
    Optional field 'language' hint: 'en' | 'hi' | 'gu'
    Returns: { text, language, confidence, error }
    """
    if 'audio' not in request.files:
        return jsonify({"error": "No audio file uploaded. Use field name 'audio'."}), 400

    audio_file = request.files['audio']
    if not audio_file.filename:
        return jsonify({"error": "No audio file selected."}), 400
    if audio_file.mimetype and audio_file.mimetype not in _SUPPORTED_STT_CONTENT_TYPES:
        return jsonify({"error": "Unsupported audio format. Please use WebM, WAV, MP3, M4A, or OGG."}), 415

    max_bytes = current_app.config.get('AI_STT_MAX_UPLOAD_BYTES', 15 * 1024 * 1024)
    if request.content_length and request.content_length > max_bytes:
        return jsonify({"error": "Audio is too large. Please record a clip under 15 MB."}), 413

    language_hint = request.form.get('language') or None
    audio_bytes = audio_file.read(max_bytes + 1)
    if len(audio_bytes) > max_bytes:
        return jsonify({"error": "Audio is too large. Please record a clip under 15 MB."}), 413

    if len(audio_bytes) < 512:
        return jsonify({"text": "", "language": language_hint or "en",
                        "confidence": 0, "error": "Audio too short or empty."}), 200

    stt    = STTService()
    result = stt.transcribe(audio_bytes, language_hint, filename=audio_file.filename)
    return jsonify(result)


@ai_bp.route('/history', methods=['GET'])
@login_required
def history():
    """
    Retrieve chat history for a given session_id.
    Query param: ?session_id=<id>&limit=<n>
    """
    session_id = request.args.get('session_id', '')
    try:
        limit = min(max(int(request.args.get('limit', 50)), 1), 200)
    except (TypeError, ValueError):
        return jsonify({"error": "limit must be a number."}), 400

    if not session_id:
        return jsonify({"messages": []})
    if not _session_id(session_id):
        return jsonify({"error": "Invalid chat session."}), 400

    rows = (
        ChatMessage.query
        .filter_by(session_id=session_id, user_id=current_user.id)
        .filter(ChatMessage.role.in_(["user", "assistant"]))
        .order_by(ChatMessage.created_at.desc(), ChatMessage.id.desc())
        .limit(limit)
        .all()
    )
    rows.reverse()
    messages = [
        {
            "id": r.id,
            "role": r.role,
            "content": r.content,
            "language": r.original_language,
            "action_type": r.action_type,
            "timestamp": r.created_at.isoformat(),
        }
        for r in rows
    ]
    return jsonify({"messages": messages, "session_id": session_id})


def _conversation_title(content):
    """Create a compact plain-text label without trusting message markup."""
    text = ' '.join(str(content or '').split())
    return (text[:77] + '...') if len(text) > 80 else (text or 'New conversation')


@ai_bp.route('/sessions', methods=['GET'])
@login_required
def sessions():
    """List saved conversations belonging only to the authenticated user.

    ChatMessage is the source of truth: a session is represented by its id and
    needs no separate mutable row.  Only a short title and metadata are sent
    here; opening a conversation uses the already access-controlled history
    endpoint.
    """
    try:
        limit = min(max(int(request.args.get('limit', 30)), 1), 100)
    except (TypeError, ValueError):
        return jsonify({"error": "limit must be a number."}), 400

    try:
        stats = (
            db.session.query(
                ChatMessage.session_id.label('session_id'),
                db.func.max(ChatMessage.created_at).label('updated_at'),
                db.func.count(ChatMessage.id).label('message_count'),
            )
            .filter_by(user_id=current_user.id)
            .filter(ChatMessage.role.in_(['user', 'assistant']))
            .group_by(ChatMessage.session_id)
            .order_by(db.func.max(ChatMessage.created_at).desc())
            .limit(limit)
            .all()
        )
        session_ids = [row.session_id for row in stats]
        first_user_messages = (
            ChatMessage.query
            .filter(ChatMessage.user_id == current_user.id)
            .filter(ChatMessage.session_id.in_(session_ids))
            .filter(ChatMessage.role == 'user')
            .order_by(ChatMessage.created_at.asc(), ChatMessage.id.asc())
            .all()
        ) if session_ids else []
        titles = {}
        for row in first_user_messages:
            titles.setdefault(row.session_id, _conversation_title(row.content))

        return jsonify({
            'sessions': [
                {
                    'session_id': row.session_id,
                    'title': titles.get(row.session_id, 'Conversation'),
                    'message_count': row.message_count,
                    'updated_at': row.updated_at.isoformat() if row.updated_at else None,
                }
                for row in stats
            ]
        })
    except Exception as exc:
        log.warning('[AI] Could not list chat sessions: %s', exc)
        db.session.rollback()
        return jsonify({'sessions': []})


@ai_bp.route('/config', methods=['GET'])
@login_required
def get_config():
    """Return current AI config (sanitized — no API keys)."""
    cfg = current_app.config
    return jsonify({
        "provider":       cfg.get('AI_PROVIDER', 'ollama'),
        "llm_model":      cfg.get('AI_LLM_MODEL', 'qwen3:8b'),
        "ollama_host":    cfg.get('AI_OLLAMA_HOST', 'http://localhost:11434'),
        "stt_backend":    cfg.get('AI_STT_BACKEND', 'whisper_local'),
        "stt_model_size": cfg.get('AI_STT_MODEL_SIZE', 'small'),
        "default_language": cfg.get('AI_DEFAULT_LANGUAGE', 'auto'),
        "sarvam_key_set": bool(cfg.get('AI_SARVAM_API_KEY')),
    })


@ai_bp.route('/config', methods=['POST'])
@login_required
def update_config():
    """Update runtime AI config (admin only)."""
    if not current_user.is_admin:
        return jsonify({"error": "Admin privileges required."}), 403

    data = request.get_json(silent=True) or {}
    if not isinstance(data, dict):
        return jsonify({"error": "Request body must be a JSON object."}), 400
    allowed = {
        'AI_PROVIDER', 'AI_LLM_MODEL', 'AI_STT_BACKEND', 'AI_STT_MODEL_SIZE',
        'AI_DEFAULT_LANGUAGE', 'AI_MAX_TOOL_ITERATIONS', 'AI_SARVAM_API_KEY'
    }
    updated = {}
    valid_backends = {'whisper_local', 'sarvam_api', 'groq_api', 'browser'}
    valid_providers = {'ollama', 'groq'}
    valid_model_sizes = {'tiny', 'base', 'small', 'medium', 'large-v3'}
    valid_languages = {'auto', 'en', 'hi', 'gu'}
    for key, val in data.items():
        env_key = key.upper()
        if env_key not in allowed:
            continue
        if env_key == 'AI_PROVIDER' and val not in valid_providers:
            return jsonify({"error": "Invalid AI provider."}), 400
        if env_key == 'AI_STT_BACKEND' and val not in valid_backends:
            return jsonify({"error": "Invalid STT backend."}), 400
        if env_key == 'AI_STT_MODEL_SIZE' and val not in valid_model_sizes:
            return jsonify({"error": "Invalid Whisper model size."}), 400
        if env_key == 'AI_DEFAULT_LANGUAGE' and val not in valid_languages:
            return jsonify({"error": "Invalid default language."}), 400
        if env_key == 'AI_MAX_TOOL_ITERATIONS':
            try:
                val = int(val)
            except (TypeError, ValueError):
                return jsonify({"error": "AI_MAX_TOOL_ITERATIONS must be a number."}), 400
            if not 1 <= val <= 10:
                return jsonify({"error": "AI_MAX_TOOL_ITERATIONS must be between 1 and 10."}), 400
        elif not isinstance(val, str) or len(val) > 200:
            return jsonify({"error": f"Invalid value for {env_key}."}), 400
        current_app.config[env_key] = val
        updated[env_key] = val

    return jsonify({"updated": updated, "message": "Config updated for this session."})


# ---------------------------------------------------------------------------
# SocketIO events — real-time chat
# ---------------------------------------------------------------------------

@socketio.on('connect')
def on_connect():
    if not current_user.is_authenticated:
        return False  # Reject unauthenticated connections
    log.debug(f"[SocketIO] Client connected: user={current_user.id}")
    emit('connected', {"status": "ok", "user": current_user.username})


@socketio.on('disconnect')
def on_disconnect():
    log.debug(f"[SocketIO] Client disconnected")


@socketio.on('join_session')
def on_join_session(data):
    """Client sends session_id to join its personal room."""
    session_id = _session_id((data or {}).get('session_id'))
    if session_id and current_user.is_authenticated:
        join_room(_room_name(current_user.id, session_id))
        emit('joined', {"session_id": session_id})


@socketio.on('chat_message')
def on_chat_message(data):
    """
    Real-time chat via WebSocket.
    Receives: { message: str, session_id: str }
    Emits:    'chat_typing' (while processing), 'chat_response' (final answer)
    """
    if not current_user.is_authenticated:
        emit('chat_error', {"error": "Not authenticated."})
        return

    data = data if isinstance(data, dict) else {}
    user_message = str(data.get('message') or '').strip()
    session_id = _session_id(data.get('session_id'))

    if not user_message:
        emit('chat_error', {"error": "Empty message."})
        return
    if not session_id:
        emit('chat_error', {"error": "Invalid chat session."})
        return
    if len(user_message) > current_app.config.get('AI_MAX_MESSAGE_CHARS', 4000):
        emit('chat_error', {"error": "Message is too long. Please keep it under 4,000 characters."})
        return

    room = _room_name(current_user.id, session_id)

    # Notify client we are processing
    emit('chat_typing', {"status": "thinking"}, room=room)

    assistant = get_assistant()

    if not assistant.is_available():
        model = current_app.config.get('AI_LLM_MODEL', 'qwen3:8b')
        offline_text = f"⚠️ AI offline. The configured model '{model}' is unavailable."
        assistant._save_message(session_id, current_user.id, 'user', user_message, original_text=user_message)
        assistant._save_message(session_id, current_user.id, 'assistant', offline_text)
        emit('chat_response', {
            "text": offline_text,
            "action": None,
            "navigate_to": None,
            "tool_calls": [],
            "error": "provider_unavailable",
        }, room=room)
        return

    try:
        result = assistant.chat(user_message, session_id, current_user.id)
        emit('chat_response', result, room=room)
    except Exception as exc:
        log.exception("[SocketIO] chat_message error")
        emit('chat_error', {"error": "The assistant could not process that request. Please try again."}, room=room)
