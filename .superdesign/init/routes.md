# Flask route map

| Path | Template / feature |
| --- | --- |
| `/dashboard` | `dashboard/index.html` — KPI dashboard |
| `/customers/` | `customers/list.html` — customers |
| `/prescriptions/*` | add, edit, view and history prescription workflow |
| `/orders/` | `orders/list.html` — order lifecycle |
| `/orders/new/<customer_id>` | `orders/new.html` — billing, tax, delivery scheduling |
| `/orders/<id>` | `orders/view.html` — invoice summary, payment actions |
| `/orders/<id>/print/invoice` | `print/invoice.html` |
| `/orders/<id>/print/job-slip` | `print/job_slip.html` |
| `/payments/<id>/print/receipt` | `print/payment_receipt.html` |
| `/inventory/` | inventory list, add, edit |
| `/tax-config/` | `tax_config/index.html` |

Route modules are under `backend/routes/`; all normal routes are registered in `backend/app.py`.
