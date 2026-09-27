# Everest Optical ERP — Complete Implementation Prompt

Copy everything below this line and give it to the implementation agent.

---

You are implementing a complete production upgrade for the Everest Optical ERP application.

Repository:

`E:\\E drive\\Optical_ERP`

The application is a Flask + SQLAlchemy optical-shop ERP. Inspect the repository before making changes. Preserve all existing functionality and user data. Do not reset the database, delete files, or overwrite unrelated work.

If your environment supports subagents, spawn parallel subagents for these disjoint areas:

1. Stock, purchasing, suppliers, returns, and inventory
2. Accounting, cashbook, expenses, payments, and reports
3. WhatsApp Business Cloud API integration
4. Workshop, CRM, loyalty, notifications, permissions, and UI

Each subagent must work only in its assigned area, report findings/changes, and be archived or dissolved after completion. Reconcile all work before final verification.

Implement the following end-to-end, not just a plan.

## 1. Foundation architecture

The existing application currently has mutable inventory quantities, order-linked payments, basic admin/staff roles, and business logic embedded in route files.

Introduce these services:

```text
backend/services/
  stock_service.py
  purchasing_service.py
  payment_service.py
  accounting_service.py
  return_service.py
  workshop_service.py
  loyalty_service.py
  permission_service.py
  notification_service.py
  report_service.py
  whatsapp_service.py
```

Routes should validate input, check permissions, call services, and render/redirect. Business operations must use one database transaction wherever stock, payment, accounting, or order state changes together.

Do not break existing routes while migrating. Preserve compatibility with the existing `User.role` and `Inventory.quantity` fields until migration is complete.

## 2. Branches and permissions

Add:

- `Branch`
- `UserBranch`
- `Role`
- `Permission`
- `RolePermission`
- `UserRole`

Add permissions such as:

```text
orders.view
orders.create
orders.edit
orders.cancel
payments.collect
refunds.approve
inventory.view
inventory.adjust
purchases.create
purchases.approve
purchases.receive
workshop.view
workshop.update
reports.view
accounting.view
accounting.close_day
users.manage
settings.manage
exports.download
deletions.approve
loyalty.adjust
```

Apply branch scope to orders, inventory, payments, purchases, workshop jobs, reports, and number sequences.

Add approval thresholds for large discounts, refunds, stock adjustments, manual loyalty changes, and deletions. Log permission failures and approval actions.

Existing admin users must retain access. Existing staff users must migrate safely with reasonable default permissions.

## 3. Stock ledger and inventory

Add:

- `StockBalance`
- `StockLedgerEntry`
- `StockAdjustment`
- `StockAdjustmentItem`
- `StockTransfer`
- `StockTransferItem`
- `InventoryLot` where useful

Stock movement types:

```text
PURCHASE_RECEIPT
SALE
SALE_RETURN
EXCHANGE_IN
EXCHANGE_OUT
DAMAGE
ADJUSTMENT
TRANSFER_IN
TRANSFER_OUT
WORKSHOP_USAGE
```

Every movement must record branch, inventory item, quantity delta, unit cost, movement type, reference type, reference ID, user, timestamp, reason, and running balance where practical.

Backfill current `Inventory.quantity` into opening stock ledger entries. Keep `Inventory.quantity` as a cached balance during migration, but update it only through `stock_service.py`.

Do not silently clamp stock to zero. If stock is insufficient, return a clear error and prevent the transaction.

Add reconciliation tools and tests.

## 4. Barcode and labels

Implement:

- Persist barcode during inventory creation and editing
- Reject duplicate barcodes
- Generate a barcode when one is missing
- Search inventory by barcode
- Barcode lookup during order entry, receiving, and adjustments
- Scanner-friendly USB/Bluetooth input
- Printable barcode labels
- Batch label printing
- Labels containing item, brand, model, price, barcode, branch, and location

## 5. Suppliers and purchasing

Add:

- `Supplier`
- `SupplierContact` if needed
- `PurchaseOrder`
- `PurchaseOrderItem`
- `GoodsReceipt`
- `GoodsReceiptItem`
- `PurchaseReturn`
- `PurchaseReturnItem`
- `SupplierPayment`

Supplier fields should support name, legal name, GSTIN/PAN where applicable, phone, email, address, payment terms, credit limit, opening balance, and active status.

Purchase order states:

```text
Draft
Submitted
Approved
Partially Received
Fully Received
Closed
Cancelled
```

Creating or approving a purchase order must not increase stock. Receiving goods must:

1. Validate received quantities.
2. Create receipt records.
3. Create positive stock ledger entries.
4. Update branch stock balances.
5. Create supplier payable accounting entries.
6. Update purchase status.
7. Commit atomically.

Add supplier, purchase, receiving, returns, and supplier payment screens and routes.

## 6. Returns, refunds, and exchanges

Add:

