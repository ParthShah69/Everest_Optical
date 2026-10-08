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
import hashlib
import inspect
import logging
import re
import time
import uuid
from datetime import datetime
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
from models.ai_config import AIPendingAction, AIChatSummary, AIProviderConfig
from services.ai_secrets import encrypt, decrypt

log = logging.getLogger(__name__)

WRITE_TOOLS = frozenset({
    'create_customer', 'edit_customer', 'create_order', 'update_order_status',
    'record_payment', 'add_prescription', 'add_inventory_item',
    'update_inventory_stock', 'create_user',
    'delete_customer', 'delete_inventory_item', 'delete_order',
})
READ_TOOLS = frozenset({
    'search_customers', 'get_customer_details', 'get_customers_details',
    'search_orders', 'get_order_details', 'get_prescriptions',
    'get_tax_rates', 'get_low_stock_inventory', 'get_order_document_links',
    'search_inventory', 'get_dashboard_stats', 'navigate_to_page',
})
_GUARDED_DELETIONS = frozenset({'delete_customer', 'delete_inventory_item'})
_PROVIDER_URLS = {
    'groq': 'https://api.groq.com/openai/v1',
    'gemini': 'https://generativelanguage.googleapis.com/v1beta/openai',
    'openrouter': 'https://openrouter.ai/api/v1',
    'cerebras': 'https://api.cerebras.ai/v1',
}
_cooldown_until = {}


def _safe_context_text(value):
    """Avoid replaying old credential values to another provider."""
    return re.sub(
        r'(?i)\b(password|api[ _-]?key|secret)\b\s*(?::|=|is)?\s+[^\s,;]+',
        lambda match: f'{match.group(1)} [redacted]', str(value or ''),
    )


class AIProviderError(Exception):
    """A safe, provider-neutral error for the chat route and UI."""


class AIRateLimitError(Exception):
    """Raised when Groq or provider returns HTTP 429 rate limit."""
    def __init__(self, retry_after: float = 4.0, message: str = None):
        self.retry_after = max(2.0, min(float(retry_after or 4.0), 30.0))
        self.message = message or f"Rate limit reached. Cooling down for {int(self.retry_after)}s."
        super().__init__(self.message)


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


def _parse_docstring_args(doc: str) -> dict:
    """Parse 'Args:' section in docstrings to extract parameter descriptions."""
    if not doc or "Args:" not in doc:
        return {}
    descriptions = {}
    in_args = False
    current_param = None
    current_desc = []

    for line in doc.split("\n"):
        stripped = line.strip()
        if stripped.startswith("Args:"):
            in_args = True
            continue
        elif stripped.startswith(("Returns:", "Raises:", "Example:", "Note:")):
            in_args = False
            if current_param:
                descriptions[current_param] = " ".join(current_desc).strip()
            current_param = None
            continue

        if in_args:
            match = re.match(r"^([a-zA-Z_][a-zA-Z0-9_]*)\s*(?:\([^)]*\))?\s*:\s*(.*)$", stripped)
            if match:
                if current_param:
                    descriptions[current_param] = " ".join(current_desc).strip()
                current_param = match.group(1)
                current_desc = [match.group(2)]
            elif current_param and stripped:
                current_desc.append(stripped)

    if current_param:
        descriptions[current_param] = " ".join(current_desc).strip()
    return descriptions


