# Page dependency tree

## `/dashboard`

Entry: `backend/templates/dashboard/index.html`

Dependencies:

- `backend/templates/base.html`
  - `backend/static/css/style.css`
  - `backend/templates/components/chat_widget.html`
  - `backend/static/css/chat_widget.css`
  - `backend/static/js/chat_widget.js`
- Bootstrap 5 / Font Awesome CDN

## `/orders/new/<customer_id>`

Entry: `backend/templates/orders/new.html`

Dependencies:

- `backend/templates/base.html`
- inventory autocomplete API: `backend/routes/inventory_routes.py`
- tax configuration: `backend/models/tax_config.py`

## `/orders/<id>`

Entry: `backend/templates/orders/view.html`

Dependencies:

- `backend/templates/base.html`
- payment routes: `backend/routes/payment_routes.py`
- printable views: `backend/templates/print/*.html`

## `/prescriptions/history/<customer_id>`

Entry: `backend/templates/prescriptions/history.html`

Dependencies:

- `backend/templates/base.html`
- `backend/models/prescription.py`