- `CustomerReturn`
- `CustomerReturnItem`
- `Refund`
- `ExchangeOrder`
- `CustomerCredit`

Return states:

```text
Requested
Approved
Received
Inspected
Refunded
Exchanged
Store Credit
Rejected
Completed
```

Record original order/item, quantity, reason, condition, resalable flag, refund method, approval user, completion user, stock action, and notes.

Do not automatically restock damaged or non-resalable items. Refunds must update payment history, cashbook, accounting, customer balance, stock where applicable, and loyalty points where applicable.

## 7. Accounting, cashbook, and expenses

Add:

- `Account`
- `JournalEntry`
- `JournalLine`
- `CashAccount`
- `CashbookEntry`
- `Expense`
- `DayClosure`
- `CustomerReceivable`
- `SupplierPayable`

Account types:

```text
Asset
Liability
Income
Expense
Equity
```

Support cash, bank, UPI, card, and other payment accounts.

Post accounting entries for:

```text
Customer payment:
  Debit Cash/Bank
  Credit Customer Receivable

Sale:
  Debit Cash or Receivable
  Credit Sales
  Credit Tax Payable

Purchase receipt:
  Debit Inventory
  Credit Supplier Payable

Supplier payment:
  Debit Supplier Payable
  Credit Cash/Bank

Refund:
  Debit Sales Return/Refund account
  Credit Cash/Bank

Stock adjustment:
  Debit or credit Inventory Adjustment account
```

Add cashbook, expenses, journal, receivable ageing, supplier payable ageing, and daily closing screens.

Day closing must record business date, branch, opening balance, expected balance, counted balance, variance, closing user, and timestamp. After closing, entries must be immutable except through authorized reversal.

Do not invent unreliable historical accounting data. Import old payments as opening or unclassified entries where exact accounting cannot be reconstructed.

## 8. Workshop and lab management

Add:

- `WorkshopJob`
- `WorkshopJobItem`
- `WorkshopStatusHistory`
- `WorkshopTask`
- `WorkshopQC`
- `WorkshopRemake`

Workshop states:

```text
Queued
Sent to Lab
In Production
Awaiting Parts
QC Hold
Ready for Pickup
Delivered
Remake
```

Support technician assignment, lab/supplier assignment, prescription snapshot, lens/frame information, promised date, priority, production notes, QC checklist, QC pass/fail, remake reason, and completion timestamp.

Workshop status must be separate from customer-facing `Order.status`, while still mapping to order display status.

Add a workshop queue/board with filters for branch, technician, status, priority, and due date.

## 9. Customer 360, CRM, reminders, and loyalty

Add a customer profile showing contact details, orders, payments, outstanding balance, prescriptions, WhatsApp activity, notes, follow-ups, reminders, and loyalty balance.

Add:

- `CustomerReminder`
- `CustomerInteraction`
- `LoyaltyLedgerEntry`

Reminder types:

- Ready for pickup
- Unpaid balance
- Prescription refresh
- Lens expiry
- Warranty follow-up
- Abandoned order
- Birthday
- Promotional campaign

Every reminder must have due date, owner, status, completed timestamp, related customer/order, delivery channel, and an idempotency key.

Replace standalone loyalty balance logic with a ledger supporting earn, redeem, expire, refund reversal, and manual adjustment. Do not allow redemption above available balance.

## 10. Dashboard, work queue, and notifications

Add a “Today’s Work” dashboard containing orders due today, overdue orders, ready orders, workshop queue, pending payments, low-stock items, failed WhatsApp messages, customer reminders, pending approvals, and cash closing status.

Each item must have a clear next action, owner, due date, ageing, branch, and direct action button.

Add a persistent notification model with user, type, related record, message, read status, timestamp, action URL, and deduplication key. Notifications must be idempotent and auditable.

## 11. Reporting

Add dedicated report routes and templates for:

- Sales by date, branch, staff, product, and payment method
- Collections
- Outstanding balances and ageing
- GST/tax summary
- Discounts and overrides
- Purchases
- Supplier payables
- Inventory valuation
- Stock movement
- Low stock and reorder suggestions
- Dead stock
- Workshop turnaround time
- Remake rate
- Customer repeat purchases
- Loyalty liability
- Daily cash closing
- User activity and audit history

Every report must support date range, branch filter, pagination, export, permission checks, applied-filter display, an “as of” timestamp, and reconciled totals. Keep existing CSV exports working.

## 12. WhatsApp Business integration

The existing click-to-chat integration must remain as a fallback.

Use the official Meta WhatsApp Business Platform Cloud API as the primary provider. Do not use unofficial WhatsApp Web automation.

Add a provider abstraction supporting:

```text
cloud_api
click_to_chat
twilio
360dialog
```

Add models:

- `WhatsAppContact`
- `WhatsAppConsent`
- `WhatsAppMessage`
- `WhatsAppWebhookEvent`
- `WhatsAppTemplate`

