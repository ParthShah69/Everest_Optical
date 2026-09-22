# Extractable UI patterns

## SidebarNavigation

- Source: `backend/templates/base.html`
- Category: layout
- Description: Role-aware persistent navigation, fixed on desktop and offcanvas on mobile.
- Extractable props: active endpoint, is_admin, pending deletion count.
- Hardcoded: menu labels and Font Awesome icons.

## GlobalSearch

- Source: `backend/templates/base.html`
- Category: basic
- Description: Debounced cross-customer/order lookup with order/Rx actions.
- Extractable props: endpoint, minimum query length.

## KpiCard

- Source: `backend/templates/dashboard/index.html`
- Category: basic
- Description: Colour-coded sales/collection/dues summary tile.
- Extractable props: label, value, caption, icon, semantic colour.

## ChatWidget

- Source: `backend/templates/components/chat_widget.html`
- Category: basic
- Description: Floating conversational assistant with text and microphone controls.
- Extractable props: assistant status, supported actions.
