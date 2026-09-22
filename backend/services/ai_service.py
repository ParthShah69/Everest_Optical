"""
ai_service.py
=============
Core AI assistant — supports local Ollama and Groq's hosted, OpenAI-compatible
API, with a ReAct (Reason → Act → Observe) loop and per-session history.

Public interface:
    assistant = AIAssistant()
    result = assistant.chat(user_message, session_id, user_id)
    # result = {
    #     'text': str,           # reply to display in chat
    #     'action': str | None,  # 'navigate' | 'create' | etc.
    #     'navigate_to': str,    # URL if action='navigate'
    #     'language': str,       # detected input language
    #     'tool_calls': list,    # tools that were executed
    #     'error': str | None,
    # }
"""

import json
import inspect
import logging
import time
from decimal import Decimal
from types import UnionType
from typing import Union, get_args, get_origin

import requests

try:
    import ollama as _ollama
    _OllamaResponseError = _ollama.ResponseError
except (ImportError, AttributeError):
    _ollama = None
    class _OllamaResponseError(Exception):
        pass

from flask import current_app


from services.ai_glossary import MULTILINGUAL_GLOSSARY_PROMPT
from services.ai_tools import TOOLS
from services.language_service import detect_language, extract_english_name
from extensions import db
from models.chat_history import ChatMessage

log = logging.getLogger(__name__)


class AIProviderError(Exception):
    """A safe, provider-neutral error for the chat route and UI."""


def _json_type(annotation):
    """Translate the simple Python tool annotations into JSON-schema types."""
    if annotation in (inspect.Parameter.empty, str, None):
        return {"type": "string"}
    if annotation is bool:
        return {"type": "boolean"}
    if annotation is int:
        return {"type": "integer"}
    if annotation in (float, Decimal):
        return {"type": "number"}
    if annotation is list:
        return {"type": "array", "items": {"type": "object"}}
    if annotation is dict:
        return {"type": "object"}
    origin = get_origin(annotation)
    if origin is list:
        args = get_args(annotation)
        return {"type": "array", "items": _json_type(args[0] if args else str)}
    if origin is dict:
        return {"type": "object"}
    if origin in (Union, UnionType):
        args = [arg for arg in get_args(annotation) if arg is not type(None)]
        return _json_type(args[0] if args else str)
    return {"type": "string"}


def _openai_tools():
    """Build the OpenAI-compatible tool schema without another dependency."""
    result = []
    for function in TOOLS:
        signature = inspect.signature(function)
        properties = {
            name: _json_type(parameter.annotation)
            for name, parameter in signature.parameters.items()
        }
        required = [
            name for name, parameter in signature.parameters.items()
            if parameter.default is inspect.Parameter.empty
        ]
        parameters = {"type": "object", "properties": properties}
        if required:
            parameters["required"] = required
        result.append({
            "type": "function",
            "function": {
                "name": function.__name__,
                "description": inspect.getdoc(function) or function.__name__,
                "parameters": parameters,
            },
        })
    return result

# ---------------------------------------------------------------------------
# System prompt
# ---------------------------------------------------------------------------

SYSTEM_PROMPT = """You are the AI assistant for Everest Optical ERP. You help staff manage
customers, orders, prescriptions, inventory, and users through natural conversation.

CORE RULES:
1. Always use the provided tools to perform actions — never make up data.
2. If information is missing, ASK for it before calling a tool.
3. ALWAYS call search_customers before creating a customer (to prevent duplicates).
4. For orders: search/create the customer first, then create the order.
5. ALL database values (names, descriptions) MUST be in English.
6. Respond in the SAME language/style the user uses (Hindi/Gujarati/English/mixed).
7. For navigation requests ("show me", "open", "jao", "dikhao"), use navigate_to_page.
8. For DELETE actions: always ask for confirmation first. Never delete without consent.
9. For prescription values: warn if SPH > ±20 or CYL > ±10; AXIS must be 0-180.
10. Calculate all money server-side; never guess totals.
11. If Ollama or a tool fails, say so clearly and offer manual navigation.

MULTILINGUAL UNDERSTANDING:
""" + MULTILINGUAL_GLOSSARY_PROMPT + """

RESPONSE FORMAT:
- Keep responses concise and actionable.
- After a successful creation/update, always offer the next logical step.
- Use the user's language; keep emojis minimal (1-2 max per message).
- When returning search results, list them clearly with numbers.
- For forms that need multiple fields, gather ONE field per turn if not all provided upfront.
"""

# ---------------------------------------------------------------------------
# AIAssistant class
# ---------------------------------------------------------------------------

