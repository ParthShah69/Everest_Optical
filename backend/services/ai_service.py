"""
ai_service.py
=============
Core AI assistant — wraps Ollama with a ReAct (Reason → Act → Observe) loop
and manages per-session conversation history stored in the DB.

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
import logging
import time
from datetime import datetime

import ollama as _ollama

from flask import current_app

from services.ai_glossary import MULTILINGUAL_GLOSSARY_PROMPT
from services.ai_tools import TOOLS
from services.language_service import detect_language, extract_english_name
from extensions import db
from models.chat_history import ChatMessage

log = logging.getLogger(__name__)

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
            self.model         = cfg.get('AI_LLM_MODEL', 'qwen3:8b')
            self.ollama_host   = cfg.get('AI_OLLAMA_HOST', 'http://localhost:11434')
            self.max_iters     = int(cfg.get('AI_MAX_TOOL_ITERATIONS', 6))
        except RuntimeError:
            self.model       = 'qwen3:8b'
            self.ollama_host = 'http://localhost:11434'
            self.max_iters   = 6

        # Configure ollama client host
        self._client = _ollama.Client(host=self.ollama_host)

    # ------------------------------------------------------------------
    def is_available(self) -> bool:
        """Check if Ollama server + model are reachable."""
        try:
            self._client.list()
            return True
        except Exception:
            return False

    # ------------------------------------------------------------------
    def chat(self, user_message: str, session_id: str, user_id: int) -> dict:
        """
        Main entry point.

        Returns a dict:
        {
            text, action, navigate_to, language, tool_calls, error
        }
        """
        start = time.time()

        # Detect language
        lang = detect_language(user_message)

        # Retrieve conversation history for this session
        history = self._load_history(session_id)

        # Build message list
        messages = [{"role": "system", "content": SYSTEM_PROMPT}]
        messages.extend(history)
        messages.append({"role": "user", "content": user_message})

        # Persist the user message
        self._save_message(session_id, user_id, "user", user_message, lang, user_message)

        try:
            result = self._react_loop(messages)
        except _ollama.ResponseError as exc:
            log.error(f"[AI] Ollama response error: {exc}")
            error_text = self._ollama_error_reply(str(exc), lang)
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
            response = self._client.chat(
                model=self.model,
                messages=messages,
                tools=TOOLS,
                options={"temperature": 0.2, "num_predict": 1024},
            )
            msg = response.message

            # If no tool calls — we have the final text answer
            if not msg.tool_calls:
                text         = (msg.content or "").strip()
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
            messages.append(msg)  # add assistant message with tool_calls

            for tc in msg.tool_calls:
                fn_name = tc.function.name
                fn_args = tc.function.arguments or {}

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

                messages.append({
                    "role": "tool",
                    "content": tool_result_str,
                    "name": fn_name,
                })

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
    def _ollama_error_reply(error: str, lang: str) -> str:
        """Return a friendly error based on detected language."""
        if "connection refused" in error.lower() or "connect" in error.lower():
            msgs = {
                "hi": "माफ़ करें, AI सर्वर (Ollama) चालू नहीं है। कृपया Ollama शुरू करें।",
                "gu": "માફ કરો, AI સર્વર (Ollama) ચાલુ નથી. Ollama ચાલુ કરો.",
                "romanized_hi": "Maaf karo, AI server (Ollama) chal nahi raha. Please Ollama start karo.",
                "romanized_gu": "Maaf karjo, AI server (Ollama) chalu nathi. Ollama start karo.",
            }
            return msgs.get(lang, "⚠️ The AI server (Ollama) is not running. Please start Ollama and try again.")
        if "model" in error.lower() and "not found" in error.lower():
            return "⚠️ The AI model is not downloaded yet. Run: `ollama pull qwen3:8b` in your terminal."
        return f"⚠️ AI error: {error}"

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
