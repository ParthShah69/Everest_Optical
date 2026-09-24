# AI Chat: Conversations and ERP Actions

The Optical ERP assistant is a signed-in staff member's conversational shortcut to the ERP. It can answer questions, look up operational records, and perform a small set of validated actions. It is not an unrestricted database interface: the same server-side validation, stock rules, and role checks used by the normal ERP screens remain in force.

## Conversations that keep their context

Each conversation has a unique **session ID**. Every user and assistant message is saved with that session ID and the authenticated ERP user ID. This makes a conversation usable as a real working thread instead of a collection of unrelated one-off prompts.

The chat window provides the following behaviour:

| Need | How it works |
| --- | --- |
| Continue the current discussion | Keep writing in the open conversation. The server supplies recent messages from that same session to the model, so references such as "that customer" or "the previous order" have useful context. |
| Start a clean discussion | Select **New chat**. A fresh session is created and the visible message area is reset. The prior conversation stays saved. |
| Return to an older discussion | Open **History**, select a previous conversation, and its saved messages are restored. Sending the next message continues that session. |
| Keep a record | Conversation history is stored in `chat_messages`, not only in the browser. It remains available after closing/reopening the widget and can be reopened on another device after signing in as the same ERP user. |

The model receives a bounded recent slice of the selected conversation rather than an unlimited transcript. This keeps requests fast and avoids an old, unrelated discussion drowning out the current task. When starting a materially new task, use **New chat**; when a detail matters, restate the order number, customer, or date rather than relying on a very old message.

## Privacy, safety, and permissions

- History and session listings are scoped to the currently authenticated ERP user. Knowing or guessing another session ID does not reveal its messages.
- API keys stay on the server. They are never returned to the browser or saved in chat history.
- The assistant works only with data available to the ERP application. It does not browse the public web or access a user's device files.
- Read operations do not change records. Write operations validate their input server-side and return the resulting record/link. Inventory-linked orders still obey stock validation and stock is deducted only through the standard delivery rule.
- Creating a user is restricted to ERP administrators. Other actions retain the validation and audit behaviour of the ordinary application routes.
- Do not put passwords, API keys, card details, or unnecessary personal health information into a prompt.

## Available ERP actions

The assistant decides which validated tool to call from natural language. A tool result is shown as part of its answer; when appropriate it includes a link to the normal ERP page so the operator can inspect the source record.

### Read and find

| Capability | What it can do | Example prompt |
| --- | --- | --- |
| Customer search | Find customers by name, phone, or care-of name. | "Find customers named Ramesh Patel." |
| Customer profile | Show one customer's contact details, recent orders, and prescriptions. | "Show customer 42's details." |
| Multi-customer details | Return a compact comparison for several explicitly identified customers (for example, selected IDs), including balances and recent activity. | "Show details for customers 12, 19 and 24." |
| Bill/order search | Find bills by customer, bill number, lifecycle status, date range, delivery date, or balance/due filters. | "Show all ready orders due today." |
| Bill/order detail | Display a specific bill's customer, line items, tax, payments/advance, balance, delivery information, linked prescription, and links to view/print it. | "Show bill ORD-0007." |
| Inventory lookup | Search by model, brand, or frame type; list low-stock items. | "Which Titan frames are low in stock?" |
| Operational snapshot | Show dashboard totals such as customers, orders, sales, collections, dues, pending work, and low stock. | "Give me today's shop snapshot." |
| Navigation | Open a relevant ERP screen when the operator asks to show or go to it. | "Open the new order page for customer 42." |

### Create or update (validated actions)

| Capability | Guardrails | Example prompt |
| --- | --- | --- |
| Create customer | Checks name/phone format and searches for likely duplicates before creation. | "Add Neha Shah, 9876543210." |
| Update customer | Changes only supplied profile fields. | "Change customer 42's phone to 9876543210." |
| Create a bill/order | Uses the normal sequence, calculates totals/tax server-side, validates line items, and links available inventory/prescription records. | "Create a bill for customer 42: 1 Ray-Ban frame at 2500 and 1 lens at 1200, GST 12%, advance 1000." |
| Update order status | Accepts only configured lifecycle statuses. Marking an inventory-linked order delivered checks stock first; reverting delivery restores stock through the shared ERP rule. | "Mark order 77 ready for pickup." |
| Add prescription | Validates clinically sensible power/axis ranges and stores a linked prescription. | "Add prescription for customer 42: RE -1.25, LE -0.75." |
| Add inventory item | Requires product, location, and valid cost/selling prices. | "Add Acme A10, rack B2, cost 500, sale 950, quantity 6." |
| Adjust stock | Prevents stock from falling below zero and accepts a reason. | "Add 4 units to inventory item 18; reason: supplier delivery." |
| Create ERP user | Administrator-only; passwords are never echoed in the response. | "Create staff user anita with the supplied password." |