Suggested consent fields:

```text
customer_id
phone_e164
category
opted_in
source
opted_in_at
opted_out_at
```

Suggested message fields:

```text
customer_id
direction
provider
provider_message_id
message_type
template_name
status
payload_json
sent_at
delivered_at
read_at
failed_at
error_code
retry_count
idempotency_key
```

Add environment variables:

```text
WHATSAPP_PROVIDER=cloud_api
WHATSAPP_API_VERSION=
WHATSAPP_PHONE_NUMBER_ID=
WHATSAPP_WABA_ID=
WHATSAPP_ACCESS_TOKEN=
WHATSAPP_APP_SECRET=
WHATSAPP_WEBHOOK_VERIFY_TOKEN=
WHATSAPP_DEFAULT_LOCALE=en_US
```

Never commit real secrets or log access tokens.

Add:

```text
GET  /api/whatsapp/webhook
POST /api/whatsapp/webhook
```

GET must validate the Meta verification token and return the challenge.

POST must validate `X-Hub-Signature-256`, store the raw event, deduplicate provider event IDs, return HTTP 200 quickly, and process the event asynchronously.

Track sent, delivered, read, and failed statuses.

Use idempotency keys such as:

```text
order_id + notification_type + template_version
```

Retry only network failures, HTTP 429, and temporary HTTP 5xx errors. Do not retry permanent 4xx errors, opt-outs, invalid numbers, or policy failures.

Add approved utility templates for order confirmation, order ready, payment receipt, invoice available, delivery update, payment reminder, and opt-out confirmation.

Record customer consent before proactive notifications. Honor STOP/unsubscribe requests.

Support invoice and receipt delivery using either short-lived signed HTTPS URLs or WhatsApp media upload/document messages. Never expose private filesystem paths.

Add manual send buttons with permission checks and audit logs.

Recommended deployment:

- Keep Flask API and webhook on Render.
- Add a background worker for outbound messages and retries.
- Use Vercel only for a thin webhook if necessary.
- Do not process media or long-running jobs synchronously in a serverless request.

Official references:

- https://whatsappbusiness.com/developers/developer-hub/
- https://www.postman.com/meta/whatsapp-business-platform/folder/13382743-ba8d099d-007e-4b52-b9f2-3cf3c60e4fbc

## 13. Migrations and data safety

Create proper Alembic/Flask-Migrate migrations in this order:

1. Branches and permissions
2. Stock balances and stock ledger
3. Suppliers and purchasing
4. Goods receiving
5. Accounting and cashbook
6. Returns, refunds, and exchanges
7. Workshop
8. Loyalty and reminders
9. WhatsApp integration
10. Reports and dashboard improvements

Before changing behavior:

- Inspect the current schema.
- Back up existing data if possible.
- Add nullable columns first where necessary.
- Backfill data safely.
- Add constraints only after backfill.
- Keep compatibility fields during transition.

Never run destructive database resets.

## 14. Testing

Add or update tests for:

- Permission enforcement
- Branch isolation
- Stock ledger posting
- Insufficient stock
- Purchase receiving
- Partial receiving
- Purchase return
- Sale return
- Refund
- Exchange
- Cashbook posting
- Day closing
- Accounting balance
- Loyalty earn/redeem/reversal
- Workshop status history
- Notification deduplication
- WhatsApp webhook verification
- WhatsApp signature validation
- WhatsApp idempotency
- Retry behavior
- Opt-out handling
- Invoice/document message creation
- Existing AI features
- Existing CSV exports
- Existing printing
- Existing order flows

Run import checks, unit tests, migration tests, template rendering tests, API smoke tests, browser smoke tests, and responsive/mobile smoke tests.

## 15. UI requirements

Keep the existing responsive visual system and dark mode.

Add clear navigation for Purchasing, Stock, Accounting, Workshop, Reports, CRM, and Settings.

Ensure mobile-friendly tables, fast counter workflows, keyboard-friendly search, scanner-friendly input, empty states, loading states, confirmation dialogs, accessible labels, focus states, clear errors, and permission-aware action buttons.

Do not hide important errors behind generic flash messages.

## 16. Final validation

After implementation:

1. Review git status.
2. Ensure unrelated user changes remain untouched.
3. Run all tests.
4. Run migrations against a test database.
5. Verify existing login, customer, prescription, order, payment, inventory, printing, exports, AI, and click-to-chat flows.
6. Verify purchasing, receiving, returns, accounting, workshop, reports, permissions, notifications, and WhatsApp API flows.
7. Document all environment variables.
8. Document Meta WhatsApp setup steps.
9. Document Render worker/webhook deployment.
10. Update `task.md` with completed and remaining work.
11. Provide a final summary containing files changed, migrations created, features implemented, tests run, required environment variables, manual setup, limitations, and follow-up work.

Do not claim completion unless the implementation and verification have actually been performed.

---
