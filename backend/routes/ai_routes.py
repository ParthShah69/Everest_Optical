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

SocketIO events (handled in this file via socketio.on decorators):
  connect        – Authenticate + register session
  disconnect     – Cleanup
  chat_message   – Bidirectional chat; emits 'chat_response'
"""

import json
import logging
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
            "host": assistant.ollama_host,
        },
        "stt": {
            "available": stt.is_available(),
            "backend": stt.backend,
            "model_size": stt.model_size,
        },
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
    user_message = (data.get("message") or "").strip()
    session_id   = (data.get("session_id") or str(uuid.uuid4()))

    if not user_message:
        return jsonify({"error": "Message cannot be empty."}), 400

    assistant = get_assistant()

    if not assistant.is_available():
        model = current_app.config.get('AI_LLM_MODEL', 'qwen3:8b')
        return jsonify({
            "text": (
                f"⚠️ The AI assistant is offline. "
                f"Please make sure Ollama is running and the model '{model}' is downloaded.\n"
                f"Run: `ollama pull {model}` in your terminal."
            ),
            "action": None,
            "navigate_to": None,
            "language": "en",
            "tool_calls": [],
            "error": "ollama_unavailable",
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

    audio_file    = request.files['audio']
    language_hint = request.form.get('language') or None
    audio_bytes   = audio_file.read()

    if len(audio_bytes) < 512:
        return jsonify({"text": "", "language": language_hint or "en",
                        "confidence": 0, "error": "Audio too short or empty."}), 200

    stt    = STTService()
    result = stt.transcribe(audio_bytes, language_hint)
    return jsonify(result)


@ai_bp.route('/history', methods=['GET'])
@login_required
def history():
    """
    Retrieve chat history for a given session_id.
    Query param: ?session_id=<id>&limit=<n>
    """
    session_id = request.args.get('session_id', '')
    limit      = min(int(request.args.get('limit', 50)), 200)

    if not session_id:
        return jsonify({"messages": []})

    rows = (
        ChatMessage.query
        .filter_by(session_id=session_id, user_id=current_user.id)
        .filter(ChatMessage.role.in_(["user", "assistant"]))
        .order_by(ChatMessage.created_at.asc())
        .limit(limit)
        .all()
    )
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


@ai_bp.route('/config', methods=['GET'])
@login_required
def get_config():
    """Return current AI config (sanitized — no API keys)."""
    cfg = current_app.config
    return jsonify({
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
    allowed = {
        'AI_LLM_MODEL', 'AI_STT_BACKEND', 'AI_STT_MODEL_SIZE',
        'AI_DEFAULT_LANGUAGE', 'AI_MAX_TOOL_ITERATIONS', 'AI_SARVAM_API_KEY'
    }
    updated = {}
    for key, val in data.items():
        env_key = key.upper()
        if env_key in allowed:
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
    session_id = data.get('session_id', '')
    if session_id:
        join_room(session_id)
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

    user_message = (data.get('message') or '').strip()
    session_id   = data.get('session_id') or str(uuid.uuid4())

    if not user_message:
        emit('chat_error', {"error": "Empty message."})
        return

    # Notify client we are processing
    emit('chat_typing', {"status": "thinking"}, room=session_id)

    assistant = get_assistant()

    if not assistant.is_available():
        model = current_app.config.get('AI_LLM_MODEL', 'qwen3:8b')
        emit('chat_response', {
            "text": f"⚠️ AI offline. Run `ollama pull {model}` then restart Ollama.",
            "action": None,
            "navigate_to": None,
            "tool_calls": [],
            "error": "ollama_unavailable",
        }, room=session_id)
        return

    try:
        result = assistant.chat(user_message, session_id, current_user.id)
        emit('chat_response', result, room=session_id)
    except Exception as exc:
        log.exception("[SocketIO] chat_message error")
        emit('chat_error', {"error": str(exc)}, room=session_id)