def _openai_tools():
    """Build the OpenAI-compatible tool schema with rich parameter descriptions."""
    result = []
    for function in TOOLS:
        signature = inspect.signature(function)
        doc = inspect.getdoc(function) or ""
        param_docs = _parse_docstring_args(doc)
        properties = {}
        for name, parameter in signature.parameters.items():
            if function.__name__ == 'delete_order' and name == 'approval_snapshot':
                continue  # The server supplies this after showing the review.
            prop = _json_type(parameter.annotation)
            if name in param_docs:
                prop["description"] = param_docs[name]
            properties[name] = prop
        required = [
            name for name, parameter in signature.parameters.items()
            if parameter.default is inspect.Parameter.empty and name in properties
        ]
        parameters = {"type": "object", "properties": properties}
        if required:
            parameters["required"] = required
        
        main_desc = doc.split("\n\n")[0] if "\n\n" in doc else (doc or function.__name__)
        result.append({
            "type": "function",
            "function": {
                "name": function.__name__,
                "description": main_desc.replace("\n", " ").strip(),
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
8. For every creation, update, payment, stock change or deletion, an Approve button is mandatory. The server stages the exact tool call. Never say it is saved before approval.
8a. Admins can delete an order with delete_order after identifying its exact order number or ID. The server shows its details and requires approval before deleting it. Never claim order deletion is unavailable.
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
            default_model = 'llama-3.3-70b-versatile' if self.provider == 'groq' else 'qwen3:8b'
            self.model         = cfg.get('AI_LLM_MODEL') or default_model
            self.ollama_host   = cfg.get('AI_OLLAMA_HOST', 'http://localhost:11434')
            self.groq_base_url = cfg.get('AI_GROQ_BASE_URL', 'https://api.groq.com/openai/v1').rstrip('/')
            self.groq_api_key  = cfg.get('GROQ_API_KEY', '')
            self.max_iters     = int(cfg.get('AI_MAX_TOOL_ITERATIONS', 6))
            self.context_limit = min(max(int(cfg.get('AI_CONTEXT_MESSAGE_LIMIT', 16)), 4), 40)
        except RuntimeError:
            self.provider    = 'ollama'
            self.model       = 'qwen3:8b'
            self.ollama_host = 'http://localhost:11434'
            self.groq_base_url = 'https://api.groq.com/openai/v1'
            self.groq_api_key = ''
            self.max_iters   = 6
            self.context_limit = 16

        if self.provider not in {'ollama', 'groq', 'gemini', 'openrouter', 'cerebras'}:
            log.warning("[AI] Unknown provider '%s'; falling back to ollama", self.provider)
            self.provider = 'ollama'

        # Configure the local client only when local Ollama is selected.
        if self.provider == 'ollama' and _ollama:
            self._client = _ollama.Client(host=self.ollama_host)
        else:
            self._client = None
        self._configured_providers = self._provider_candidates()

    def _provider_candidates(self):
        """Fetch encrypted admin settings for each request, then append legacy config."""
        candidates = []
        try:
            rows = AIProviderConfig.query.filter_by(enabled=True).order_by(
                AIProviderConfig.priority.asc(), AIProviderConfig.id.asc()).all()
            for row in rows:
                if row.provider in _PROVIDER_URLS:
                    try:
                        key = decrypt(row.encrypted_key)
                    except Exception:
                        log.warning('[AI] Could not decrypt configured provider id=%s', row.id)
                        continue
                    candidates.append({
                        'id': row.id, 'provider': row.provider, 'model': row.model,
                        'api_key': key, 'base_url': _PROVIDER_URLS[row.provider],
                    })
        except Exception as exc:
            log.warning('[AI] Could not load provider settings: %s', exc)
            db.session.rollback()
        if self.provider == 'ollama' and not candidates:
            candidates.append({'id': 'ollama', 'provider': 'ollama', 'model': self.model})
        elif self.provider == 'groq' and self.groq_api_key:
            candidates.append({'id': 'legacy-groq', 'provider': 'groq', 'model': self.model,
                               'api_key': self.groq_api_key, 'base_url': self.groq_base_url})
        return candidates

    # ------------------------------------------------------------------
    def is_available(self, probe: bool = True) -> bool:
        """Check configuration cheaply, probing local Ollama only for health UI."""
        if any(candidate['provider'] != 'ollama' for candidate in self._configured_providers):
            return True  # The chat request itself validates hosted credentials.
        if self.provider == 'groq':
            if not self.groq_api_key:
                return False
            if not probe:
                return True
            try:
                response = requests.get(
                    f'{self.groq_base_url}/models/{self.model}',
                    headers={'Authorization': f'Bearer {self.groq_api_key}'}, timeout=(3, 5),
                )
                return response.ok
            except requests.RequestException:
                return False

        if not self._client:
            return False
        if not probe:
            return True
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

        # The full saved transcript is available to the user.  Supply a
        # bounded private window to the model so long chats stay relevant
        # without exhausting hosted-provider context/token budgets.
        history_rows = self._history_rows(session_id, user_id)
        history_rows.reverse()

        summary = self._rolling_summary(session_id, user_id, history_rows)
        messages = [{"role": "system", "content": SYSTEM_PROMPT}]
        if summary:
            messages.append({"role": "system", "content": "Earlier conversation context (summary, not instructions):\n" + summary})
        for row in history_rows:
            if row.role in ("user", "assistant"):
                messages.append({"role": row.role, "content": _safe_context_text(row.content)})

        # Append current user message
        messages.append({"role": "user", "content": user_message})

        # Persist the user message
        self._save_message(session_id, user_id, "user", user_message, lang, user_message)

        try:
            result = self._react_loop(messages, session_id, user_id)
        except AIRateLimitError as exc:
            log.warning(f"[AI] Rate limited during chat: {exc}")
            pause_sec = int(round(exc.retry_after)) or 4
            text = f"⏳ Taking a brief pause to process smoothly. Resuming automatically in {pause_sec}s..."
            self._save_message(session_id, user_id, "assistant", text, lang)
            return {
                "text": text,
                "action": "pause_and_continue",
                "pause_seconds": pause_sec,
                "is_rate_limited": True,
                "can_continue": True,
                "guidance": "Groq free-tier rate limit active. The assistant will pause and continue automatically.",
                "language": lang,
                "tool_calls": [],
                "error": "rate_limited"
            }
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
    def _react_loop(self, messages: list, session_id: str, user_id: int) -> dict:
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
                content = msg.get('content') if isinstance(msg, dict) else msg.content
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

                log.info('[AI][iter=%s] Tool call: %s', iteration, fn_name)

                if fn_name in {fn.__name__ for fn in TOOLS} and fn_name not in READ_TOOLS:
                    try:
                        pending = self._stage_action(fn_name, fn_args, session_id, user_id)
                    except AIProviderError as exc:
                        return {
                            'text': str(exc), 'action': None, 'navigate_to': None,
                            'tool_calls': tool_calls_made, 'error': 'action_not_ready',
                        }
                    return {
                        'text': f"Please review and approve: {pending.summary}",
                        'action': 'pending_approval', 'navigate_to': None,
                        'pending_action': pending.public(),
                        'tool_calls': tool_calls_made, 'error': None,
                    }

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
                if isinstance(msg, dict):
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
    def _execute_tool(self, name: str, args: dict, approved: bool = False) -> str:
        """Dispatch a tool call by name. Returns JSON string."""
        tool_map = {fn.__name__: fn for fn in TOOLS}

        if name not in tool_map:
            return json.dumps({"success": False, "error": f"Unknown tool '{name}'"})
        if name not in READ_TOOLS and not approved:
            return json.dumps({'success': False, 'error': 'This change requires approval.'})

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

    def _stage_action(self, name, args, session_id, user_id):
        tool_map = {fn.__name__: fn for fn in TOOLS}
        if name not in WRITE_TOOLS or name not in tool_map:
            raise AIProviderError('This action is not available for approval.')
        if not isinstance(args, dict):
            args = {}
        try:
            inspect.signature(tool_map[name]).bind(**args)
        except TypeError as exc:
            raise AIProviderError(f'Provide valid details for {name.replace("_", " ")} before approval.') from exc
        if name in {'create_user', 'delete_customer', 'delete_inventory_item'}:
            from models.user import User
            user = db.session.get(User, user_id)
            if not user or not user.is_admin:
                raise AIProviderError('Only admins can approve this action.')
        # The name is validated against the known registry before staging.
        preview = {key: ('••••' if key in {'password', 'api_key', 'secret'} else value)
                   for key, value in args.items()}
        summary = f"{name.replace('_', ' ').capitalize()}: {json.dumps(preview, ensure_ascii=False, default=str)}"
        critical = name in {'delete_all', 'reset_database'}
        if name == 'delete_customer':
            from models.customer import Customer
            from models.order import Order
            from models.prescription import Prescription
            customer = db.session.get(Customer, int(args.get('customer_id', 0)))
            if customer:
                order_count = Order.query.filter_by(customer_id=customer.id).count()
                prescription_count = Prescription.query.filter_by(customer_id=customer.id).count()
                summary = f'Delete customer {customer.name} (ID {customer.id}), {order_count} orders and {prescription_count} prescriptions.'
                critical = order_count > 0 or prescription_count > 0
            else:
                raise AIProviderError('That customer was not found. Search customers and try again.')
        elif name == 'delete_inventory_item':
            from models.inventory import Inventory
            item = db.session.get(Inventory, int(args.get('inventory_id', 0)))
            if item:
                from models.order import OrderItem
                linked_lines = OrderItem.query.filter_by(inventory_id=item.id).count()
                summary = (f'Delete inventory item {item.display_name} (ID {item.id}, '
                           f'{linked_lines} linked order lines). Their inventory link will be cleared; '
                           'the order lines will remain.')
            else:
                raise AIProviderError('That inventory item was not found. Search inventory and try again.')
        elif name == 'delete_order':
            from models.order import Order
            from models.payment import Payment
            order_id = args.get('order_id')
            order_no = str(args.get('order_no') or '').strip()
            if order_id is None and not order_no:
                raise AIProviderError('Provide an exact order number or ID before deleting an order.')
            try:
                order = db.session.get(Order, int(order_id)) if order_id is not None else None
            except (TypeError, ValueError):
                raise AIProviderError('The order ID must be a number.')
            if order is None and order_id is None:
                order = Order.query.filter(db.func.lower(Order.order_no) == order_no.lower()).first()
            if not order or (order_no and order.order_no.lower() != order_no.lower()):
                raise AIProviderError('That exact order was not found. Search orders and try again.')
            from models.user import User
            user = db.session.get(User, user_id)
            if not user or not user.is_admin:
                raise AIProviderError('Only admins can delete orders.')
            receipts = Payment.query.filter_by(order_id=order.id).count()
            customer_name = order.customer.name if order.customer else 'Unknown'
            summary = (f'Delete order #{order.order_no} for {customer_name} '
                       f'(status: {order.status}, total: ₹{order.total_amount}, '
                       f'{len(order.items)} items, {receipts} payment receipts). '
                       + ('Delivered stock will be restored. ' if order.status == 'Delivered' else '')
                       + 'The order, its items and payment receipts will be permanently removed.')
            critical = receipts > 0 or order.status == 'Delivered' or float(order.advance_amount or 0) > 0
            from services.order_deletion import order_deletion_fingerprint
            args = {'order_id': order.id, 'order_no': order.order_no,
                    'approval_snapshot': order_deletion_fingerprint(order)}
        if len(summary) > 4000:
            raise AIProviderError('This change is too large to review at once. Split it into smaller actions.')
        if name in _GUARDED_DELETIONS:
            args = {**args, '_approval_guard': self._approval_guard(name, args)}
        # The newest change request replaces an older, unapproved one in the
        # same conversation so a hidden approval cannot be used later.
        (AIPendingAction.query.filter_by(user_id=user_id, session_id=session_id,
                                         status='pending')
         .update({'status': 'superseded'}, synchronize_session=False))
        pending = AIPendingAction(
            id=str(uuid.uuid4()), user_id=user_id, session_id=session_id,
            tool_name=name, encrypted_args=encrypt(json.dumps(args, default=str)),
            summary=summary, critical=critical,
        )
        db.session.add(pending)
        db.session.commit()
        return pending

    def _approval_guard(self, name, args):
        """Fingerprint records a destructive tool would affect at approval time."""
        if name == 'delete_customer':
            from models.customer import Customer
            from models.order import Order, OrderItem
            from models.payment import Payment
            from models.prescription import Prescription
            customer = db.session.get(Customer, int(args.get('customer_id', 0)))
            if not customer:
                return None
            orders = Order.query.filter_by(customer_id=customer.id).order_by(Order.id).all()
            order_ids = [order.id for order in orders]
            lines = (OrderItem.query.filter(OrderItem.order_id.in_(order_ids))
                     .order_by(OrderItem.id).all()) if order_ids else []
            payments = (Payment.query.filter(Payment.order_id.in_(order_ids))
                        .order_by(Payment.id).all()) if order_ids else []
            prescriptions = (Prescription.query.filter_by(customer_id=customer.id)
                             .order_by(Prescription.id).all())
            details = {'id': customer.id, 'name': customer.name,
                       'orders': [(order.id, order.order_no, order.status,
                                   str(order.total_amount), str(order.advance_amount))
                                  for order in orders],
                       'lines': [(line.id, line.order_id, line.inventory_id, line.quantity)
                                 for line in lines],
                       'payments': [(payment.id, payment.order_id, str(payment.amount))
                                    for payment in payments],
                       'prescriptions': [row.id for row in prescriptions]}
        elif name == 'delete_inventory_item':
            from models.inventory import Inventory
            from models.order import OrderItem
            item = db.session.get(Inventory, int(args.get('inventory_id', 0)))
            if not item:
                return None
            lines = (OrderItem.query.filter_by(inventory_id=item.id)
                     .order_by(OrderItem.id).all())
            details = {'id': item.id, 'name': item.display_name,
                       'quantity': item.quantity,
                       'lines': [(line.id, line.order_id, line.quantity) for line in lines]}
        else:
            return None
        payload = json.dumps(details, sort_keys=True, default=str)
        return hashlib.sha256(payload.encode('utf-8')).hexdigest()

    def _rolling_summary(self, session_id, user_id, recent_rows):
        """Retain a short deterministic crux of turns outside the model window."""
        try:
            summary = db.session.get(AIChatSummary, (user_id, session_id))
            newest_replayed = min((r.id for r in recent_rows), default=0)
            if newest_replayed:
                start_id = summary.through_message_id if summary else 0
                old_rows = (ChatMessage.query.filter_by(user_id=user_id, session_id=session_id)
                    .filter(ChatMessage.role.in_(['user', 'assistant']))
                    .filter(ChatMessage.id > start_id, ChatMessage.id < newest_replayed)
                    .order_by(ChatMessage.id.asc()).limit(200).all())
                if old_rows:
                    lines = [f"{r.role}: {' '.join(_safe_context_text(r.content).split())[:220]}" for r in old_rows]
                    content = ((summary.content + '\n') if summary else '') + '\n'.join(lines)
                    if not summary:
                        summary = AIChatSummary(user_id=user_id, session_id=session_id)
                        db.session.add(summary)
                    summary.content = content[-4000:]
                    summary.through_message_id = old_rows[-1].id
                    summary.updated_at = datetime.utcnow()
                    db.session.commit()
            return summary.content if summary else ''
        except Exception as exc:
            log.warning('[AI] Could not update chat summary: %s', exc)
            db.session.rollback()
            return ''

    def _complete(self, messages):
        """Return the provider's assistant message in its native format."""
        candidates = self._provider_candidates()
        if not candidates:
            raise AIProviderError('No AI provider is configured.')
        last_error = None
        for candidate in candidates:
            if _cooldown_until.get(candidate['id'], 0) > time.monotonic():
                continue
            try:
                if candidate['provider'] == 'ollama':
                    if not self._client:
                        raise AIProviderError('Local AI service is unavailable.')
                    response = self._client.chat(
                        model=candidate['model'], messages=messages, tools=TOOLS,
                        options={'temperature': 0.2, 'num_predict': 1024},
                    )
                    self.provider = 'ollama'
                    return response.message
                response = self._hosted_complete(candidate, messages)
                self.provider = candidate['provider']
                return response
            except AIRateLimitError as exc:
                _cooldown_until[candidate['id']] = time.monotonic() + exc.retry_after
                last_error = exc
            except (_OllamaResponseError, AIProviderError) as exc:
                last_error = exc
                log.warning('[AI] Provider %s failed: %s', candidate['provider'], exc)
        if last_error:
            raise last_error
        raise AIRateLimitError(retry_after=4, message='All configured providers are cooling down.')

    def _hosted_complete(self, candidate, messages):
        """One bounded request; the caller handles provider fallback."""
        provider = candidate['provider']
        try:
            try:
                response = requests.post(
                    f"{candidate['base_url']}/chat/completions",
                    headers={
                        'Authorization': f"Bearer {candidate['api_key']}",
                        'Content-Type': 'application/json',
                    },
                    json={
                        'model': candidate['model'],
                        'messages': messages,
                        'tools': _openai_tools(),
                        'tool_choice': 'auto',
                        'temperature': 0.2,
                        'max_tokens': 1024,
                    },
                    timeout=(4, 25),
                )
            except requests.RequestException as exc:
                raise AIProviderError('The hosted AI service could not be reached.') from exc

            status_code = getattr(response, 'status_code', 200 if getattr(response, 'ok', False) else 500)
            if status_code == 429:
                headers = getattr(response, 'headers', {}) or {}
                retry_header = headers.get('Retry-After') if hasattr(headers, 'get') else None
                pause_time = 4.0
                if retry_header:
                    try:
                        pause_time = float(retry_header)
                    except (ValueError, TypeError):
                        pass
                else:
                    try:
                        if callable(getattr(response, 'json', None)):
                            body_err = response.json().get('error', {}).get('message', '')
                            m = re.search(r"try again in (\d+(?:\.\d+)?)s", body_err)
                            if m:
                                pause_time = float(m.group(1))
                    except Exception:
                        pass

                raise AIRateLimitError(
                    retry_after=pause_time,
                    message=f"{provider} rate limit reached."
                )

            if not getattr(response, 'ok', False):
                if status_code == 401:
                    raise AIProviderError('The hosted AI key is invalid or missing.')
                raise AIProviderError('The hosted AI service is temporarily unavailable.')

            try:
                message = response.json()['choices'][0]['message']
            except (KeyError, IndexError, TypeError, ValueError) as exc:
                raise AIProviderError('The hosted AI returned an invalid response.') from exc
            return message
        except AIRateLimitError:
            raise

    def _tool_calls(self, message):
        return message.get('tool_calls', []) if isinstance(message, dict) else getattr(message, 'tool_calls', [])

    def _tool_call_parts(self, tool_call):
        def parsed_arguments(value):
            if isinstance(value, dict):
                return value
            if isinstance(value, str):
                try:
                    parsed = json.loads(value or '{}')
                    return parsed if isinstance(parsed, dict) else {}
                except (TypeError, ValueError):
                    return {}
            return {}

        if isinstance(tool_call, dict):
            function = tool_call.get('function', {})
            return function.get('name'), parsed_arguments(function.get('arguments')), tool_call.get('id')
        return tool_call.function.name, parsed_arguments(tool_call.function.arguments), None

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

    def _history_rows(self, session_id: str, user_id: int):
        """Read history without allowing a transient DB error to crash chat."""
        try:
            rows = (
                ChatMessage.query
                .filter_by(session_id=session_id, user_id=user_id)
                .filter(ChatMessage.role.in_(["user", "assistant"]))
                .order_by(ChatMessage.created_at.desc())
                .limit(self.context_limit)
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
                "hi": "माफ़ करें, AI सर्वर चालू नहीं है। कृपया Ollama या Groq सेवा जांचें।",
                "gu": "માફ કરો, AI સર્વર ચાલુ નથી. Ollama અથવા Groq સેવા તપાસો.",
                "romanized_hi": "Maaf karo, AI server chal nahi raha. Please Ollama ya Groq service check karo.",
                "romanized_gu": "Maaf karjo, AI server chalu nathi. Ollama athva Groq service check karo.",
            }
            return msgs.get(lang, "⚠️ The AI service is currently offline. Please check connection.")
        if "limit has been reached" in error.lower() or "rate limit" in error.lower():
            return "⏳ Free-tier rate limit reached. Taking a brief pause to cooldown..."
        return "⚠️ The AI service is temporarily unavailable. Loading state will resume once connected."

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
