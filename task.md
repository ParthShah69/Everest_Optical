# Optical ERP Upgrade Tracker

## Phase 1 — Core billing and clinical data

- [x] Enhanced prescription and customer data capture.
- [x] Split payments, sequential receipts, order numbering, stock-linked billing, dashboard KPIs, smart search, WhatsApp actions, and expanded status/tax fields.
- [x] Add a reusable GST tax-configuration model and wire it into order entry.
- [x] Show the enhanced prescription fields in prescription history.
- [x] Extract compatible DV/NV/PD values from prescription OCR.

## Phase 2 — Print and delivery workflow

- [x] Add print-optimized workshop job slip, payment receipt, and final invoice views.
- [x] Support delivery offsets in days and hours in addition to scheduled date/time.

## Phase 3 — Optional accounting and purchases

- [ ] Loyalty points.
- [ ] Supplier purchase/stock receiving.
- [ ] Cash book and accounting ledger.

## Validation

- [x] Run application import, template rendering, database migration, and browser smoke checks after implementation.

## Stabilization and responsive usability

- [x] Reconcile delivered-order inventory when orders are created, edited, cancelled, or deleted.
- [x] Keep GST calculation consistent between the billing form preview and server-side totals.
- [x] Improve responsive navigation, global search, lists, accessibility, and printable mobile previews.
- [x] Harden chatbot/STT fallbacks and align AI-created orders with sequential billing, GST, and delivery stock rules.