For a new bill, the assistant should first identify the customer and confirm the line items/prices if the request is ambiguous. The canonical result is the created order number and a normal ERP order link—not a chat-only record.

## Useful filtering language

Use concrete filters to make results short and reliable:

- **Status:** `Pending`, `Ready`, `Delivered`, or another configured order lifecycle state.
- **Date:** `today`, `tomorrow`, a date such as `2026-09-25`, or a bounded range such as `from 2026-09-01 to 2026-09-25`.
- **Financial state:** `with balance due`, `paid`, `advance below 500`, or `highest value` (where exposed by the order query).
- **Customer:** full/partial name, phone, customer ID, or a list of customer IDs for a multi-customer response.
- **Inventory:** model, brand, frame type, location, and `low stock only`.

Examples:

> "Show Ramesh's pending orders with balance due."

> "Compare customers 12, 19, and 24: last order and outstanding balance."

> "Show bill ORD-0007 and open the printable invoice."

> "Create a bill for customer 42. Ask me for anything missing before you save it."

## Deliberately not automated

The following are intentionally not exposed as free-form chat actions:

- deleting customers, orders, payments, prescriptions, or inventory;
- bulk price/stock changes or bulk status updates;
- refunds, payment reversals, credit-note issuance, or accounting postings;
- role/permission changes and password resets;
- sending WhatsApp/email messages without a review step;
- exporting broad datasets or sharing records outside the ERP.

These are high-impact or privacy-sensitive operations and should stay in a purpose-built screen with explicit confirmation, audit logging, and—where needed—manager approval.

## Candidate next tools (not currently enabled)

The following are useful additions, but should be added only with their stated safeguards. They are **future candidates**, not promises that the assistant already performs them.

| Candidate | Value | Required safeguard |
| --- | --- | --- |
| Draft invoice/quote | Prepare, but do not save, a bill from a spoken list of items. | Present a line-by-line preview and require an explicit Save action. |
| Payment collection | Record cash/card/UPI splits against an order. | Confirm amount, mode, reference, and remaining balance before posting. |
| Print/share document | Open job slip, receipt, or invoice for one known order. | Resolve a single order first; preview the document, then let the user print/share. |
| Delivery queue | Filter ready/pending orders by promised date and delivery mode. | Read-only by default; status changes stay explicit. |
| Customer communications | Draft WhatsApp/SMS/email reminders and ready-for-pickup notes. | Show recipient and final message; require user-triggered send. |
| Purchase receiving | Draft a supplier receipt and stock intake. | Confirm supplier, quantities, costs, and duplicate invoice number. |
| Analytics questions | Sales, margin, due, and product trend questions. | Use aggregate results, date ranges, and clear labels for estimates. |
| Attachments/OCR review | Extract a prescription from an uploaded image into a draft. | Show every extracted field for review; never auto-save medical values. |
| Voice commands | Use browser recognition or server transcription for the same chat tools. | Microphone permission, visible transcript, and normal validation before action. |

## Deployment notes

For Vercel/serverless deployments, use the REST chat endpoints; Socket.IO is optional and should be disabled unless a stateful host is configured. A Groq provider configuration typically needs:

```env
AI_PROVIDER=groq
GROQ_API_KEY=your_server_only_key
AI_LLM_MODEL=openai/gpt-oss-20b
AI_STT_BACKEND=groq_api
AI_GROQ_STT_MODEL=whisper-large-v3-turbo
AI_DISABLE_SOCKETIO=true
```

Set these in the hosting provider's protected environment-variable settings and then redeploy. The widget's status label shows whether the configured language model is reachable; a saved conversation can still be viewed even while the provider is temporarily unavailable.

## Operator checklist

1. Open the chat and verify it says **Online** before expecting an AI answer.
2. Use **New chat** for a new customer or task; use **History** to resume a saved one.
3. Include IDs, order numbers, dates, and amounts when precision matters.
4. Review returned totals, tax, delivery date, and stock warnings before treating a write action as final.
5. Open the returned ERP link to print, collect payment, or make a sensitive change in the normal workflow.

