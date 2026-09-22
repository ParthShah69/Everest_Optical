# Shared UI primitives

This Flask/Jinja application uses Bootstrap 5 and Font Awesome rather than a separate component directory. Shared UI primitives are Bootstrap classes: `.btn`, `.card`, `.form-control`, `.form-select`, `.table`, `.badge`, and `.alert`.

## AI chat widget

- Source: `backend/templates/components/chat_widget.html`
- Category: basic
- Description: Persistent assistant launcher, chat history and message input.
- Client behavior source: `backend/static/js/chat_widget.js`
- Styles source: `backend/static/css/chat_widget.css`

## Global search

- Source: `backend/templates/base.html`
- Category: basic
- Description: Navbar search control with customer/order quick actions.
- API: `GET /api/search?q=<query>`.