class AIAssistant:
    """Manages the conversation loop for a single chat session."""

    def __init__(self):
        try:
            cfg = current_app.config
            self.provider      = cfg.get('AI_PROVIDER', 'ollama').lower()
            self.model         = cfg.get('AI_LLM_MODEL', 'qwen3:8b')
            self.ollama_host   = cfg.get('AI_OLLAMA_HOST', 'http://localhost:11434')
            self.groq_base_url = cfg.get('AI_GROQ_BASE_URL', 'https://api.groq.com/openai/v1').rstrip('/')
            self.groq_api_key  = cfg.get('GROQ_API_KEY', '')
            self.max_iters     = int(cfg.get('AI_MAX_TOOL_ITERATIONS', 6))
        except RuntimeError:
            self.provider    = 'ollama'
            self.model       = 'qwen3:8b'
            self.ollama_host = 'http://localhost:11434'
            self.groq_base_url = 'https://api.groq.com/openai/v1'
            self.groq_api_key = ''
            self.max_iters   = 6

        if self.provider not in {'ollama', 'groq'}:
            log.warning("[AI] Unknown provider '%s'; falling back to ollama", self.provider)
            self.provider = 'ollama'

        # Configure the local client only when local Ollama is selected.
        if self.provider == 'ollama' and _ollama:
            self._client = _ollama.Client(host=self.ollama_host)
        else:
            self._client = None

    # ------------------------------------------------------------------
    def is_available(self) -> bool:
        """Check that the selected provider is reachable and has the model."""
        if self.provider == 'groq':
            if not self.groq_api_key:
                return False
            try:
                response = requests.get(
                    f'{self.groq_base_url}/models/{self.model}',
                    headers={'Authorization': f'Bearer {self.groq_api_key}'},
                    timeout=(3, 5),
                )
                return response.ok
            except requests.RequestException:
                return False

        if not self._client:
            return False
        try:
            models_response = self._client.list()
            models = getattr(models_response, 'models', None)
            if models is None and isinstance(models_response, dict):
                models = models_response.get('models')
            if models is None:
                return True
            names = {
                (getattr(model, 'model', None) or getattr(model, 'name', None)
                 or (model.get('model') if isinstance(model, dict) else None)
                 or (model.get('name') if isinstance(model, dict) else None))
                for model in (models or [])
            }
            # Older Ollama clients may not return a model list.  A successful
            # reachability check is still useful there; chat will give the
            # existing friendly model-not-found response if necessary.
            return self.model in names
        except Exception:
            return False

    # ------------------------------------------------------------------
    def chat(self, user_message: str, session_id: str, user_id: int) -> dict:
        """
        Main chat entry point:
          1. Detects input language
          2. Builds message history (recent turns from DB)
          3. Runs ReAct tool-calling loop
          4. Persists the exchange to DB
          5. Returns response dict
        """
        user_message = (user_message or '').strip()
        if not user_message:
            return {"text": "Please enter a message.", "action": None, "navigate_to": None,
                    "language": "en", "tool_calls": [], "error": "empty_message"}
        start = time.time()
        lang = detect_language(user_message)
        log.info(f"[AI] Chat request from user={user_id} session={session_id[:8]} lang={lang}")

        # Fetch conversation history for this session (last 10 messages)
        history_rows = self._history_rows(session_id, user_id)
        history_rows.reverse()

        messages = [{"role": "system", "content": SYSTEM_PROMPT}]
        for row in history_rows:
            if row.role in ("user", "assistant"):
                messages.append({"role": row.role, "content": row.content})

        # Append current user message
        messages.append({"role": "user", "content": user_message})

        # Persist the user message
        self._save_message(session_id, user_id, "user", user_message, lang, user_message)

        try:
            result = self._react_loop(messages)
        except (_OllamaResponseError, AIProviderError) as exc:
            log.error(f"[AI] Provider response error: {exc}")
            error_text = self._provider_error_reply(str(exc), lang)
            self._save_message(session_id, user_id, "assistant", error_text, lang)
            return {"text": error_text, "action": None, "navigate_to": None,
                    "language": lang, "tool_calls": [], "error": str(exc)}
        except Exception as exc:
            log.exception("[AI] Unexpected error in chat")
            error_text = self._generic_error_reply(lang)
            self._save_message(session_id, user_id, "assistant", error_text, lang)
            return {"text": error_text, "action": None, "navigate_to": None,
                    "language": lang, "tool_calls": [], "error": str(exc)}

        elapsed = round(time.time() - start, 2)
        log.info(f"[AI] session={session_id[:8]} lang={lang} tools={len(result['tool_calls'])} time={elapsed}s")

        # Persist the assistant's final reply
        self._save_message(
            session_id, user_id, "assistant",
            result["text"], lang,
            action_type=result.get("action"),
        )

        result["language"] = lang
        return result

    # ------------------------------------------------------------------
    def _react_loop(self, messages: list) -> dict:
        """
        ReAct loop: call LLM → if tool_calls → execute → append result → repeat.
        Returns {'text', 'action', 'navigate_to', 'tool_calls'}.
        """
        tool_calls_made = []

        for iteration in range(self.max_iters):
            msg = self._complete(messages)
            tool_calls = self._tool_calls(msg)

            # If no tool calls — we have the final text answer
            if not tool_calls:
                content = msg.get('content') if self.provider == 'groq' else msg.content
                text         = (content or "").strip()
                action       = None
                navigate_to  = None

                # Check if last tool result was a navigate action
                if tool_calls_made:
                    last_result = tool_calls_made[-1].get("result", {})
                    if isinstance(last_result, dict) and last_result.get("action") == "navigate":
                        action      = "navigate"
                        navigate_to = last_result.get("url")

                return {
                    "text": text or "✓ Done.",
                    "action": action,
                    "navigate_to": navigate_to,
                    "tool_calls": tool_calls_made,
                    "error": None,
                }

            # Execute each tool call
            # Ollama accepts its Message object; Groq needs the response dict
            # including tool-call ids before the tool results can be appended.
            messages.append(msg)

            for tc in tool_calls:
                fn_name, fn_args, tool_call_id = self._tool_call_parts(tc)

                log.info(f"[AI][iter={iteration}] Tool call: {fn_name}({fn_args})")

                tool_result_str = self._execute_tool(fn_name, fn_args)
                tool_result_obj = {}
                try:
                    tool_result_obj = json.loads(tool_result_str)
                except Exception:
                    pass

                tool_calls_made.append({
                    "name": fn_name,
                    "args": fn_args,
                    "result": tool_result_obj,
                })

                tool_message = {"role": "tool", "content": tool_result_str}
                if self.provider == 'groq':
                    tool_message['tool_call_id'] = tool_call_id
                else:
                    tool_message['name'] = fn_name
                messages.append(tool_message)

        # Safety: too many iterations
        log.warning(f"[AI] Reached max iterations ({self.max_iters})")
        return {
            "text": "I'm having trouble completing this in one go. Could you try breaking the request into smaller steps?",
            "action": None,
            "navigate_to": None,
            "tool_calls": tool_calls_made,
            "error": "max_iterations_reached",
        }

    # ------------------------------------------------------------------
    def _execute_tool(self, name: str, args: dict) -> str:
        """Dispatch a tool call by name. Returns JSON string."""
        tool_map = {fn.__name__: fn for fn in TOOLS}

        if name not in tool_map:
            return json.dumps({"success": False, "error": f"Unknown tool '{name}'"})

        try:
            # Tools need an active app context — they import from flask
            result = tool_map[name](**args)
            return result if isinstance(result, str) else json.dumps(result)
        except TypeError as exc:
            # Wrong arguments from LLM
            log.warning(f"[AI][Tool:{name}] Argument mismatch: {exc}")
            return json.dumps({"success": False, "error": f"Invalid arguments for tool '{name}': {exc}"})
        except Exception as exc:
            log.exception(f"[AI][Tool:{name}] Execution error")
            return json.dumps({"success": False, "error": str(exc)})

    def _complete(self, messages):
        """Return the provider's assistant message in its native format."""
        if self.provider == 'ollama':
            response = self._client.chat(
                model=self.model,
                messages=messages,
                tools=TOOLS,
                options={"temperature": 0.2, "num_predict": 1024},
            )
            return response.message

        try:
            response = requests.post(
                f'{self.groq_base_url}/chat/completions',
                headers={
                    'Authorization': f'Bearer {self.groq_api_key}',
                    'Content-Type': 'application/json',
                },
                json={
                    'model': self.model,
                    'messages': messages,
                    'tools': _openai_tools(),
                    'tool_choice': 'auto',
                    'temperature': 0.2,
                    'max_tokens': 1024,
                },
                timeout=(5, 45),
            )
        except requests.RequestException as exc:
            raise AIProviderError('The hosted AI service could not be reached.') from exc

        if not response.ok:
            if response.status_code == 401:
                raise AIProviderError('The hosted AI key is invalid or missing.')
            if response.status_code == 429:
                raise AIProviderError('The hosted AI free-tier limit has been reached. Please try later.')
            raise AIProviderError('The hosted AI service is temporarily unavailable.')

        try:
            message = response.json()['choices'][0]['message']
        except (KeyError, IndexError, TypeError, ValueError) as exc:
            raise AIProviderError('The hosted AI returned an invalid response.') from exc
        return message

    def _tool_calls(self, message):
        return message.get('tool_calls', []) if self.provider == 'groq' else getattr(message, 'tool_calls', [])

    def _tool_call_parts(self, tool_call):
        if self.provider == 'groq':
            function = tool_call.get('function', {})
            try:
                arguments = json.loads(function.get('arguments') or '{}')
            except (TypeError, ValueError):
                arguments = {}
            return function.get('name'), arguments, tool_call.get('id')
        return tool_call.function.name, tool_call.function.arguments or {}, None

    # ------------------------------------------------------------------
    def _load_history(self, session_id: str, limit: int = 20) -> list:
        """Load the last N message pairs for the session from DB."""
        try:
            rows = (
                ChatMessage.query
                .filter_by(session_id=session_id)
                .filter(ChatMessage.role.in_(["user", "assistant"]))
                .order_by(ChatMessage.created_at.desc())
                .limit(limit)
                .all()
            )
            # Return in chronological order (oldest first)
            return [{"role": r.role, "content": r.content} for r in reversed(rows)]
        except Exception as exc:
            log.warning(f"[AI] Could not load chat history: {exc}")
            return []

    @staticmethod
    def _history_rows(session_id: str, user_id: int):
        """Read history without allowing a transient DB error to crash chat."""
        try:
            rows = (
                ChatMessage.query
                .filter_by(session_id=session_id, user_id=user_id)
                .filter(ChatMessage.role.in_(["user", "assistant"]))
                .order_by(ChatMessage.created_at.desc())
                .limit(10)
                .all()
            )
            return rows
        except Exception as exc:
            log.warning("[AI] Could not read chat history: %s", exc)
            db.session.rollback()
            return []

    # ------------------------------------------------------------------
    def _save_message(
        self,
        session_id: str,
        user_id: int,
        role: str,
        content: str,
        lang: str = "en",
        original_text: str = None,
        action_type: str = None,
    ):
        """Persist a chat message to the DB (best-effort)."""
        try:
            msg = ChatMessage(
                session_id=session_id,
                user_id=user_id,
                role=role,
                content=content,
                original_language=lang,
                original_text=original_text,
                action_type=action_type,
            )
            db.session.add(msg)
            db.session.commit()
        except Exception as exc:
            log.warning(f"[AI] Failed to save chat message: {exc}")
            db.session.rollback()

    # ------------------------------------------------------------------
    @staticmethod
    def _provider_error_reply(error: str, lang: str) -> str:
        """Return a friendly provider-neutral error based on detected language."""
        if "connection refused" in error.lower() or "could not be reached" in error.lower():
            msgs = {
                "hi": "माफ़ करें, AI सर्वर (Ollama) चालू नहीं है। कृपया Ollama शुरू करें।",
                "gu": "માફ કરો, AI સર્વર (Ollama) ચાલુ નથી. Ollama ચાલુ કરો.",
                "romanized_hi": "Maaf karo, AI server (Ollama) chal nahi raha. Please Ollama start karo.",
                "romanized_gu": "Maaf karjo, AI server (Ollama) chalu nathi. Ollama start karo.",
            }
            return msgs.get(lang, "⚠️ The AI service is offline. Please try again shortly.")
        return "⚠️ The AI service is unavailable. Please try again shortly."

    @staticmethod
    def _generic_error_reply(lang: str) -> str:
        msgs = {
            "hi": "कुछ गड़बड़ हो गई। कृपया दोबारा कोशिश करें।",
            "gu": "કંઈક ખોટું થઈ ગયું. ફરી પ્રયાસ કરો.",
            "romanized_hi": "Kuch gadbad ho gayi. Dobara koshish karo.",
            "romanized_gu": "Kainck khotu thayuu. Fari try karo.",
        }
        return msgs.get(lang, "⚠️ Something went wrong. Please try again or rephrase your request.")


# ---------------------------------------------------------------------------
# Module-level singleton (lazy, recreated per request for correct app context)
# ---------------------------------------------------------------------------

def get_assistant() -> AIAssistant:
    """Return a fresh AIAssistant bound to the current app context."""
    return AIAssistant()
