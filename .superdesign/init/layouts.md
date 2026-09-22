# Shared application layout

## `backend/templates/base.html`

The application shell renders an authenticated offcanvas-on-mobile / fixed-on-desktop sidebar, a compact top navigation bar, flash messages, and the AI assistant component. It loads Bootstrap 5, Font Awesome, `static/css/style.css`, `static/css/chat_widget.css`, Socket.IO, and `static/js/chat_widget.js`.

Key layout structure:

```html
<nav id="sidebarMenu" class="offcanvas-md offcanvas-start bg-dark sidebar">…</nav>
<main class="col-md-9 ms-sm-auto col-lg-10 px-3 px-md-4 main-content">
  <nav class="navbar navbar-expand-lg navbar-light bg-light rounded shadow-sm">…</nav>
  {% block content %}{% endblock %}
</main>
{% include 'components/chat_widget.html' %}
```

Navigation: Dashboard, Customers, Orders, Inventory; admins also get Audit Logs, Manage Users, Deletion Requests, and GST Configuration. The top bar contains the global search and light/dark mode control.
