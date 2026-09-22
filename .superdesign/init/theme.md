# Theme tokens

## Compact token summary

- Framework: Bootstrap 5.3 with custom CSS.
- Font: Segoe UI, Tahoma, Geneva, Verdana, sans-serif.
- Brand / primary: Bootstrap blue `#0d6efd`.
- Sidebar: light `#f8f9fa` or dark `#212529`; desktop width 240px.
- Cards: 10px radius; subtle elevation; hover lift of 2px.
- Semantic KPI borders: primary `#4e73df`, success `#1cc88a`, info `#36b9cc`, warning `#f6c23e`.
- Responsive breakpoint: 768px. Mobile uses offcanvas navigation, 44px minimum buttons, reduced card padding, and cardified tables.

## Raw style source

`backend/static/css/style.css` defines the shared responsive sidebar, mobile table/card behavior, card rules, and dark-theme overrides. `backend/static/css/chat_widget.css` styles the assistant. `backend/static/css/print.css` is isolated for paper output.
