# AI Assistant / Chatbox Integration — Implementation Plan

## Optical ERP — Everest Optical

> **Status**: Awaiting Review  
> **Date**: 2026-09-16  
> **Scope**: Local LLM-powered conversational assistant with voice input, multilingual support (English, Hindi, Gujarati + romanized Gujarati), full ERP action orchestration

---

## Table of Contents

1. [Executive Summary](#1-executive-summary)
2. [Current System Analysis](#2-current-system-analysis)
3. [Technology Stack Selection](#3-technology-stack-selection)
4. [Architecture Overview](#4-architecture-overview)
5. [Phase 1 — Backend AI Service Layer](#5-phase-1--backend-ai-service-layer)
6. [Phase 2 — Tool Functions (LLM to ERP Actions)](#6-phase-2--tool-functions-llm-to-erp-actions)
7. [Phase 3 — Multilingual and Transliteration Pipeline](#7-phase-3--multilingual-and-transliteration-pipeline)
8. [Phase 4 — Speech-to-Text Integration](#8-phase-4--speech-to-text-integration)
9. [Phase 5 — Frontend Chat UI](#9-phase-5--frontend-chat-ui)
10. [Phase 6 — Conversation Orchestration and Error Handling](#10-phase-6--conversation-orchestration-and-error-handling)
11. [File-by-File Change Manifest](#11-file-by-file-change-manifest)
12. [Test Plan — All Test Cases](#12-test-plan--all-test-cases)
13. [Deployment and Configuration](#13-deployment-and-configuration)
14. [Open Questions for Review](#14-open-questions-for-review)

---

## 1. Executive Summary

This plan integrates a **fully local, privacy-first AI assistant** into the existing Optical ERP Flask application. The assistant will:

- Understand natural language commands in **English**, **Hindi**, and **Gujarati** (including romanized Gujarati like "mera bhai")
- Accept **voice input** via speech-to-text
- Execute **all ERP operations** (create customers, make bills/orders, add inventory, manage users, look up prescriptions, etc.) through a conversational tool-calling loop
- Handle **incomplete information** by asking follow-up questions
- Handle **incorrect/invalid data** by clearly explaining what went wrong and re-prompting
- **Navigate the user** to the correct pages when appropriate
- Store all interactions with **proper audit trails**

### Key Architectural Decisions

| Decision | Choice | Rationale |
|---|---|---|
| **LLM Runtime** | Ollama (local) | Free, no API costs, OpenAI-compatible API, runs on user's machine |
| **LLM Model** | Qwen3.6 (8B or 14B) or Mistral Small 3.1 | Best function-calling accuracy at reasonable hardware requirements; falls back to Llama 3.2 3B for low-spec machines |
| **Speech-to-Text** | OpenAI Whisper (local via `faster-whisper`) + optional Sarvam AI API fallback | Free local; Sarvam for production-grade Indian language accuracy |
| **Transliteration** | `ai4bharat-transliteration` + custom romanized-Indic pipeline | Best-in-class neural transliteration for Gujarati/Hindi romanized text |
| **Agent Framework** | Custom ReAct loop (no LangChain dependency) | Keeps it lightweight; Flask-native; full control over tool orchestration |

---

## 2. Current System Analysis

### 2.1 Application Architecture

- **Framework**: Flask 2.3.3 (Python 3.11)
- **Database**: PostgreSQL (Neon) / SQLite fallback
- **ORM**: SQLAlchemy 2.0 + Flask-Migrate (Alembic)
- **Auth**: Flask-Login + Flask-Bcrypt, Google OAuth
- **Frontend**: Server-side Jinja2 templates + Bootstrap 5.3 + vanilla JS
- **Deployment**: Render (free tier) via Gunicorn

### 2.2 Data Models (8 models)

| Model | Table | Key Fields | Required for Create |
|---|---|---|---|
| `User` | `users` | id, username, email, password_hash, role, google_id, otp, otp_expiry | username, password, role |
| `Customer` | `customers` | id, name, care_of, phone | name, phone |
| `Prescription` | `prescriptions` | id, customer_id, re_sph/cyl/axis, le_sph/cyl/axis, addition, image_path, notes | customer_id |
| `Order` | `orders` | id, order_no, customer_id, prescription_id, status, delivery_mode, issue_date, delivery_date, advance_amount, discount, total_amount, created_by | customer_id, at least 1 line item |
| `OrderItem` | `order_items` | id, order_id, inventory_id, description, quantity, unit_price | order_id, quantity, unit_price |
| `Inventory` | `inventory` | id, model_name, brand, frame_type, quantity, location, shop_branch, cost_price, selling_price, low_stock_threshold, color_stock, image_path | model_name, location, cost_price, selling_price |
| `AuditLog` | `audit_logs` | id, user_id, action, table_name, record_id, field_name, old_value, new_value, timestamp | (auto-generated) |
| `DeletionRequest` | `deletion_requests` | id, entity_type, entity_id, entity_identifier, reason, status, admin_notes, requested_by_id, reviewed_by_id | entity_type, entity_id, reason |
| `DropdownOption` | `dropdown_options` | id, category, value | category, value |

### 2.3 Existing Routes (Endpoints the AI Must Wire Into)

| Blueprint | Route | Method | Purpose |
|---|---|---|---|
| `auth` | `/users/add` | POST | Create user (admin only) |
| `auth` | `/users/edit/<id>` | POST | Edit user (admin only) |
| `auth` | `/users/delete/<id>` | POST | Delete user (admin only) |
| `customer` | `/customers/` | GET | List/search customers |
| `customer` | `/customers/add` | POST | Create customer |
| `customer` | `/customers/edit/<id>` | POST | Edit customer |
| `customer` | `/customers/delete/<id>` | POST | Delete customer (or submit deletion request) |
| `order` | `/orders/` | GET | List orders |
| `order` | `/orders/new/<customer_id>` | POST | Create order |
| `order` | `/orders/<id>` | GET | View order |
| `order` | `/orders/edit/<id>` | POST | Edit order |
| `order` | `/orders/delete/<id>` | POST | Delete order |
| `prescription` | `/prescriptions/add/<customer_id>` | POST | Add prescription |
| `prescription` | `/prescriptions/history/<customer_id>` | GET | View prescription history |
| `prescription` | `/prescriptions/edit/<id>` | POST | Edit prescription |
| `prescription` | `/prescriptions/delete/<id>` | POST | Delete prescription |
| `inventory` | `/inventory/` | GET | List/search inventory |
| `inventory` | `/inventory/add` | POST | Add inventory item |
| `inventory` | `/inventory/edit/<id>` | POST | Edit inventory item |
| `inventory` | `/inventory/delete/<id>` | POST | Delete inventory item |
| `audit` | `/audit/` | GET | View audit logs (admin only) |
| `dashboard` | `/dashboard` | GET | Dashboard with metrics |
| `deletion_request` | `/deletion-requests/` | GET | List deletion requests (admin only) |
| `deletion_request` | `/deletion-requests/<id>/approve` | POST | Approve deletion (admin) |
| `deletion_request` | `/deletion-requests/<id>/reject` | POST | Reject deletion (admin) |

### 2.4 Current Gaps to Address

1. **No JSON API layer** — All routes return HTML templates or redirects. The AI assistant needs programmatic data access.
2. **No WebSocket/streaming** — Chat requires real-time communication.
3. **No AI/NLP dependencies** — No Ollama, Whisper, or transliteration libraries.
4. **No chat UI component** — Must be added to `base.html`.

---

## 3. Technology Stack Selection

### 3.1 Local LLM — Ollama + Qwen3.6

```
Primary:   Ollama + qwen3.6:8b     (8GB VRAM — most machines)
Fallback:  Ollama + llama3.2:3b     (4GB VRAM — low-spec machines)
Premium:   Ollama + qwen3.6:14b     (16GB VRAM — for better accuracy)
```

**Why Qwen3.6?**
- Native function-calling/tool-use support (not prompt-hacked)
- Excellent at structured JSON output
- Multilingual — understands Hindi, Gujarati text natively
- Available through Ollama with a single `ollama pull qwen3.6:8b`

**Why Ollama?**
- Industry standard local LLM runtime
- OpenAI-compatible REST API on `localhost:11434`
- Zero cost, fully offline
- Python SDK: `pip install ollama`

### 3.2 Speech-to-Text — faster-whisper (local) + Sarvam AI (optional)

```
Primary (Local):  faster-whisper with whisper-large-v3
Fallback (API):   Sarvam AI Saaras V3 (Rs 1.5/min, free credits on signup)
Browser-native:   Web Speech API (Chrome/Edge) for zero-setup fallback
```

**Why faster-whisper?**
- 4x faster than original Whisper
- Supports Hindi + Gujarati + English
- Runs on CPU (no GPU required, though GPU recommended)
- Model sizes: `tiny` (75MB) to `large-v3` (3GB)

**Why Sarvam AI as fallback?**
- Specifically optimized for Indian languages and code-mixing (Hinglish/Gujlish)
- Free credits on signup (Rs 1,000)
- REST API — easy integration

### 3.3 Transliteration — ai4bharat + Custom Pipeline

```
Romanized Gujarati -> Gujarati Script -> English Translation
"mera bhai"        -> "mera bhai"      -> understood by LLM natively
```

**Pipeline**:
1. **Language Detection**: Detect if input is English, Hindi, Gujarati, or romanized Indic
2. **Transliteration**: `ai4bharat-transliteration` converts romanized text to native script
3. **Translation to English**: The LLM itself handles translation (Qwen3.6 is multilingual)
4. **All DB entries stored in English**: The LLM translates before executing tool calls

### 3.4 New Python Dependencies

```txt
# AI / LLM
ollama>=0.4.0

# Speech-to-Text
faster-whisper>=1.1.0

# Transliteration
ai4bharat-transliteration>=1.0.0
indic-nlp-library>=0.92

# WebSocket for real-time chat
flask-socketio>=5.3.0
eventlet>=0.36.0

# Optional: Sarvam AI fallback
requests  # already in project via Flask
```

---

## 4. Architecture Overview

```
+------------------------------------------------------------------+
|                        BROWSER (Frontend)                         |
|  +----------------+  +----------------+  +---------------------+ |
|  |  Chat Widget   |  |  Voice Btn     |  | Existing ERP Pages  | |
|  |  (Floating)    |  | (Mic Input)    |  | (Customers, Orders) | |
|  +-------+--------+  +-------+--------+  +---------------------+ |
|          | WebSocket          | MediaRecorder API                 |
|          | (flask-socketio)   | -> POST /api/ai/transcribe        |
+----------+--------------------+-----------------------------------+
           |                    |
           v                    v
+------------------------------------------------------------------+
|                    FLASK BACKEND (Python)                          |
|                                                                    |
|  +--------------------------------------------------------------+ |
|  |                    AI Service Layer                            | |
|  |  +--------------+  +--------------+  +-------------------+   | |
|  |  |  Language     |  | Transliter.  |  |  Conversation     |   | |
|  |  |  Detector     |->|  Pipeline    |->|  Manager          |   | |
|  |  +--------------+  +--------------+  +--------+----------+   | |
|  |                                               |               | |
|  |  +--------------+  +--------------+  +--------v----------+   | |
|  |  |  STT Service  |  |  Ollama      |  |  ReAct Agent      |   | |
|  |  |  (Whisper)    |  |  Client      |<>|  Loop             |   | |
|  |  +--------------+  +--------------+  +--------+----------+   | |
|  |                                               |               | |
|  |                                    +----------v------------+  | |
|  |                                    |   TOOL FUNCTIONS       |  | |
|  |                                    |   (14 ERP Actions)     |  | |
|  |                                    +----------+------------+  | |
|  +-----------------------------------------------+--------------+ |
|                                                   |                |
|  +------------------------------------------------v--------------+|
|  |                  Existing ERP Layer                            ||
|  |  Models: User, Customer, Order, Prescription, Inventory...    ||
|  |  Database: PostgreSQL (Neon) / SQLite                         ||
|  +---------------------------------------------------------------+|
+------------------------------------------------------------------+
           |
           v
+---------------------+
|     OLLAMA           |
|  (localhost:11434)   |
|  Model: qwen3.6:8b  |
+---------------------+
```

---

## 5. Phase 1 — Backend AI Service Layer

### 5.1 New Files to Create

#### `backend/services/ai_service.py` — Core AI orchestrator

Responsibilities:
- Initialize Ollama client
- Manage conversation history per session
- Run the ReAct (Reason -> Act -> Observe) loop
- Handle tool call execution and response formatting
- Translate multilingual input to English for DB operations

```python
# Pseudo-code structure
class AIAssistant:
    def __init__(self, model='qwen3.6:8b'):
        self.model = model
        self.ollama_client = ollama
        self.tools = [...]  # All ERP tool functions
        self.system_prompt = SYSTEM_PROMPT  # Defined below
    
    def chat(self, user_message, session_id, user_context):
        """
        Main entry point. Handles:
        1. Language detection + transliteration
        2. Message -> Ollama with tools
        3. ReAct loop (up to MAX_ITERATIONS)
        4. Returns: { text, action, navigate_to, data }
        """
    
    def _react_loop(self, messages, max_iterations=5):
        """
        Loop:
        1. Send messages to Ollama
        2. If response has tool_calls -> execute them
        3. Append tool results as 'tool' role messages
        4. Repeat until model gives final text response
        """
    
    def _execute_tool(self, tool_name, arguments):
        """
        Dispatch to the appropriate tool function.
        Wrap in try/except for graceful error handling.
        Returns: { success: bool, data: any, error: str }
        """
```

#### System Prompt (Critical for Behavior)

```
You are the Optical ERP Assistant for Everest Optical shop. You help staff manage
customers, orders, prescriptions, inventory, and users through natural conversation.

RULES:
1. When the user wants to perform an action, use the available tools.
2. If information is missing, ASK for it before proceeding. Never guess.
3. For customer operations, always search first to avoid duplicates.
4. For orders, a customer must exist first. If not found, offer to create one.
5. All data you write to the database MUST be in English.
6. If the user speaks in Hindi, Gujarati, or romanized Indic text, understand
   their intent but respond in the same language they used for conversation.
   All data fields (names, descriptions) must be in English.
7. For navigation requests ("show me orders", "go to dashboard"), return a
   navigation action instead of executing a tool.
8. Always confirm destructive actions (delete) before executing.
9. When creating orders, calculate totals correctly.
10. For prescriptions, validate SPH/CYL/AXIS values are within optical ranges.

NAVIGATION MAP:
- Dashboard: /dashboard
- Customers List: /customers/
- Add Customer: /customers/add
- Edit Customer: /customers/edit/{id}
- Orders List: /orders/
- New Order: /orders/new/{customer_id}
- View Order: /orders/{id}
- Inventory: /inventory/
- Add Inventory: /inventory/add
- Prescriptions: /prescriptions/history/{customer_id}
- Manage Users: /users (admin only)
- Audit Logs: /audit/ (admin only)
```

#### `backend/services/language_service.py` — Language Detection + Transliteration

```python
class LanguageService:
    def detect_language(self, text: str) -> str:
        """Returns: 'en', 'hi', 'gu', 'romanized_hi', 'romanized_gu'"""
    
    def transliterate_to_english(self, text: str, source_lang: str) -> str:
        """Converts Hindi/Gujarati/romanized to English for DB storage"""
    
    def transliterate_romanized_to_native(self, text: str, target_lang: str) -> str:
        """Converts 'kem cho' to native Gujarati script for display purposes"""
```

#### `backend/services/stt_service.py` — Speech-to-Text

```python
class STTService:
    def __init__(self, backend='whisper_local'):
        # backend: 'whisper_local', 'sarvam_api', 'browser_native'
    
    def transcribe(self, audio_data: bytes, language_hint: str = None) -> dict:
        """
        Returns: {
            text: str,
            language: str,
            confidence: float,
            segments: list  # word-level timestamps
        }
        """
    
    def _transcribe_whisper(self, audio_data, language_hint):
        """Use faster-whisper locally"""
    
    def _transcribe_sarvam(self, audio_data, language_hint):
        """Fallback to Sarvam AI API"""
```

### 5.2 New Routes — `backend/routes/ai_routes.py`

```python
ai_bp = Blueprint('ai', __name__, url_prefix='/api/ai')

@ai_bp.route('/chat', methods=['POST'])           # Text chat endpoint
@ai_bp.route('/transcribe', methods=['POST'])      # Audio -> text
@ai_bp.route('/health', methods=['GET'])           # LLM + STT status check
@ai_bp.route('/config', methods=['GET'])           # Get current AI config
@ai_bp.route('/config', methods=['POST'])          # Update AI config (admin)
```

### 5.3 New Model — `backend/models/chat_history.py`

```python
class ChatMessage(db.Model):
    __tablename__ = 'chat_messages'
    
    id = db.Column(db.Integer, primary_key=True)
    session_id = db.Column(db.String(50), nullable=False, index=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    role = db.Column(db.String(20), nullable=False)  # 'user', 'assistant', 'tool'
    content = db.Column(db.Text, nullable=False)
    original_language = db.Column(db.String(10))      # 'en', 'hi', 'gu', etc.
    original_text = db.Column(db.Text)                 # Pre-translation text
    tool_name = db.Column(db.String(50))               # If role='tool'
    tool_args = db.Column(db.Text)                     # JSON
    tool_result = db.Column(db.Text)                   # JSON
    action_type = db.Column(db.String(30))             # 'navigate', 'create', 'query', etc.
    created_at = db.Column(db.DateTime, server_default=db.func.now())
```

---

## 6. Phase 2 — Tool Functions (LLM to ERP Actions)

### 6.1 Complete Tool Registry — `backend/services/ai_tools.py`

Each tool function operates **directly on the database** using SQLAlchemy models, bypassing the HTML form routes. This is necessary because the LLM needs JSON responses, not HTML redirects.

#### Tool 1: `search_customers`

```python
def search_customers(query: str, limit: int = 10) -> str:
    """
    Search for customers by name, phone number, or care_of field.
    Use this before creating a customer to check for duplicates.
    
    Args:
        query: Search term (name, phone, or care_of)
        limit: Maximum results to return (default 10)
    
    Returns: JSON list of matching customers with id, name, phone, care_of
    """
```

#### Tool 2: `create_customer`

```python
def create_customer(name: str, phone: str, care_of: str = None) -> str:
    """
    Create a new customer in the system.
    ALWAYS search for existing customers first to avoid duplicates.
    
    Args:
        name: Full name of the customer (required, must be in English)
        phone: Phone number (required, 10-digit Indian mobile or landline)
        care_of: Care of / parent / guardian name (optional)
    
    Returns: JSON with created customer details including ID
    """
```

#### Tool 3: `get_customer_details`

```python
def get_customer_details(customer_id: int) -> str:
    """
    Get complete details of a customer including their orders and prescriptions.
    
    Args:
        customer_id: The numeric ID of the customer
    
    Returns: JSON with customer details, recent orders, and prescriptions
    """
```

#### Tool 4: `edit_customer`

```python
def edit_customer(customer_id: int, name: str = None, phone: str = None, care_of: str = None) -> str:
    """
    Update an existing customer's details. Only provided fields will be updated.
    
    Args:
        customer_id: The numeric ID of the customer to update
        name: New name (optional)
        phone: New phone number (optional)
        care_of: New care_of value (optional)
    
    Returns: JSON with updated customer details
    """
```

#### Tool 5: `create_order`

```python
def create_order(
    customer_id: int,
    items: list,
    prescription_id: int = None,
    delivery_date: str = None,
    advance_amount: float = 0.0,
    discount: float = 0.0,
    status: str = "Pending",
    delivery_mode: str = "Self"
) -> str:
    """
    Create a new order/bill for a customer.
    Each item in the items list must have: description, quantity, unit_price.
    
    Args:
        customer_id: Customer ID (required — search/create customer first)
        items: List of dicts, each with 'description' (str), 'quantity' (int), 'unit_price' (float)
        prescription_id: Link to a prescription (optional)
        delivery_date: Expected delivery date in YYYY-MM-DD format (optional)
        advance_amount: Advance payment received (default 0.0)
        discount: Discount amount (default 0.0)
        status: Order status — 'Pending', 'Ready', 'Delivered' (default 'Pending')
        delivery_mode: 'Self', 'Courier', 'Home' (default 'Self')
    
    Returns: JSON with order details including order_no, total, balance
    """
```

#### Tool 6: `search_orders`

```python
def search_orders(
    customer_name: str = None,
    order_no: str = None,
    status: str = None,
    limit: int = 10
) -> str:
    """
    Search orders by customer name, order number, or status.
    
    Args:
        customer_name: Search by customer name (partial match)
        order_no: Search by order number (exact or partial match)
        status: Filter by status — 'Pending', 'Ready', 'Delivered'
        limit: Maximum results (default 10)
    
    Returns: JSON list of matching orders
    """
```

#### Tool 7: `update_order_status`

```python
def update_order_status(order_id: int, status: str) -> str:
    """
    Update the status of an existing order.
    
    Args:
        order_id: The numeric ID of the order
        status: New status — must be one of: 'Pending', 'Ready', 'Delivered'
    
    Returns: JSON with updated order details
    """
```

#### Tool 8: `add_prescription`

```python
def add_prescription(
    customer_id: int,
    re_sph: float = None, re_cyl: float = None, re_axis: int = None,
    le_sph: float = None, le_cyl: float = None, le_axis: int = None,
    addition: float = None,
    notes: str = None
) -> str:
    """
    Add a new eye prescription for a customer.
    SPH values typically range from -20.00 to +20.00.
    CYL values typically range from -10.00 to +10.00.
    AXIS values range from 0 to 180 degrees.
    Addition values range from +0.50 to +4.00.
    
    Args:
        customer_id: Customer ID (required)
        re_sph: Right eye sphere power
        re_cyl: Right eye cylinder power
        re_axis: Right eye axis (0-180 degrees)
        le_sph: Left eye sphere power
        le_cyl: Left eye cylinder power
        le_axis: Left eye axis (0-180 degrees)
        addition: Near vision addition power
        notes: Additional notes
    
    Returns: JSON with prescription details
    """
```

#### Tool 9: `search_inventory`

```python
def search_inventory(
    query: str = None,
    low_stock_only: bool = False,
    limit: int = 15
) -> str:
    """
    Search inventory items by model name, brand, or frame type.
    Can also filter to show only low-stock items.
    
    Args:
        query: Search term (model name, brand, or frame type)
        low_stock_only: If true, only returns items below their low stock threshold
        limit: Maximum results (default 15)
    
    Returns: JSON list of inventory items with stock info
    """
```

#### Tool 10: `add_inventory_item`

```python
def add_inventory_item(
    model_name: str,
    location: str,
    cost_price: float,
    selling_price: float,
    brand: str = None,
    frame_type: str = None,
    quantity: int = 0,
    shop_branch: str = None,
    low_stock_threshold: int = 5,
    color_stock: str = None
) -> str:
    """
    Add a new item to the inventory.
    
    Args:
        model_name: Product model name (required)
        location: Storage location — rack/drawer/shelf (required)
        cost_price: Purchase/cost price (required)
        selling_price: Selling price (required)
        brand: Brand name (optional)
        frame_type: Type of frame (optional)
        quantity: Initial stock quantity (default 0)
        shop_branch: Shop branch location (optional)
        low_stock_threshold: Alert threshold (default 5)
        color_stock: Color-wise stock, e.g. 'black-10, white-15' (optional)
    
    Returns: JSON with created inventory item details
    """
```

#### Tool 11: `update_inventory_stock`

```python
def update_inventory_stock(inventory_id: int, quantity_change: int, reason: str = None) -> str:
    """
    Increase or decrease inventory stock quantity.
    Use positive numbers to add stock, negative to reduce.
    
    Args:
        inventory_id: The inventory item ID
        quantity_change: Amount to add (positive) or remove (negative)
        reason: Reason for the stock change (optional)
    
    Returns: JSON with updated inventory details including new quantity
    """
```

#### Tool 12: `get_dashboard_stats`

```python
def get_dashboard_stats() -> str:
    """
    Get current dashboard statistics including total customers, orders,
    pending orders, low stock items, and total revenue.
    
    Returns: JSON with all dashboard metrics
    """
```

#### Tool 13: `create_user` (Admin only)

```python
def create_user(username: str, password: str, role: str = "staff", email: str = None) -> str:
    """
    Create a new system user. Only admins can perform this action.
    
    Args:
        username: Unique username (required)
        password: Password (minimum 6 characters, required)
        role: User role — 'admin' or 'staff' (default 'staff')
        email: Email address (optional)
    
    Returns: JSON with created user details (password not included)
    """
```

#### Tool 14: `navigate_to_page`

```python
def navigate_to_page(page_name: str, entity_id: int = None) -> str:
    """
    Navigate the user to a specific page in the ERP system.
    Use this when the user wants to GO TO or SEE a specific page.
    
    Args:
        page_name: One of: 'dashboard', 'customers', 'add_customer',
                   'edit_customer', 'orders', 'new_order', 'view_order',
                   'edit_order', 'inventory', 'add_inventory', 'edit_inventory',
                   'prescriptions', 'add_prescription', 'users', 'audit_logs',
                   'deletion_requests'
        entity_id: Required for pages that need an ID (edit_customer, view_order, etc.)
    
    Returns: JSON with the URL to navigate to
    """
```

### 6.2 Tool Validation Layer

Each tool will include validation before execution:

```python
# Example validation in create_customer
def create_customer(name, phone, care_of=None):
    errors = []
    
    # Validate name
    if not name or len(name.strip()) < 2:
        errors.append("Customer name must be at least 2 characters")
    
    # Validate phone
    phone_clean = re.sub(r'[^\d+]', '', phone)
    if not re.match(r'^(\+91)?[6-9]\d{9}$', phone_clean):
        errors.append("Phone must be a valid 10-digit Indian mobile number")
    
    # Check duplicates
    existing = Customer.query.filter(
        (Customer.phone == phone_clean) | 
        (Customer.name.ilike(f'%{name.strip()}%'))
    ).all()
    
    if existing:
        return json.dumps({
            "success": False,
            "error": "Possible duplicate customers found",
            "duplicates": [{"id": c.id, "name": c.name, "phone": c.phone} for c in existing],
            "message": "Similar customers exist. Do you still want to create a new one?"
        })
    
    if errors:
        return json.dumps({"success": False, "errors": errors})
    
    # ... proceed with creation
```

---

## 7. Phase 3 — Multilingual and Transliteration Pipeline

### 7.1 Language Detection Strategy

```
Input: "mera bhai ka order dikhao"
  |
  v
Step 1: Script detection
  - All Latin characters -> romanized Indic OR English
  - Devanagari chars -> Hindi
  - Gujarati chars -> Gujarati
  |
  v
Step 2: If Latin, run language identification
  - Use word-level detection
  - "mera" -> likely Hindi/Gujarati
  - "order" -> English
  - "dikhao" -> likely Hindi
  - Result: "romanized_hi" (Hindi in Roman script)
  |
  v
Step 3: Context-aware interpretation
  - The LLM (Qwen3.6) natively understands romanized Hindi/Gujarati
  - System prompt instructs: "User may speak in romanized Hindi/Gujarati"
  - LLM extracts intent: "Show my brother's order"
  |
  v
Step 4: Tool execution
  - search_orders(customer_name="<brother's name>") if context available
  - OR ask: "What is your brother's name so I can look up their orders?"
```

### 7.2 Romanized Gujarati ("Gujlish") Handling

Key challenge: Gujarati words spelled in English characters.

**Examples the system must handle:**

| Romanized Input | Meaning | Action |
|---|---|---|
| "chasma nu bill banavo" | "Make a glasses bill" | Create order |
| "navu customer umarao" | "Add new customer" | Create customer |
| "stock maan su che?" | "What's in stock?" | Search inventory |
| "aankh no number nakhvo" | "Enter eye number" | Add prescription |
| "aaje ketla order aavya?" | "How many orders today?" | Dashboard stats |
| "Rajesh bhai no order" | "Rajesh bhai's order" | Search orders (customer=Rajesh) |

**Implementation**: The LLM itself (Qwen3.6) is multilingual and can understand romanized Hindi/Gujarati. The system prompt will include examples of common optical shop terminology in all three formats.

### 7.3 Optical Domain Glossary

A glossary file will be created at `backend/services/ai_glossary.py`:

```python
OPTICAL_GLOSSARY = {
    # Gujarati romanized -> English
    "chasma": "glasses/spectacles",
    "frame": "frame",
    "number": "prescription/power",
    "lens": "lens",
    "goggles": "sunglasses",
    "bill": "order/invoice",
    "advance": "advance payment",
    "bakshi": "discount",
    "grahak": "customer",
    "dukaan": "shop",
    
    # Hindi romanized -> English
    "chashma": "glasses/spectacles",
    "nazar": "vision/prescription",
    "paisa": "money/payment",
    "order": "order",
    "customer": "customer",
    "stock": "inventory/stock",
}
```

---

## 8. Phase 4 — Speech-to-Text Integration

### 8.1 Audio Capture (Frontend)

Using the browser's **MediaRecorder API**:

```javascript
// In chat widget
class VoiceRecorder {
    constructor() {
        this.mediaRecorder = null;
        this.audioChunks = [];
    }
    
    async startRecording() {
        const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
        this.mediaRecorder = new MediaRecorder(stream, { mimeType: 'audio/webm' });
        this.audioChunks = [];
        
        this.mediaRecorder.ondataavailable = (e) => this.audioChunks.push(e.data);
        this.mediaRecorder.start();
    }
    
    stopRecording() {
        return new Promise(resolve => {
            this.mediaRecorder.onstop = () => {
                const blob = new Blob(this.audioChunks, { type: 'audio/webm' });
                resolve(blob);
            };
            this.mediaRecorder.stop();
        });
    }
}
```

### 8.2 Server-Side Processing

```
Browser -> POST /api/ai/transcribe (audio blob) -> Server
Server -> faster-whisper (language auto-detect) -> text + language
Server -> AI chat pipeline -> response
```

### 8.3 Whisper Model Configuration

```python
# backend/services/stt_service.py
from faster_whisper import WhisperModel

class STTService:
    def __init__(self):
        # Model sizes: tiny, base, small, medium, large-v3
        # Recommendation: 'small' for CPU, 'medium' for GPU
        self.model = WhisperModel(
            "small",          # or "medium" for better accuracy
            device="cpu",     # or "cuda" if GPU available
            compute_type="int8"  # quantized for speed
        )
    
    def transcribe(self, audio_path, language_hint=None):
        segments, info = self.model.transcribe(
            audio_path,
            language=language_hint,  # None = auto-detect
            beam_size=5,
            vad_filter=True,  # Remove silence
        )
        
        text = " ".join([s.text for s in segments])
        return {
            "text": text.strip(),
            "language": info.language,
            "confidence": info.language_probability,
        }
```

### 8.4 Browser Speech API Fallback

For zero-setup on Chrome/Edge, use the built-in Web Speech API:

```javascript
// Fallback when Whisper server is unavailable
const recognition = new (window.SpeechRecognition || window.webkitSpeechRecognition)();
recognition.lang = 'hi-IN'; // or 'gu-IN', 'en-IN'
recognition.continuous = false;
recognition.interimResults = true;
```

### 8.5 STT Configuration

```python
# AI Configuration stored in environment / DB
# AI_STT_BACKEND = 'whisper_local'   -> 'whisper_local', 'sarvam_api', 'browser'
# AI_STT_MODEL_SIZE = 'small'        -> 'tiny', 'base', 'small', 'medium', 'large-v3'
# AI_LLM_MODEL = 'qwen3.6:8b'       -> Ollama model name
# AI_SARVAM_API_KEY = None           -> Optional
# AI_DEFAULT_LANGUAGE = 'en'         -> 'en', 'hi', 'gu'
```

---

## 9. Phase 5 — Frontend Chat UI

### 9.1 Chat Widget Component

A floating chat widget will be injected into `base.html` via a Jinja2 include. It appears on every authenticated page.

**File**: `backend/templates/components/chat_widget.html`

**Features**:
- Floating action button (bottom-right corner)
- Expandable chat window (400px wide x 550px tall)
- Message bubbles (user / assistant)
- Voice input button (microphone icon)
- Language selector (EN / HI / GU)
- Typing indicator (dots animation)
- Navigation action buttons (when assistant suggests a page)
- Minimized state shows unread count badge
- Draggable on mobile
- Dark mode support (inherits Bootstrap theme)

### 9.2 Chat Widget Layout

```
+----------------------------------+
|  AI Optical ERP Assistant   v  x |  <- Header (collapse/close)
+----------------------------------+
|                                  |
|  +----------------------------+  |
|  | Welcome! I can help with:  |  |  <- System greeting
|  | - Create customers/orders  |  |
|  | - Check inventory/stock    |  |
|  | - Look up prescriptions    |  |
|  | - Navigate the app         |  |
|  +----------------------------+  |
|                                  |
|         +-------------------+    |
|         |  Create a new     |    |  <- User message
|         |  bill for Ramesh  |    |
|         +-------------------+    |
|                                  |
|  +----------------------------+  |
|  | I found a customer named   |  |  <- Assistant response
|  | "Ramesh Patel" (Phone:     |  |
|  | 9876543210). Is this the   |  |
|  | right customer?            |  |
|  |                            |  |
|  | [Yes, proceed] [No, other] |  |  <- Quick action buttons
|  +----------------------------+  |
|                                  |
+----------------------------------+
| [EN v] [Type a message...      ] |  <- Input area
|        [mic]            [Send]   |  <- Mic + Send buttons
+----------------------------------+
```

### 9.3 JavaScript — `backend/static/js/chat_widget.js`

Core responsibilities:
- WebSocket connection to Flask-SocketIO
- Message send/receive
- Voice recording + upload
- Navigation action handling (window.location redirect)
- Quick-reply button rendering
- Conversation history display
- Auto-scroll behavior
- Error state handling

### 9.4 CSS — `backend/static/css/chat_widget.css`

- Position: fixed, bottom-right
- Z-index: 9999 (above all ERP content)
- Glassmorphism styling (matches modern design)
- Responsive: full-width on mobile
- Animations: slide-up open, message appear, typing dots
- Dark mode variants

---

## 10. Phase 6 — Conversation Orchestration and Error Handling

### 10.1 Conversation State Machine

```
                    +--------------+
                    |    IDLE       |
                    |  (Waiting)    |
                    +------+-------+
                           | User sends message
                           v
                    +--------------+
                    |  PROCESSING   |
                    |  (LLM call)   |
                    +------+-------+
                           |
              +------------+------------+
              |            |            |
              v            v            v
       +------------+ +----------+ +--------------+
       | TOOL_CALL   | | RESPONSE | | NAVIGATION   |
       | (Execute    | | (Text    | | (Redirect    |
       |  action)    | |  reply)  | |  user)       |
       +------+-----+ +----------+ +--------------+
              |
              v
       +------------+
       | CONFIRMING  |  <- "Are you sure?" for destructive actions
       |             |  <- "Missing info" for incomplete data
       +------+-----+
              | User confirms / provides info
              v
       +------------+
       | EXECUTING   |
       | (DB write)  |
       +------+-----+
              |
              v
       +------------+
       | COMPLETED   |  -> Show success + offer next action
       +------------+
```

### 10.2 Error Handling Matrix

| Scenario | System Behavior |
|---|---|
| **Ollama not running** | Show "AI assistant offline. Start Ollama to enable." Allow manual navigation. |
| **Model not downloaded** | Show "Downloading AI model... This takes a few minutes the first time." |
| **LLM timeout (>30s)** | Retry once. If still fails: "I'm taking too long. Try a simpler request." |
| **LLM returns invalid tool call** | Log error, tell user "I misunderstood. Could you rephrase?" |
| **Tool function throws exception** | Catch, log, return friendly error. "Something went wrong creating that order. Error: [detail]" |
| **User provides half information** | LLM asks for missing fields. "I need the customer's phone number to create their profile." |
| **User provides wrong data type** | Validation catches it. "Phone number should be a 10-digit number. You entered 'abc123'." |
| **User requests admin action as staff** | Check `current_user.role`. "Sorry, only admins can manage users." |
| **Duplicate entity detected** | Show existing records. "I found a customer 'Ramesh Patel' with phone 9876543210. Is this the same person?" |
| **Prescription values out of range** | "SPH value -25.00 seems unusual. Typical range is -20 to +20. Are you sure?" |
| **Whisper fails to transcribe** | "I couldn't understand the audio clearly. Please try again or type your message." |
| **Unknown language detected** | Fall back to English processing. "I'll try to help — could you repeat in English if I misunderstood?" |
| **Order with no items** | "An order needs at least one item. What items should I add?" |
| **Order total calculation error** | Recalculate server-side. Never trust LLM math for totals. |
| **Network error (Sarvam API)** | Fall back to local Whisper. Log the failure. |
| **Database connection error** | Use existing retry logic from `app.py`. Show "Server reconnecting..." |
| **Concurrent modification** | Use SQLAlchemy optimistic locking where critical. |
| **Session expired during chat** | Redirect to login. Preserve last message for after re-auth. |

### 10.3 Multi-Turn Conversation Examples

#### Example 1: Creating an Order (Happy Path)

```
User: "I want to make a bill"
AI:   "Sure! I'll help you create a new order. Which customer is this for?"
User: "Ramesh bhai"
AI:   "I found 2 customers matching 'Ramesh':
       1. Ramesh Patel (Phone: 9876543210)
       2. Ramesh Kumar (Phone: 9123456789)
       Which one?"
User: "The first one"
AI:   "Great! What items should I add to Ramesh Patel's order?"
User: "One Ray-Ban frame, Rs 2500, and lenses Rs 1500"
AI:   "Here's your order summary:
       Customer: Ramesh Patel
       Items:
       1. Ray-Ban Frame x 1 — Rs 2,500.00
       2. Lenses x 1 — Rs 1,500.00
       Subtotal: Rs 4,000.00
       
       Any advance payment or discount?"
User: "500 advance"
AI:   "Order created!
       Order #ORD-20260916-A3F2B1
       Total: Rs 4,000.00 | Advance: Rs 500.00 | Balance: Rs 3,500.00
       
       [View Order] [Create Another]"
```

#### Example 2: Handling Missing Information

```
User: "add customer"
AI:   "I'd be happy to add a new customer. I need the following:
       - Name (required)
       - Phone number (required)
       - Care of / Guardian (optional)
       
       What is the customer's name?"
User: "Suresh"
AI:   "Got it — Suresh. What's their phone number?"
User: "98765"
AI:   "That phone number looks incomplete. Indian mobile numbers should be 
       10 digits starting with 6-9. Could you provide the full number?"
User: "9876543210"
AI:   "Customer created!
       Name: Suresh | Phone: 9876543210
       [View Customer] [Create Order for Suresh]"
```

#### Example 3: Wrong Information / Error Handling

```
User: "Add prescription for customer 99999"
AI:   "I couldn't find a customer with ID 99999. Would you like to:
       - Search for the customer by name?
       - Create a new customer first?"
User: "Search for Mahesh"
AI:   "Found: Mahesh Shah (ID: 42, Phone: 9988776655)
       Would you like to add a prescription for this customer?"
User: "Yes, right eye SPH -50"
AI:   "Warning: SPH value of -50.00 is outside the typical range (-20.00 to +20.00).
       This might be a typo. Did you mean -5.00?"
User: "Yes, -5.00"
AI:   "Got it. What about the right eye CYL and AXIS? And the left eye values?"
```

#### Example 4: Gujarati Romanized Input

```
User: "navu customer umarao, naam Jayesh, phone 9898989898"
AI:   "Customer created!
       Name: Jayesh | Phone: 9898989898
       [View Customer] [Create Order]"
       
       (Response adapted to user's language style,
        but DB entry is in English)
```

#### Example 5: Navigation Request

```
User: "show me inventory"
AI:   "Taking you to the Inventory page.
       [-> Open Inventory]"
       
       (Client receives navigate action, redirects to /inventory/)
```

---

## 11. File-by-File Change Manifest

### New Files (10 files)

| # | File Path | Purpose |
|---|---|---|
| 1 | `backend/services/ai_service.py` | Core AI assistant — ReAct loop, conversation management |
| 2 | `backend/services/ai_tools.py` | All 14 tool functions for ERP operations |
| 3 | `backend/services/ai_glossary.py` | Optical domain glossary in EN/HI/GU |
| 4 | `backend/services/language_service.py` | Language detection + transliteration pipeline |
| 5 | `backend/services/stt_service.py` | Speech-to-text (Whisper local + Sarvam fallback) |
| 6 | `backend/routes/ai_routes.py` | API endpoints for chat, transcribe, health, config |
| 7 | `backend/models/chat_history.py` | ChatMessage model for conversation persistence |
| 8 | `backend/templates/components/chat_widget.html` | Chat widget HTML template (included in base.html) |
| 9 | `backend/static/js/chat_widget.js` | Chat widget frontend logic (WebSocket, voice, UI) |
| 10 | `backend/static/css/chat_widget.css` | Chat widget styling (dark mode, responsive, animations) |

### Modified Files (5 files)

| # | File Path | Changes |
|---|---|---|
| 1 | `backend/app.py` | Register `ai_bp` blueprint, init SocketIO, import ChatMessage model |
| 2 | `backend/templates/base.html` | Include chat_widget.html partial, add SocketIO JS CDN |
| 3 | `backend/config.py` | Add AI configuration variables (model, STT backend, API keys) |
| 4 | `backend/extensions.py` | Add SocketIO extension instance |
| 5 | `backend/requirements.txt` / `requirements.txt` | Add new dependencies |

### Database Migration

- New table: `chat_messages`
- Run: `flask db migrate -m "Add chat_messages table"` then `flask db upgrade`

---

## 12. Test Plan — All Test Cases

### 12.1 Unit Tests — AI Service Layer

| TC# | Test Case | Input | Expected Output | Category |
|---|---|---|---|---|
| TC-001 | AI service initializes with default model | No args | `AIAssistant.model == 'qwen3.6:8b'` | Setup |
| TC-002 | AI service handles Ollama not running | Chat message | Graceful error: "AI offline" | Error |
| TC-003 | AI service handles model not found | Chat message (no model pulled) | Error with download instructions | Error |
| TC-004 | System prompt is included in first message | Any chat | Messages[0].role == 'system' | Core |
| TC-005 | Conversation history maintained per session | 3 messages, same session_id | All 3 in history | Core |
| TC-006 | Conversation history isolated between sessions | Same msg, different session_ids | Separate histories | Core |
| TC-007 | ReAct loop terminates after MAX_ITERATIONS | Tool that always calls more tools | Stops at 5, returns error | Safety |
| TC-008 | ReAct loop handles single tool call | "what's the customer count?" | get_dashboard_stats -> result | Core |
| TC-009 | ReAct loop handles chained tool calls | "create order for Ramesh" | search_customers -> create_order | Core |
| TC-010 | ChatMessage saved to DB after each exchange | Any chat | Row in chat_messages table | Persistence |

### 12.2 Unit Tests — Tool Functions

| TC# | Test Case | Input | Expected Output | Category |
|---|---|---|---|---|
| TC-011 | `search_customers` — match by name | query="Ramesh" | JSON list with matching customers | Happy |
| TC-012 | `search_customers` — match by phone | query="9876543210" | JSON list with matching customers | Happy |
| TC-013 | `search_customers` — no results | query="zzzznonexistent" | Empty list, success=true | Edge |
| TC-014 | `search_customers` — partial name match | query="Ram" | Matches "Ramesh", "Raman", etc. | Happy |
| TC-015 | `create_customer` — all fields valid | name="Jayesh", phone="9898989898" | Success, customer ID returned | Happy |
| TC-016 | `create_customer` — missing name | name="", phone="9898989898" | Error: "name required" | Validation |
| TC-017 | `create_customer` — missing phone | name="Jayesh", phone="" | Error: "phone required" | Validation |
| TC-018 | `create_customer` — invalid phone (short) | phone="12345" | Error: "invalid phone" | Validation |
| TC-019 | `create_customer` — invalid phone (letters) | phone="abcdefghij" | Error: "invalid phone" | Validation |
| TC-020 | `create_customer` — duplicate phone | Existing phone | Warning: "duplicate found" + existing data | Validation |
| TC-021 | `create_customer` — duplicate name (similar) | Existing name | Warning: "similar customer exists" | Validation |
| TC-022 | `create_customer` — phone with +91 prefix | phone="+919876543210" | Normalized, created successfully | Edge |
| TC-023 | `create_customer` — phone with spaces | phone="98765 43210" | Normalized, created successfully | Edge |
| TC-024 | `get_customer_details` — valid ID | customer_id=1 | Full customer data with orders/prescriptions | Happy |
| TC-025 | `get_customer_details` — invalid ID | customer_id=99999 | Error: "customer not found" | Error |
| TC-026 | `edit_customer` — update name only | customer_id=1, name="New Name" | Name updated, others unchanged | Happy |
| TC-027 | `edit_customer` — no customer found | customer_id=99999 | Error: "customer not found" | Error |
| TC-028 | `create_order` — valid order, 1 item | customer_id=1, items=[{desc, qty, price}] | Order created, order_no returned | Happy |
| TC-029 | `create_order` — valid order, 3 items | customer_id=1, items=[3 items] | Correct total calculated | Happy |
| TC-030 | `create_order` — with discount | discount=100 | total = subtotal - 100 | Happy |
| TC-031 | `create_order` — with advance | advance=500 | balance = total - 500 | Happy |
| TC-032 | `create_order` — no items | items=[] | Error: "at least 1 item required" | Validation |
| TC-033 | `create_order` — invalid customer_id | customer_id=99999 | Error: "customer not found" | Validation |
| TC-034 | `create_order` — negative price | unit_price=-100 | Error: "price must be positive" | Validation |
| TC-035 | `create_order` — zero quantity | quantity=0 | Error: "quantity must be >= 1" | Validation |
| TC-036 | `create_order` — discount > subtotal | discount=10000, subtotal=5000 | total = 0, warning shown | Edge |
| TC-037 | `create_order` — total calculation accuracy | items with decimals | Exact decimal match (no floating point errors) | Accuracy |
| TC-038 | `search_orders` — by customer name | customer_name="Ramesh" | Orders for matching customers | Happy |
| TC-039 | `search_orders` — by order number | order_no="ORD-2026" | Matching orders | Happy |
| TC-040 | `search_orders` — by status | status="Pending" | Only pending orders | Happy |
| TC-041 | `search_orders` — no results | customer_name="zzz" | Empty list | Edge |
| TC-042 | `update_order_status` — valid transition | Pending -> Ready | Status updated | Happy |
| TC-043 | `update_order_status` — invalid status | status="InvalidValue" | Error: "invalid status" | Validation |
| TC-044 | `update_order_status` — order not found | order_id=99999 | Error: "order not found" | Error |
| TC-045 | `add_prescription` — all values valid | re_sph=-2.5, re_cyl=-1.0, re_axis=90 | Prescription created | Happy |
| TC-046 | `add_prescription` — SPH out of range | re_sph=-25.0 | Warning: "unusual value" | Validation |
| TC-047 | `add_prescription` — CYL out of range | re_cyl=-15.0 | Warning: "unusual value" | Validation |
| TC-048 | `add_prescription` — AXIS out of range | re_axis=200 | Error: "AXIS must be 0-180" | Validation |
| TC-049 | `add_prescription` — AXIS negative | re_axis=-10 | Error: "AXIS must be 0-180" | Validation |
| TC-050 | `add_prescription` — customer not found | customer_id=99999 | Error: "customer not found" | Error |
| TC-051 | `add_prescription` — partial values (only RE) | Only right eye values | Prescription created with NULL left eye | Happy |
| TC-052 | `search_inventory` — by model name | query="Ray-Ban" | Matching items | Happy |
| TC-053 | `search_inventory` — low stock only | low_stock_only=True | Only items below threshold | Happy |
| TC-054 | `search_inventory` — no results | query="zzz" | Empty list | Edge |
| TC-055 | `add_inventory_item` — all required fields | model_name, location, cost, selling | Item created | Happy |
| TC-056 | `add_inventory_item` — missing model_name | model_name="" | Error: "model name required" | Validation |
| TC-057 | `add_inventory_item` — missing location | location="" | Error: "location required" | Validation |
| TC-058 | `add_inventory_item` — cost > selling price | cost=500, selling=300 | Warning: "cost exceeds selling price" | Validation |
| TC-059 | `add_inventory_item` — negative price | cost_price=-100 | Error: "price must be positive" | Validation |
| TC-060 | `update_inventory_stock` — add stock | quantity_change=+10 | New quantity = old + 10 | Happy |
| TC-061 | `update_inventory_stock` — reduce stock | quantity_change=-5 | New quantity = old - 5 | Happy |
| TC-062 | `update_inventory_stock` — reduce below zero | quantity_change=-100 (qty=5) | Error: "insufficient stock" | Validation |
| TC-063 | `update_inventory_stock` — item not found | inventory_id=99999 | Error: "item not found" | Error |
| TC-064 | `get_dashboard_stats` — returns all metrics | No args | total_customers, total_orders, pending, low_stock, revenue | Happy |
| TC-065 | `get_dashboard_stats` — empty database | Fresh DB | All zeros | Edge |
| TC-066 | `create_user` — valid staff user | username, password, role="staff" | User created | Happy |
| TC-067 | `create_user` — valid admin user | role="admin" | User created with admin role | Happy |
| TC-068 | `create_user` — duplicate username | Existing username | Error: "username exists" | Validation |
| TC-069 | `create_user` — short password | password="abc" | Error: "min 6 characters" | Validation |
| TC-070 | `create_user` — called by non-admin | current_user.role="staff" | Error: "admin only" | Auth |
| TC-071 | `navigate_to_page` — dashboard | page_name="dashboard" | URL: "/dashboard" | Happy |
| TC-072 | `navigate_to_page` — edit customer with ID | page_name="edit_customer", entity_id=5 | URL: "/customers/edit/5" | Happy |
| TC-073 | `navigate_to_page` — edit without ID | page_name="edit_customer", entity_id=None | Error: "entity_id required" | Validation |
| TC-074 | `navigate_to_page` — unknown page | page_name="unknown_page" | Error: "unknown page" | Validation |

### 12.3 Unit Tests — Language Service

| TC# | Test Case | Input | Expected | Category |
|---|---|---|---|---|
| TC-075 | Detect English text | "Create a new customer" | 'en' | Detection |
| TC-076 | Detect Hindi (Devanagari) | "naya grahak banao" (Devanagari) | 'hi' | Detection |
| TC-077 | Detect Gujarati script | "navo grahak umero" (Gujarati script) | 'gu' | Detection |
| TC-078 | Detect romanized Hindi | "naya grahak banao" | 'romanized_hi' | Detection |
| TC-079 | Detect romanized Gujarati | "navu grahak umarao" | 'romanized_gu' | Detection |
| TC-080 | Mixed English + romanized | "customer ka bill banao" | 'romanized_hi' (dominant) | Detection |
| TC-081 | Pure English with Indic words | "Ramesh bhai" | 'en' (mostly English) | Edge |
| TC-082 | Single word input | "hello" | 'en' | Edge |
| TC-083 | Empty input | "" | 'en' (default) | Edge |
| TC-084 | Numbers only | "9876543210" | 'en' (default) | Edge |
| TC-085 | Transliterate "mera bhai" | Input: "mera bhai" | Translation context understood by LLM | Transliteration |

### 12.4 Unit Tests — Speech-to-Text Service

| TC# | Test Case | Input | Expected | Category |
|---|---|---|---|---|
| TC-086 | Transcribe English audio | English WAV/WebM | Correct English text | Happy |
| TC-087 | Transcribe Hindi audio | Hindi audio | Correct Hindi text + lang='hi' | Happy |
| TC-088 | Transcribe Gujarati audio | Gujarati audio | Correct Gujarati text + lang='gu' | Happy |
| TC-089 | Transcribe code-mixed audio | "bill banao customer Ramesh ka" | Mixed text detected | Happy |
| TC-090 | Handle empty/silent audio | Silent recording | Empty text or "No speech detected" | Edge |
| TC-091 | Handle corrupt audio file | Random bytes | Graceful error message | Error |
| TC-092 | Handle very long audio (>60s) | 2-minute audio | Processed (may truncate with warning) | Edge |
| TC-093 | Handle very short audio (<1s) | 0.5s clip | Transcribed or "Too short" | Edge |
| TC-094 | Whisper not available, fall to Sarvam | Whisper fails | Sarvam API called | Fallback |
| TC-095 | Both STT backends fail | All backends error | Error: "Could not transcribe" | Error |
| TC-096 | Auto-detect language | No language hint | Correct language detected | Happy |
| TC-097 | Forced language hint | language_hint='gu' | Gujarati model prioritized | Config |

### 12.5 Integration Tests — Full Conversation Flows

| TC# | Test Case | Conversation | Expected Outcome | Category |
|---|---|---|---|---|
| TC-098 | Create customer via chat — happy path | "add customer Jayesh, phone 9898989898" | Customer created in DB | E2E |
| TC-099 | Create customer — incremental info | "add customer" -> name -> phone | Customer created after all info | E2E |
| TC-100 | Create order — full flow | Customer -> items -> advance -> confirm | Order in DB with correct total | E2E |
| TC-101 | Create order — customer not found | "bill for XYZ" (no match) | "Customer not found" + offer to create | E2E |
| TC-102 | Create order — no items provided | "bill for Ramesh" -> no items | Asked for items | E2E |
| TC-103 | Navigation — "show me orders" | "show me orders" | Client redirected to /orders/ | E2E |
| TC-104 | Navigation — "go to dashboard" | "dashboard dikhao" (Hindi) | Redirect to /dashboard | E2E |
| TC-105 | Search — "find customer Suresh" | Search query | List of matching customers returned | E2E |
| TC-106 | Prescription — full flow | "add prescription for Suresh, RE -2.5/-1.0 90" | Prescription saved | E2E |
| TC-107 | Inventory — check low stock | "kya stock kam hai?" (Hindi) | List of low-stock items | E2E |
| TC-108 | Inventory — add item | "add Ray-Ban Aviator, rack A3, cost 800, sell 1500" | Item in inventory | E2E |
| TC-109 | User management — admin creates user | "add user staff1 with password test123" | User created | E2E |
| TC-110 | User management — staff tries to create | "add user" (as staff user) | "Admin only" error | Auth |
| TC-111 | Gujarati input -> English DB | Gujarati script input for customer | Customer name in English in DB | Multilingual |
| TC-112 | Romanized Gujarati -> English DB | "navu customer umarao naam Rajesh" | Customer name="Rajesh" in DB | Multilingual |
| TC-113 | Hindi input -> English DB | "customer banaao naam Amit phone 9876543210" | Customer created in English | Multilingual |
| TC-114 | Voice -> text -> action | Record "create bill for Ramesh" | Transcribed + order flow starts | Voice |
| TC-115 | Voice in Hindi -> action | Record Hindi audio | Transcribed + understood + action | Voice |
| TC-116 | Concurrent sessions | 2 users chatting simultaneously | Independent conversations | Concurrency |
| TC-117 | Session recovery after page reload | Chat, reload page | History restored | Persistence |
| TC-118 | Chat history pagination | 50+ messages | Scrollable, loads older on scroll | UI |

### 12.6 Edge Case and Adversarial Tests

| TC# | Test Case | Input | Expected | Category |
|---|---|---|---|---|
| TC-119 | XSS in chat input | `<script>alert(1)</script>` | Escaped, treated as text | Security |
| TC-120 | SQL injection via chat | `'; DROP TABLE users; --` | Escaped, no DB harm (SQLAlchemy parameterized) | Security |
| TC-121 | Very long message (10K chars) | 10,000 char string | Truncated or handled gracefully | Edge |
| TC-122 | Rapid-fire messages (10 in 1s) | 10 quick messages | Queued, processed in order | Edge |
| TC-123 | Emoji in input | "add customer (smile emoji) Jayesh" | Processed, emoji stripped from name | Edge |
| TC-124 | Special characters in names | "O'Brien", "Mueller" | Handled correctly | Edge |
| TC-125 | Unicode in phone | Arabic numeral chars | Converted or rejected gracefully | Edge |
| TC-126 | Ambiguous intent | "Ramesh" (just a name) | "What would you like to do with customer Ramesh?" | Edge |
| TC-127 | Contradictory info | "add customer, name: delete everything" | Treated as name, no destructive action | Security |
| TC-128 | Price in words | "cost is fifteen hundred" | Parsed as 1500.00 (by LLM) | Edge |
| TC-129 | Relative dates | "delivery tomorrow" | Calculated correct date | Edge |
| TC-130 | Gibberish input | "asdfghjkl" | "I didn't understand. Could you rephrase?" | Error |
| TC-131 | Empty message | "" | Ignored or "Please type a message" | Edge |
| TC-132 | Chat while not logged in | Unauthenticated request | 401 + redirect to login | Auth |
| TC-133 | Request data about other users | Staff asks for admin data | Access denied per role | Auth |

### 12.7 Performance Tests

| TC# | Test Case | Metric | Target | Category |
|---|---|---|---|---|
| TC-134 | LLM response time — simple query | Wall clock | < 5 seconds | Performance |
| TC-135 | LLM response time — tool call | Wall clock | < 8 seconds | Performance |
| TC-136 | STT transcription time — 10s audio | Wall clock | < 5 seconds | Performance |
| TC-137 | Chat history load time — 100 messages | Wall clock | < 1 second | Performance |
| TC-138 | Concurrent chat sessions — 5 users | Server stability | No crashes, all respond | Performance |

---

## 13. Deployment and Configuration

### 13.1 Local Development Setup

```bash
# 1. Install Ollama (one-time)
# Download from https://ollama.com and install

# 2. Pull the LLM model (one-time, ~5GB)
ollama pull qwen3.6:8b

# 3. Start Ollama (if not auto-started)
ollama serve

# 4. Install Python dependencies
cd backend
pip install -r requirements.txt

# 5. Run database migration
flask db migrate -m "Add chat_messages table"
flask db upgrade

# 6. Start the app
python app.py
```

### 13.2 Environment Variables (new additions to `.env`)

```bash
# -- AI Assistant Configuration --
AI_LLM_MODEL=qwen3.6:8b
AI_OLLAMA_HOST=http://localhost:11434
AI_STT_BACKEND=whisper_local
AI_STT_MODEL_SIZE=small
AI_SARVAM_API_KEY=
AI_MAX_TOOL_ITERATIONS=5
AI_DEFAULT_LANGUAGE=en
```

### 13.3 Render Deployment Considerations

**WARNING**: The **Render free tier** has limited memory (512MB). Running Ollama + Whisper locally on Render is **not feasible**. For deployment:
- Use **Sarvam AI API** for STT (no local model needed)
- Use a **cloud-hosted Ollama** or an **API-based LLM** (Groq free tier, or self-hosted on a VPS)
- Alternatively: Run Ollama on a separate always-on machine and point `AI_OLLAMA_HOST` to it

### 13.4 Hardware Requirements

| Component | Minimum | Recommended |
|---|---|---|
| **RAM** | 8 GB | 16 GB |
| **GPU VRAM** | Not required (CPU mode) | 8 GB (for faster LLM) |
| **Disk** | 10 GB free (model downloads) | 20 GB |
| **CPU** | 4 cores | 8 cores |

---

## 14. Open Questions for Review

### Q1: LLM Model Size
The plan defaults to **Qwen3.6 8B** which requires ~8GB RAM. If your machine has less:
- **Option A**: Use `llama3.2:3b` (4GB, lower accuracy)
- **Option B**: Use `qwen3.6:8b` with CPU (slower but works on 8GB RAM)
- **Option C**: Use a cloud API (Groq free tier — 14,400 requests/day for free)

**What are your machine's specs (RAM, GPU)?**

### Q2: STT Priority
For speech-to-text, should we:
- **Option A**: Local Whisper only (fully offline, ~1-3GB model download)
- **Option B**: Sarvam AI API only (better Indian language accuracy, requires API key)
- **Option C**: Both — Whisper primary, Sarvam fallback (recommended)
- **Option D**: Browser Web Speech API only (zero setup, Chrome/Edge only, less accurate)

### Q3: Chat History Persistence
Should chat conversations be:
- **Option A**: Stored permanently in the database (auditable, uses DB space)
- **Option B**: Session-only (lost on page refresh, no DB cost)
- **Option C**: Stored for 30 days, then auto-purged (recommended)

### Q4: Response Language Behavior
When user speaks in Hindi/Gujarati, should the assistant:
- **Option A**: Always respond in the same language as the user
- **Option B**: Always respond in English regardless of input language
- **Option C**: Respond in user's language for conversation, but show data in English (recommended)

### Q5: Deletion via Chat
Should the assistant be able to **delete** records through chat? This is a destructive action.
- **Option A**: Yes, with double confirmation ("Are you sure? Type 'YES' to confirm")
- **Option B**: No — for deletes, redirect user to the appropriate page
- **Option C**: Only for admins; staff get the existing deletion request flow (recommended)

### Q6: Voice Activation Keyword
Should the assistant support a "wake word" (like "Hey Optical") for hands-free activation?
- This adds complexity but could be useful in a shop environment
- Can be added as a future enhancement

---

## Estimated Implementation Timeline

| Phase | Description | Effort |
|---|---|---|
| Phase 1 | Backend AI Service Layer + Config | 2-3 days |
| Phase 2 | Tool Functions (14 tools + validation) | 3-4 days |
| Phase 3 | Multilingual Pipeline | 1-2 days |
| Phase 4 | Speech-to-Text Integration | 1-2 days |
| Phase 5 | Frontend Chat Widget | 2-3 days |
| Phase 6 | Orchestration + Error Handling | 2-3 days |
| Testing | All 138 test cases | 2-3 days |
| **Total** | | **~13-20 days** |

---

*This plan will be updated based on your review feedback. No implementation will begin until approved.*
