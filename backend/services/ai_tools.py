"""
ai_tools.py
===========
ERP tool functions exposed to the LLM.

Each function:
  - Has a docstring that the Ollama SDK converts to JSON schema.
  - Validates inputs BEFORE touching the database.
  - Returns a JSON string (str) that the LLM reads as the tool result.
  - Never trusts the LLM for arithmetic — recalculates server-side.

Tool registry (imported by ai_service.py):
    TOOLS = [
        search_customers, create_customer, get_customer_details,
        get_customers_details, edit_customer, create_order, search_orders,
        get_order_details, update_order_status,
        add_prescription,
        search_inventory, add_inventory_item, update_inventory_stock,
        get_dashboard_stats,
        create_user,
        navigate_to_page,
    ]
"""

import json
import re
import logging
from datetime import datetime, date, timedelta
from decimal import Decimal, ROUND_HALF_UP

from flask_login import current_user

from extensions import db
from models.customer import Customer
from models.order import Order, OrderItem, STATUS_CHOICES
from models.prescription import Prescription
from models.inventory import Inventory
from models.user import User
from models.sequence import get_next_number
from models.payment import Payment
from models.tax_config import TaxConfig

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _ok(data: dict | list) -> str:
    return json.dumps({"success": True, **data} if isinstance(data, dict) else {"success": True, "data": data})

def _err(message: str, **kwargs) -> str:
    return json.dumps({"success": False, "error": message, **kwargs})

def _parse_date(date_str: str | None) -> date | None:
    """Parse YYYY-MM-DD string or natural language date words."""
    if not date_str:
        return None
    date_str = date_str.strip().lower()
    today = date.today()
    if date_str in ("today", "aaj", "aaje"):
        return today
    if date_str in ("tomorrow", "kal", "aavti kal"):
        return today + timedelta(days=1)
    if date_str in ("day after tomorrow", "parso"):
        return today + timedelta(days=2)
    try:
        return datetime.strptime(date_str, "%Y-%m-%d").date()
    except ValueError:
        try:
            return datetime.strptime(date_str, "%d-%m-%Y").date()
        except ValueError:
            try:
                return datetime.strptime(date_str, "%d/%m/%Y").date()
            except ValueError:
                return None

def _normalize_phone(phone: str) -> str:
    """Strip non-digits except leading +, normalize +91 prefix."""
    clean = re.sub(r"[^\d+]", "", phone)
    if clean.startswith("+91"):
        clean = clean[3:]
    if clean.startswith("91") and len(clean) == 12:
        clean = clean[2:]
    return clean

def _valid_phone(phone: str) -> bool:
    return bool(re.match(r"^[6-9]\d{9}$", phone))

def _money(val) -> Decimal:
    """Safely convert to Decimal rounded to 2 dp."""
    try:
        return Decimal(str(val)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    except Exception:
        return Decimal("0.00")

def _money_input(val) -> Decimal | None:
    """Parse user supplied currency without silently turning bad input into zero."""
    try:
        amount = Decimal(str(val))
        if amount.is_finite():
            return amount.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    except Exception:
        pass
    return None

def _paid_amount_expression():
    """SQL expression matching Order.total_paid, including legacy advances."""
    from sqlalchemy import case, func
    receipt_totals = (
        db.session.query(Payment.order_id,
                         func.sum(Payment.amount).label('receipt_total'))
        .group_by(Payment.order_id).subquery()
    )
    advance = func.coalesce(Order.advance_amount, 0)
    receipts = func.coalesce(receipt_totals.c.receipt_total, 0)
    return receipt_totals, case((receipts > advance, receipts), else_=advance)

def _require_app_context():
    """Ensure we are inside a Flask application context."""
    from flask import current_app  # noqa: F401  (raises RuntimeError if not in context)


# ===========================================================================
# TOOL 1 — search_customers
# ===========================================================================

def search_customers(query: str, limit: int = 10) -> str:
    """
    Search for customers by name, phone number, or care_of field.
    Always call this BEFORE creating a customer to check for duplicates.

    Args:
        query: Search term — customer name, phone number, or care_of
        limit: Maximum number of results to return (default 10, max 50)

    Returns:
        JSON list of matching customers with id, name, phone, care_of, order_count
    """
    _require_app_context()
    try:
        limit = min(int(limit), 50)
        q = f"%{query.strip()}%"
        customers = (
            Customer.query
            .filter(
                db.or_(
                    Customer.name.ilike(q),
                    Customer.phone.ilike(q),
                    Customer.care_of.ilike(q),
                )
            )
            .order_by(Customer.updated_at.desc())
            .limit(limit)
            .all()
        )
        results = [
            {
                "id": c.id,
                "name": c.name,
                "phone": c.phone,
                "care_of": c.care_of or "",
                "order_count": len(c.orders),
            }
            for c in customers
        ]
        return _ok({"customers": results, "count": len(results)})
    except Exception as exc:
        log.exception("[Tool:search_customers]")
        return _err(str(exc))


# ===========================================================================
# TOOL 2 — create_customer
# ===========================================================================

def create_customer(name: str, phone: str, care_of: str = None) -> str:
    """
    Create a new customer in the system.
    ALWAYS call search_customers first to check for duplicates.

    Args:
        name: Full name of the customer (required, English)
        phone: 10-digit Indian mobile or landline number (required)
        care_of: Guardian / care-of person's name (optional)

    Returns:
        JSON with created customer details including id
    """
    _require_app_context()
    errors = []

    name = (name or "").strip()
    if len(name) < 2:
        errors.append("Customer name must be at least 2 characters.")

    raw_phone = (phone or "").strip()
    phone_clean = _normalize_phone(raw_phone)
    if not _valid_phone(phone_clean):
        return _err(
            f"'{raw_phone}' is not a valid 10-digit Indian mobile number. "
            "Please provide a number starting with 6-9."
        )

    if errors:
        return _err("; ".join(errors))

    try:
        # Duplicate check
        dupes = Customer.query.filter(
            db.or_(
                Customer.phone == phone_clean,
                Customer.name.ilike(name),
            )
        ).all()
        if dupes:
            return json.dumps({
                "success": False,
                "error": "Similar customers already exist. Please confirm you want to create a new one.",
                "duplicates": [{"id": c.id, "name": c.name, "phone": c.phone} for c in dupes],
                "action_required": "confirm_create",
            })

        new_c = Customer(name=name, phone=phone_clean, care_of=(care_of or "").strip() or None)
        db.session.add(new_c)
        db.session.commit()

        return _ok({
            "message": f"Customer '{name}' created successfully!",
            "customer": {"id": new_c.id, "name": new_c.name, "phone": new_c.phone, "care_of": new_c.care_of},
            "navigate_to": f"/customers/edit/{new_c.id}",
        })
    except Exception as exc:
        db.session.rollback()
        log.exception("[Tool:create_customer]")
        return _err(str(exc))


# ===========================================================================
# TOOL 3 — get_customer_details
# ===========================================================================

def get_customer_details(customer_id: int) -> str:
    """
    Get complete details of a customer including recent orders and prescriptions.

    Args:
        customer_id: The numeric ID of the customer

    Returns:
        JSON with customer details, last 5 orders, and last 3 prescriptions
    """
    _require_app_context()
    try:
        c = db.session.get(Customer, int(customer_id))
        if not c:
            return _err(f"Customer with ID {customer_id} not found.")

        recent_orders = [
            {
                "id": o.id,
                "order_no": o.order_no,
                "status": o.status,
                "total": float(o.total_amount or 0),
                "balance": float(o.balance_amount or 0),
                "issue_date": str(o.issue_date),
            }
            for o in c.orders[:5]
        ]
        recent_prescriptions = [
            {
                "id": p.id,
                "re_sph": float(p.re_sph or 0),
                "re_cyl": float(p.re_cyl or 0),
                "le_sph": float(p.le_sph or 0),
                "le_cyl": float(p.le_cyl or 0),
                "date": str(p.created_at.date()),
            }
            for p in c.prescriptions[:3]
        ]

        return _ok({
            "customer": {
                "id": c.id, "name": c.name, "phone": c.phone, "care_of": c.care_of,
                "total_orders": len(c.orders),
            },
            "recent_orders": recent_orders,
            "recent_prescriptions": recent_prescriptions,
            "navigate_to": f"/customers/edit/{c.id}",
        })
    except Exception as exc:
        log.exception("[Tool:get_customer_details]")
        return _err(str(exc))


# ===========================================================================
# TOOL 3B — get_customers_details
# ===========================================================================

def get_customers_details(customer_ids: list[int]) -> str:
    """
    Compare the profiles and account position of several existing customers.
    This is read-only. Use search_customers first when the numeric IDs are not
    known; do not use it to create or edit any customer.

    Args:
        customer_ids: List of 1 to 20 numeric customer IDs to retrieve

    Returns:
        JSON containing one compact profile per requested customer, including
        contact details, order count, total billed, due amount, credit limit,
        loyalty points, and a link to each profile.
    """
    _require_app_context()
    try:
        if not isinstance(customer_ids, list) or not customer_ids:
            return _err("customer_ids must be a non-empty list of customer IDs.")
        if len(customer_ids) > 20:
            return _err("Please request no more than 20 customers at once.")
        try:
            requested_ids = list(dict.fromkeys(int(customer_id) for customer_id in customer_ids))
        except (TypeError, ValueError):
            return _err("Every customer_id must be a numeric value.")

        customers = Customer.query.filter(Customer.id.in_(requested_ids)).all()
        by_id = {customer.id: customer for customer in customers}
        results = []
        for customer_id in requested_ids:
            customer = by_id.get(customer_id)
            if not customer:
                results.append({"id": customer_id, "found": False})
                continue
            orders = customer.orders or []
            total_billed = sum(float(order.total_amount or 0) for order in orders)
            total_due = sum(max(float(order.remaining_due or 0), 0) for order in orders)
            results.append({
                "id": customer.id,
                "found": True,
                "name": customer.name,
                "phone": customer.phone,
                "email": customer.email or "",
                "city": customer.city or "",
                "order_count": len(orders),
                "total_billed": round(total_billed, 2),
                "total_due": round(total_due, 2),
                "credit_limit": float(customer.credit_limit or 0),
                "loyalty_points": customer.loyalty_points or 0,
                "navigate_to": f"/customers/edit/{customer.id}",
            })
        return _ok({"customers": results, "count": len(results)})
    except Exception as exc:
        log.exception("[Tool:get_customers_details]")
        return _err(str(exc))


# ===========================================================================
# TOOL 4 — edit_customer
# ===========================================================================

def edit_customer(customer_id: int, name: str = None, phone: str = None, care_of: str = None) -> str:
    """
    Update an existing customer's details. Only provide fields you want to change.

    Args:
        customer_id: The numeric ID of the customer to update
        name: New name (optional)
        phone: New 10-digit phone number (optional)
        care_of: New care-of person name (optional)

    Returns:
        JSON with updated customer details
    """
    _require_app_context()
    try:
        c = db.session.get(Customer, int(customer_id))
        if not c:
            return _err(f"Customer with ID {customer_id} not found.")

        if name is not None:
            name = name.strip()
            if len(name) < 2:
                return _err("Name must be at least 2 characters.")
            c.name = name

        if phone is not None:
            phone_clean = _normalize_phone(phone)
            if not _valid_phone(phone_clean):
                return _err(f"'{phone}' is not a valid 10-digit Indian mobile number.")
            c.phone = phone_clean

        if care_of is not None:
            c.care_of = care_of.strip() or None

        db.session.commit()
        return _ok({
            "message": f"Customer '{c.name}' updated.",
            "customer": {"id": c.id, "name": c.name, "phone": c.phone, "care_of": c.care_of},
        })
    except Exception as exc:
        db.session.rollback()
        log.exception("[Tool:edit_customer]")
        return _err(str(exc))


# ===========================================================================
# TOOL 5 — create_order
# ===========================================================================

def create_order(
    customer_id: int,
    items: list,
    prescription_id: int = None,
    delivery_date: str = None,
    advance_amount: float = 0.0,
    discount: float = 0.0,
    status: str = "Pending",
    delivery_mode: str = "Self",
    tax_mode: str = "not_apply",
    tax_percent: float = 0.0,
) -> str:
    """
    Create a new order (bill) for a customer.

    Args:
        customer_id: Customer ID (required — search/create customer first)
        items: List of order line items. Each item must have:
               'description' (str), 'quantity' (int), 'unit_price' (float),
               and optional 'inventory_id' for stock-linked products.
        prescription_id: Existing prescription ID to link (optional)
        delivery_date: Expected delivery in YYYY-MM-DD, 'today', or 'tomorrow' (optional)
        advance_amount: Advance payment received (default 0.0)
        discount: Discount amount off the subtotal (default 0.0)
        status: one of the configured ERP order lifecycle statuses (default 'Pending')
        delivery_mode: 'Self' | 'Courier' | 'Home' (default 'Self')
        tax_mode: 'not_apply' | 'not_calculated' | 'calculated' (default 'not_apply')
        tax_percent: GST percentage when tax_mode is 'calculated' (0-100)

    Returns:
        JSON with order_no, total, advance, balance, and a link to view the order
    """
    _require_app_context()
    VALID_STATUSES = set(STATUS_CHOICES)
    VALID_MODES    = {"Self", "Courier", "Home"}
    VALID_TAX_MODES = {'not_apply', 'not_calculated', 'calculated'}

    try:
        customer = db.session.get(Customer, int(customer_id))
        if not customer:
            return _err(f"Customer ID {customer_id} not found. Search or create a customer first.")

        if not isinstance(items, list) or not items:
            return _err("An order needs at least one item. Please provide item details.")

        if prescription_id is not None:
            prescription = db.session.get(Prescription, int(prescription_id))
            if not prescription or prescription.customer_id != customer.id:
                return _err("Prescription must belong to the selected customer.")
        if delivery_date and not _parse_date(delivery_date):
            return _err("delivery_date must be a valid date or a supported date word.")

        if status not in VALID_STATUSES:
            return _err(f"Invalid status '{status}'. Choose from: {', '.join(VALID_STATUSES)}.")
        if delivery_mode not in VALID_MODES:
            return _err(f"Invalid delivery_mode '{delivery_mode}'. Choose from: {', '.join(VALID_MODES)}.")
        if tax_mode not in VALID_TAX_MODES:
            return _err(f"Invalid tax_mode '{tax_mode}'. Choose from: {', '.join(sorted(VALID_TAX_MODES))}.")
        tax_percent_dec = _money_input(tax_percent)
        if tax_percent_dec is None or not Decimal('0') <= tax_percent_dec <= Decimal('100'):
            return _err('tax_percent must be between 0 and 100.')
        if tax_mode != 'calculated':
            tax_percent_dec = Decimal('0.00')

        # Parse and validate items
        parsed_items = []
        subtotal = Decimal("0.00")
        for idx, item in enumerate(items, 1):
            if not isinstance(item, dict):
                return _err(f"Item {idx} must contain a description, quantity, and unit price.")
            desc  = str(item.get("description") or item.get("desc") or "").strip()
            qty   = int(item.get("quantity") or item.get("qty") or 0)
            price = _money_input(item.get("unit_price") or item.get("price") or 0)

            if not desc:
                return _err(f"Item {idx}: description is required.")
            if qty < 1:
                return _err(f"Item {idx} '{desc}': quantity must be at least 1.")
            if price is None or price <= 0:
                return _err(f"Item {idx} '{desc}': unit price must be greater than 0.")

            inventory_id = item.get('inventory_id') or item.get('inventoryId')
            if inventory_id is not None:
                try:
                    inventory_id = int(inventory_id)
                except (TypeError, ValueError):
                    return _err(f"Item {idx} '{desc}': inventory_id must be a number.")
                inventory_item = db.session.get(Inventory, inventory_id)
                if not inventory_item:
                    return _err(f"Item {idx} '{desc}': inventory item {inventory_id} was not found.")

            line_total = _money(qty) * price
            subtotal  += line_total
            parsed_items.append({"desc": desc, "qty": qty, "price": price, "inventory_id": inventory_id})

        discount_dec     = _money_input(discount)
        advance_dec      = _money_input(advance_amount)
        if discount_dec is None or discount_dec < 0 or discount_dec > subtotal:
            return _err("Discount must be between zero and the order subtotal.")
        if advance_dec is None or advance_dec < 0:
            return _err("Advance amount cannot be negative.")
        taxable_subtotal = max(subtotal - discount_dec, Decimal("0.00"))
        tax_amount = _money(taxable_subtotal * tax_percent_dec / Decimal('100')) if tax_mode == 'calculated' else Decimal('0.00')
        total_amount = taxable_subtotal + tax_amount
        balance          = total_amount - advance_dec

        if advance_dec > total_amount:
            return _err(
                f"Advance amount ({advance_dec}) cannot exceed order total ({total_amount})."
            )

        # Use the same transaction-backed sequence as the billing form.
        order_no = get_next_number('ORD')

        # Parse delivery date
        d_date = _parse_date(delivery_date)

        new_order = Order(
            order_no=order_no,
            customer_id=customer.id,
            prescription_id=prescription_id,
            status=status,
            delivery_mode=delivery_mode,
            issue_date=date.today(),
            delivery_date=d_date,
            advance_amount=advance_dec,
            discount=discount_dec,
            total_amount=total_amount,
            tax_mode=tax_mode,
            tax_percent=tax_percent_dec,
            tax_amount=tax_amount,
            created_by=_safe_user_id(),
        )
        db.session.add(new_order)
        db.session.flush()

        for itm in parsed_items:
            db.session.add(OrderItem(
                order_id=new_order.id,
                description=itm["desc"],
                quantity=itm["qty"],
                unit_price=itm["price"],
                inventory_id=itm['inventory_id'],
            ))

        if advance_dec > 0:
            db.session.add(Payment(
                order_id=new_order.id,
                receipt_no=get_next_number('RCP'),
                amount=advance_dec,
                payment_method='Cash',
                remark='Initial advance payment',
                payment_type='advance',
                created_by=_safe_user_id(),
            ))

        # Keep the delivery stock-out rule identical to the order form.  A
        # delivery can only be confirmed when linked items have enough stock.
        if status == 'Delivered':
            db.session.flush()
            stock_error = _delivery_stock_error(new_order)
            if stock_error:
                db.session.rollback()
                return _err(stock_error)
            from routes.order_routes import _deduct_stock
            _deduct_stock(new_order)

        db.session.commit()

        return _ok({
            "message": f"Order created for {customer.name}!",
            "order": {
                "id": new_order.id,
                "order_no": order_no,
                "customer": customer.name,
                "status": status,
                "subtotal": float(subtotal),
                "discount": float(discount_dec),
                "tax_mode": tax_mode,
                "tax_percent": float(tax_percent_dec),
                "tax_amount": float(tax_amount),
                "total": float(total_amount),
                "advance": float(advance_dec),
                "balance": float(balance),
                "items": [{"description": i["desc"], "quantity": i["qty"], "unit_price": float(i["price"]), "inventory_id": i['inventory_id']} for i in parsed_items],
            },
            "navigate_to": f"/orders/{new_order.id}",
        })
    except Exception as exc:
        db.session.rollback()
        log.exception("[Tool:create_order]")
        return _err(str(exc))


def _delivery_stock_error(order):
    """Return a readable shortage before the shared delivery helper mutates stock."""
    requested = {}
    for item in order.items:
        if item.inventory_id:
            requested[item.inventory_id] = requested.get(item.inventory_id, 0) + (item.quantity or 0)
    for inventory_id, quantity in requested.items():
        inventory = db.session.get(Inventory, inventory_id)
        if not inventory or (inventory.quantity or 0) < quantity:
            name = inventory.display_name if inventory else f'item #{inventory_id}'
            available = inventory.quantity if inventory else 0
            return f"Cannot mark delivered: {name} has {available} in stock, but {quantity} is required."
    return None

def _safe_user_id():
    try:
        if current_user and current_user.is_authenticated:
            return current_user.id
    except Exception:
        pass
    return None


# ===========================================================================
# TOOL 6 — search_orders
# ===========================================================================

def search_orders(
    customer_name: str = None,
    customer_id: int = None,
    order_no: str = None,
    status: str = None,
    from_date: str = None,
    to_date: str = None,
    due_only: bool = False,
    delivery_before: str = None,
    limit: int = 10,
) -> str:
    """
    Search bills/orders using customer, status, date, delivery, and due filters.
    This is read-only and is the preferred tool for a filtered bill list. Use
    get_order_details when the user asks to see one bill with its line items
    and payment receipts.

    Args:
        customer_name: Partial customer name to search for (optional)
        customer_id: Exact numeric customer ID (optional)
        order_no: Order number (partial match accepted) (optional)
        status: Exact order lifecycle status, for example 'Pending', 'Ready',
                or 'Delivered' (optional)
        from_date: Include bills issued on/after YYYY-MM-DD, 'today', or a
                   supported natural date word (optional)
        to_date: Include bills issued on/before YYYY-MM-DD, 'today', or a
                 supported natural date word (optional)
        due_only: If true, return only orders with a remaining unpaid amount
        delivery_before: Include orders due for delivery on/before this date
        limit: Maximum results (default 10, max 50)

    Returns:
        JSON list of matching orders with id, order_no, customer, status, total, balance
    """
    _require_app_context()
    try:
        limit = max(1, min(int(limit), 50))
        q = Order.query.join(Customer)

        parsed_from = _parse_date(from_date)
        parsed_to = _parse_date(to_date)
        parsed_delivery = _parse_date(delivery_before)
        if from_date and not parsed_from:
            return _err("from_date must be YYYY-MM-DD or a supported natural date such as 'today'.")
        if to_date and not parsed_to:
            return _err("to_date must be YYYY-MM-DD or a supported natural date such as 'today'.")
        if delivery_before and not parsed_delivery:
            return _err("delivery_before must be YYYY-MM-DD or a supported natural date such as 'today'.")
        if parsed_from and parsed_to and parsed_from > parsed_to:
            return _err("from_date cannot be after to_date.")

        if customer_name:
            q = q.filter(Customer.name.ilike(f"%{customer_name.strip()}%"))
        if customer_id is not None:
            q = q.filter(Order.customer_id == int(customer_id))
        if order_no:
            q = q.filter(Order.order_no.ilike(f"%{order_no.strip()}%"))
        if status:
            if status not in STATUS_CHOICES:
                return _err(f"Invalid status '{status}'. Choose from: {', '.join(STATUS_CHOICES)}.")
            q = q.filter(Order.status == status)
        if parsed_from:
            q = q.filter(Order.issue_date >= parsed_from)
        if parsed_to:
            q = q.filter(Order.issue_date <= parsed_to)
        if parsed_delivery:
            q = q.filter(Order.delivery_date.isnot(None), Order.delivery_date <= parsed_delivery)

        if due_only:
            receipt_totals, paid = _paid_amount_expression()
            q = (q.outerjoin(receipt_totals, Order.id == receipt_totals.c.order_id)
                 .filter(Order.total_amount > paid))
        orders = q.order_by(Order.created_at.desc()).limit(limit).all()

        results = [
            {
                "id": o.id,
                "order_no": o.order_no,
                "customer": o.customer.name if o.customer else "—",
                "status": o.status,
                "total": float(o.total_amount or 0),
                "advance": float(o.advance_amount or 0),
                "balance": float(o.balance_amount or 0),
                "paid": round(float(o.total_paid or 0), 2),
                "remaining_due": round(float(o.remaining_due or 0), 2),
                "issue_date": str(o.issue_date),
                "delivery_date": str(o.delivery_date) if o.delivery_date else None,
                "navigate_to": f"/orders/{o.id}",
            }
            for o in orders
        ]
        return _ok({"orders": results, "count": len(results)})
    except Exception as exc:
        log.exception("[Tool:search_orders]")
        return _err(str(exc))


# ===========================================================================
# TOOL 6B — get_order_details
# ===========================================================================

def get_order_details(order_id: int = None, order_no: str = None) -> str:
    """
    Show one complete bill/order, including customer contact details, every
    line item, GST/tax, status, delivery schedule, and payment receipts.
    This tool is read-only. Provide either order_id or order_no; order_no is
    useful when a customer reads their printed bill number aloud.

    Args:
        order_id: Exact numeric order ID (optional when order_no is supplied)
        order_no: Exact bill/order number, for example 'ORD-0001' (optional
                  when order_id is supplied)

    Returns:
        JSON bill data and a link to open the order in the ERP.
    """
    _require_app_context()
    if order_id is None and not (order_no or "").strip():
        return _err("Provide an order_id or an order_no to show a bill.")
    try:
        order = None
        if order_id is not None:
            order = db.session.get(Order, int(order_id))
        if order is None and order_id is None and (order_no or "").strip():
            order = Order.query.filter(db.func.lower(Order.order_no) == order_no.strip().lower()).first()
        if not order or (order_no and order.order_no.lower() != order_no.strip().lower()):
            return _err("Order was not found or its number did not match.")

        payments = [
            {
                "id": payment.id,
                "receipt_no": payment.receipt_no or "",
                "amount": float(payment.amount or 0),
                "method": payment.payment_method,
                "type": payment.payment_type or "",
                "remark": payment.remark or "",
                "date": payment.created_at.isoformat() if payment.created_at else None,
            }
            for payment in (order.payments or [])
        ]
        items = [
            {
                "id": item.id,
                "description": item.display_name,
                "quantity": item.quantity,
                "unit_price": float(item.unit_price or 0),
                "discount_percent": float(item.discount_percent or 0),
                "discount_amount": float(item.discount_amount or 0),
                "line_total": float(item.total_price or 0),
                "inventory_id": item.inventory_id,
            }
            for item in (order.items or [])
        ]
        customer = order.customer
        return _ok({
            "order": {
                "id": order.id,
                "order_no": order.order_no,
                "status": order.status,
                "issue_date": str(order.issue_date),
                "delivery_date": str(order.delivery_date) if order.delivery_date else None,
                "delivery_time": order.delivery_time.isoformat() if order.delivery_time else None,
                "delivery_in_days": order.delivery_in_days,
                "delivery_in_hours": order.delivery_in_hours,
                "delivery_mode": order.delivery_mode,
                "subtotal": round(sum(float(item.gross_total or 0) for item in order.items), 2),
                "discount": float(order.discount or 0),
                "tax_mode": order.tax_mode,
                "tax_percent": float(order.tax_percent or 0),
                "tax_amount": float(order.tax_amount or 0),
                "total": float(order.total_amount or 0),
                "advance": float(order.advance_amount or 0),
                "paid": round(float(order.total_paid or 0), 2),
                "remaining_due": round(float(order.remaining_due or 0), 2),
            },
            "customer": {
                "id": customer.id if customer else None,
                "name": customer.name if customer else "—",
                "phone": customer.phone if customer else "",
                "email": customer.email if customer else "",
            },
            "items": items,
            "payments": payments,
            "navigate_to": f"/orders/{order.id}",
        })
    except (TypeError, ValueError):
        return _err("order_id must be a numeric value.")
    except Exception as exc:
        log.exception("[Tool:get_order_details]")
        return _err(str(exc))


# ===========================================================================
# TOOL 7 — update_order_status
# ===========================================================================

def update_order_status(order_id: int, status: str) -> str:
    """
    Update the status of an existing order.

    Args:
        order_id: The numeric ID of the order to update
        status: New status — must be one of the configured ERP lifecycle statuses.

    Returns:
        JSON with updated order details
    """
    _require_app_context()
    VALID_STATUSES = set(STATUS_CHOICES)
    if status not in VALID_STATUSES:
        return _err(f"Invalid status '{status}'. Valid options: {', '.join(VALID_STATUSES)}.")

    try:
        order = db.session.get(Order, int(order_id))
        if not order:
            return _err(f"Order ID {order_id} not found.")

        old_status   = order.status
        if old_status == status:
            return _ok({
                "message": f"Order #{order.order_no} is already '{status}'.",
                "order_id": order.id, "order_no": order.order_no, "status": status,
                "navigate_to": f"/orders/{order.id}",
            })

        # Delivery is the only stock-out event.  Reuse the existing route
        # helpers so chat and the normal UI cannot drift apart.
        if status == 'Delivered':
            stock_error = _delivery_stock_error(order)
            if stock_error:
                return _err(stock_error)
            from routes.order_routes import _deduct_stock
            _deduct_stock(order)
        elif old_status == 'Delivered':
            from routes.order_routes import _restore_stock
            _restore_stock(order)
        order.status = status
        db.session.commit()

        return _ok({
            "message": f"Order #{order.order_no} status changed from '{old_status}' to '{status}'.",
            "order_id": order.id,
            "order_no": order.order_no,
            "status": status,
            "navigate_to": f"/orders/{order.id}",
        })
    except Exception as exc:
        db.session.rollback()
        log.exception("[Tool:update_order_status]")
        return _err(str(exc))


# ===========================================================================
# TOOL 8 — add_prescription
# ===========================================================================

def add_prescription(
    customer_id: int,
    re_sph: float = None,
    re_cyl: float = None,
    re_axis: int = None,
    le_sph: float = None,
    le_cyl: float = None,
    le_axis: int = None,
    addition: float = None,
    notes: str = None,
) -> str:
    """
    Add a new eye prescription for a customer.
    SPH range: -20.00 to +20.00 diopters.
    CYL range: -10.00 to +10.00 diopters.
    AXIS range: 0 to 180 degrees.
    Addition range: 0.50 to 4.00 diopters.

    Args:
        customer_id: Customer ID (required)
        re_sph: Right eye sphere power (optional)
        re_cyl: Right eye cylinder power (optional)
        re_axis: Right eye axis 0-180 (optional)
        le_sph: Left eye sphere power (optional)
        le_cyl: Left eye cylinder power (optional)
        le_axis: Left eye axis 0-180 (optional)
        addition: Near-vision addition power (optional)
        notes: Clinical or general notes (optional)

    Returns:
        JSON with created prescription details
    """
    _require_app_context()
    warnings = []

    try:
        customer = db.session.get(Customer, int(customer_id))
        if not customer:
            return _err(f"Customer ID {customer_id} not found.")

        # Validate ranges (soft warnings, not hard errors)
        def _check_sph(val, side):
            if val is not None and not (-20 <= float(val) <= 20):
                warnings.append(f"{side} SPH {val} is outside typical range (-20 to +20). Please verify.")

        def _check_cyl(val, side):
            if val is not None and not (-10 <= float(val) <= 10):
                warnings.append(f"{side} CYL {val} is outside typical range (-10 to +10). Please verify.")

        def _check_axis(val, side):
            if val is not None and not (0 <= int(val) <= 180):
                return _err(f"{side} AXIS {val} must be between 0 and 180 degrees.")
            return None

        _check_sph(re_sph, "Right eye")
        _check_sph(le_sph, "Left eye")
        _check_cyl(re_cyl, "Right eye")
        _check_cyl(le_cyl, "Left eye")

        axis_err = _check_axis(re_axis, "Right eye") or _check_axis(le_axis, "Left eye")
        if axis_err:
            return axis_err

        presc = Prescription(
            customer_id=customer.id,
            re_sph=re_sph, re_cyl=re_cyl, re_axis=re_axis,
            le_sph=le_sph, le_cyl=le_cyl, le_axis=le_axis,
            addition=addition,
            notes=notes,
            created_by=_safe_user_id(),
        )
        db.session.add(presc)
        db.session.commit()

        result = _ok({
            "message": f"Prescription added for {customer.name}.",
            "prescription_id": presc.id,
            "customer": {"id": customer.id, "name": customer.name},
            "values": {
                "RE": {"SPH": re_sph, "CYL": re_cyl, "AXIS": re_axis},
                "LE": {"SPH": le_sph, "CYL": le_cyl, "AXIS": le_axis},
                "Addition": addition,
            },
            "navigate_to": f"/prescriptions/history/{customer.id}",
        })

        if warnings:
            data = json.loads(result)
            data["warnings"] = warnings
            return json.dumps(data)
        return result

    except Exception as exc:
        db.session.rollback()
        log.exception("[Tool:add_prescription]")
        return _err(str(exc))


# ===========================================================================
# TOOL 9 — search_inventory
# ===========================================================================

def search_inventory(
    query: str = None,
    low_stock_only: bool = False,
    item_type: str = None,
    brand: str = None,
    location: str = None,
    in_stock_only: bool = False,
    limit: int = 15,
) -> str:
    """
    Search inventory items by model, brand, type, or storage location. Use
    filters to answer stock questions without changing any quantity.

    Args:
        query: Search term (model name, brand, frame type) — optional
        low_stock_only: If True, only return items at or below low-stock threshold
        item_type: Exact item class such as Frame, Lens, Contact Lens, or
                   Accessory (optional)
        brand: Partial brand name filter (optional)
        location: Partial rack/drawer/shelf location filter (optional)
        in_stock_only: If True, exclude zero and negative quantities
        limit: Maximum results (default 15, max 50)

    Returns:
        JSON list of inventory items with id, model, brand, quantity, selling_price, is_low_stock
    """
    _require_app_context()
    try:
        limit = max(1, min(int(limit), 50))
        q = Inventory.query

        if query:
            s = f"%{query.strip()}%"
            q = q.filter(
                db.or_(
                    Inventory.model_name.ilike(s),
                    Inventory.brand.ilike(s),
                    Inventory.frame_type.ilike(s),
                )
            )
        if low_stock_only:
            q = q.filter(Inventory.quantity <= Inventory.low_stock_threshold)
        if item_type:
            q = q.filter(db.func.lower(Inventory.item_type) == item_type.strip().lower())
        if brand:
            q = q.filter(Inventory.brand.ilike(f"%{brand.strip()}%"))
        if location:
            q = q.filter(Inventory.location.ilike(f"%{location.strip()}%"))
        if in_stock_only:
            q = q.filter(Inventory.quantity > 0)

        items = q.order_by(Inventory.quantity.asc()).limit(limit).all()

        results = [
            {
                "id": i.id,
                "model": i.model_name,
                "brand": i.brand or "",
                "frame_type": i.frame_type or "",
                "item_type": i.item_type or "",
                "barcode": i.barcode or "",
                "quantity": i.quantity,
                "location": i.location,
                "selling_price": float(i.selling_price or 0),
                "cost_price": float(i.cost_price or 0),
                "is_low_stock": i.is_low_stock,
                "low_stock_threshold": i.low_stock_threshold,
            }
            for i in items
        ]
        return _ok({"items": results, "count": len(results)})
    except Exception as exc:
        log.exception("[Tool:search_inventory]")
        return _err(str(exc))


# ===========================================================================
# TOOL 10 — add_inventory_item
# ===========================================================================

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
    color_stock: str = None,
) -> str:
    """
    Add a new item to the inventory.

    Args:
        model_name: Product model name (required)
        location: Storage location such as rack/drawer/shelf (required)
        cost_price: Purchase cost price (required, must be > 0)
        selling_price: Selling price (required, must be > 0)
        brand: Brand name (optional)
        frame_type: Type of frame e.g. 'Full Rim', 'Half Rim', 'Rimless' (optional)
        quantity: Initial stock count (default 0)
        shop_branch: Shop branch name (optional)
        low_stock_threshold: Alert when quantity <= this value (default 5)
        color_stock: Color-wise stock e.g. 'black-10, white-15' (optional)

    Returns:
        JSON with created inventory item details
    """
    _require_app_context()
    model_name = (model_name or "").strip()
    location   = (location or "").strip()

    if not model_name:
        return _err("Model name is required.")
    if not location:
        return _err("Location (rack/drawer/shelf) is required.")

    cost    = _money_input(cost_price)
    selling = _money_input(selling_price)

    if cost is None or cost <= 0:
        return _err("Cost price must be greater than 0.")
    if selling is None or selling <= 0:
        return _err("Selling price must be greater than 0.")

    warnings = []
    if cost > selling:
        warnings.append(f"Cost price ({cost}) is higher than selling price ({selling}). Please verify.")

    try:
        qty = int(quantity) if quantity is not None else 0
        threshold = int(low_stock_threshold)
    except (TypeError, ValueError):
        return _err("Quantity and low stock threshold must be whole numbers.")
    if qty < 0 or threshold < 0:
        return _err("Quantity and low stock threshold cannot be negative.")

    try:
        item = Inventory(
            model_name=model_name,
            brand=(brand or "").strip() or None,
            frame_type=(frame_type or "").strip() or None,
            quantity=qty,
            location=location,
            shop_branch=(shop_branch or "").strip() or None,
            cost_price=cost,
            selling_price=selling,
            low_stock_threshold=threshold,
            color_stock=(color_stock or "").strip() or None,
        )
        db.session.add(item)
        db.session.commit()

        result = _ok({
            "message": f"Inventory item '{model_name}' added!",
            "item": {
                "id": item.id,
                "model": item.model_name,
                "brand": item.brand,
                "quantity": item.quantity,
                "location": item.location,
                "cost_price": float(item.cost_price),
                "selling_price": float(item.selling_price),
            },
            "navigate_to": f"/inventory/edit/{item.id}",
        })

        if warnings:
            data = json.loads(result)
            data["warnings"] = warnings
            return json.dumps(data)
        return result

    except Exception as exc:
        db.session.rollback()
        log.exception("[Tool:add_inventory_item]")
        return _err(str(exc))


# ===========================================================================
# TOOL 11 — update_inventory_stock
# ===========================================================================

def update_inventory_stock(inventory_id: int, quantity_change: int, reason: str = None) -> str:
    """
    Increase or decrease the stock quantity of an inventory item.

    Args:
        inventory_id: The inventory item ID
        quantity_change: Positive to add stock, negative to reduce stock
        reason: Optional reason for the adjustment (for audit trail)

    Returns:
        JSON with updated item details and new quantity
    """
    _require_app_context()
    try:
        item = db.session.get(Inventory, int(inventory_id))
        if not item:
            return _err(f"Inventory item ID {inventory_id} not found.")

        change = int(quantity_change)
        if change == 0:
            return _err("Stock change must be nonzero.")
        new_qty = (item.quantity or 0) + change
        if new_qty < 0:
            return _err(
                f"Cannot reduce stock below zero. "
                f"Current stock: {item.quantity}, reduction requested: {abs(change)}."
            )

        old_qty      = item.quantity
        item.quantity = new_qty
        db.session.commit()

        direction = "added" if change > 0 else "removed"
        return _ok({
            "message": f"Stock {direction} for '{item.model_name}': {old_qty} → {new_qty}.",
            "item": {
                "id": item.id,
                "model": item.model_name,
                "old_quantity": old_qty,
                "quantity_change": change,
                "new_quantity": new_qty,
                "is_low_stock": item.is_low_stock,
            },
        })
    except Exception as exc:
        db.session.rollback()
        log.exception("[Tool:update_inventory_stock]")
        return _err(str(exc))


# ===========================================================================
# TOOL 12 — get_dashboard_stats
# ===========================================================================

def get_dashboard_stats() -> str:
    """
    Get current dashboard statistics: customer count, order counts,
    revenue, pending orders, and low-stock item count.

    Returns:
        JSON with all dashboard metrics
    """
    _require_app_context()
    try:
        from sqlalchemy import func as sqlfunc
        total_customers = Customer.query.count()
        total_orders    = Order.query.count()
        pending_orders  = Order.query.filter_by(status='Pending').count()
        delivered_orders = Order.query.filter_by(status='Delivered').count()
        low_stock       = Inventory.query.filter(
            Inventory.quantity <= Inventory.low_stock_threshold
        ).count()
        revenue_raw     = db.session.query(sqlfunc.sum(Order.total_amount)).scalar()
        total_revenue   = float(revenue_raw or 0)
        advance_raw     = db.session.query(sqlfunc.sum(Order.advance_amount)).scalar()
        total_advance   = float(advance_raw or 0)
        receipt_totals, paid = _paid_amount_expression()
        due_raw = (db.session.query(sqlfunc.sum(Order.total_amount - paid))
                   .outerjoin(receipt_totals, Order.id == receipt_totals.c.order_id).scalar())
        collected_raw = (db.session.query(sqlfunc.sum(paid))
                         .outerjoin(receipt_totals, Order.id == receipt_totals.c.order_id).scalar())

        return _ok({
            "stats": {
                "total_customers": total_customers,
                "total_orders": total_orders,
                "pending_orders": pending_orders,
                "delivered_orders": delivered_orders,
                "low_stock_items": low_stock,
                "total_revenue": round(total_revenue, 2),
                "total_advance_collected": round(total_advance, 2),
                "total_collected": round(float(collected_raw or 0), 2),
                "outstanding_balance": round(float(due_raw or 0), 2),
            }
        })
    except Exception as exc:
        log.exception("[Tool:get_dashboard_stats]")
        return _err(str(exc))


# ===========================================================================
# TOOL 13 — create_user (Admin only)
# ===========================================================================

def create_user(username: str, password: str, role: str = "staff", email: str = None) -> str:
    """
    Create a new ERP system user. Only admins can call this tool.

    Args:
        username: Unique login username (required)
        password: Password with minimum 6 characters (required)
        role: 'admin' or 'staff' (default 'staff')
        email: Email address for OTP password reset (optional)

    Returns:
        JSON with created user details (password is NOT returned)
    """
    _require_app_context()
    if not current_user.is_authenticated or not current_user.is_admin:
        return _err("Only admins can create new users.")

    username = (username or "").strip()
    if not username:
        return _err("Username is required.")
    if len(password or "") < 6:
        return _err("Password must be at least 6 characters.")
    role = (role or "staff").strip().lower()
    if role not in ("admin", "staff"):
        return _err("Role must be 'admin' or 'staff'.")

    try:
        if User.query.filter_by(username=username).first():
            return _err(f"Username '{username}' is already taken.")
        if email and User.query.filter_by(email=email).first():
            return _err(f"Email '{email}' is already registered.")

        from extensions import bcrypt as _bcrypt
        new_user = User(
            username=username,
            email=(email or "").strip() or None,
            password_hash=_bcrypt.generate_password_hash(password).decode('utf-8'),
            role=role,
        )
        db.session.add(new_user)
        db.session.commit()

        return _ok({
            "message": f"User '{username}' ({role}) created successfully.",
            "user": {"id": new_user.id, "username": new_user.username, "role": new_user.role, "email": new_user.email},
            "navigate_to": "/users",
        })
    except Exception as exc:
        db.session.rollback()
        log.exception("[Tool:create_user]")
        return _err(str(exc))




# ===========================================================================
# TOOL 14 — record_payment
# ===========================================================================

def record_payment(
    order_id: int = None,
    order_no: str = None,
    amount: float = 0.0,
    payment_method: str = "Cash",
    remark: str = None,
    payment_type: str = "partial",
) -> str:
    """
    Record a customer payment (Cash, UPI, Card, Paytm, Bank) against an order.
    Issues a sequential receipt number (RCP-XXXX) and recalculates the balance.

    Args:
        order_id: Exact numeric order ID (optional if order_no provided)
        order_no: Exact order number, for example 'ORD-0001' (optional if order_id provided)
        amount: Payment amount in Rupees (must be positive number)
        payment_method: Payment method: 'Cash', 'UPI', 'Card', 'Paytm', or 'Bank' (default 'Cash')
        remark: Optional transaction note or payment reference ID
        payment_type: Payment classification: 'advance', 'partial', or 'final' (default 'partial')

    Returns:
        JSON with receipt number, amount paid, updated remaining due, and receipt link
    """
    _require_app_context()
    if order_id is None and not (order_no or "").strip():
        return _err("Provide an order_id or order_no to record a payment.")

    try:
        amt = _money_input(amount)
        if amt is None or amt <= 0:
            return _err("Payment amount must be greater than zero.")

        order = None
        if order_id is not None:
            order = db.session.get(Order, int(order_id))
        else:
            order = Order.query.filter(db.func.lower(Order.order_no) == order_no.strip().lower()).first()

        if not order or (order_no and order.order_no.lower() != order_no.strip().lower()):
            return _err(f"Order '{order_id or order_no}' was not found or its number did not match.")

        rem_due = _money(order.remaining_due or 0)
        if amt > rem_due:
            return _err(
                f"Payment amount of ₹{amt:.2f} exceeds the remaining balance of ₹{rem_due:.2f}."
            )

        valid_methods = ("Cash", "UPI", "Card", "Paytm", "Bank")
        method_norm = payment_method.strip() if payment_method else "Cash"
        method_norm = next((method for method in valid_methods
                            if method.lower() == method_norm.lower()), None)
        if method_norm is None:
            return _err("Unsupported payment method. Choose Cash, UPI, Card, Paytm, or Bank.")

        receipt_number = get_next_number('RCP')
        user_id = None
        try:
            if current_user and current_user.is_authenticated:
                user_id = current_user.id
        except Exception:
            user_id = None

        payment = Payment(
            order_id=order.id,
            receipt_no=receipt_number,
            amount=amt,
            payment_method=method_norm,
            remark=(remark or "").strip() or None,
            payment_type=payment_type if payment_type in {"advance", "partial", "final"} else "partial",
            created_by=user_id,
        )
        db.session.add(payment)
        db.session.commit()

        new_due = float(order.remaining_due or 0.0)
        return _ok({
            "message": f"Payment of ₹{amt:.2f} recorded successfully via {method_norm} for order {order.order_no}.",
            "receipt_no": receipt_number,
            "order_no": order.order_no,
            "order_id": order.id,
            "amount_paid": float(amt),
            "remaining_due": new_due,
            "payment_method": method_norm,
            "navigate_to": f"/payments/receipt/{receipt_number}",
        })
    except Exception as exc:
        db.session.rollback()
        log.exception("[Tool:record_payment]")
        return _err(str(exc))


# ===========================================================================
# TOOL 15 — get_prescriptions
# ===========================================================================

def get_prescriptions(
    customer_id: int = None,
    prescription_id: int = None,
    limit: int = 5,
) -> str:
    """
    Look up clinical optical prescriptions and refraction values for a customer.
    Returns complete power measurements (RE/LE SPH, CYL, AXIS, VA, Addition, PD, Doctor, Notes).

    Args:
        customer_id: Customer numeric ID to view their prescription history (optional)
        prescription_id: Specific prescription record ID (optional)
        limit: Maximum number of prescriptions to return (default 5, max 20)

    Returns:
        JSON with detailed ophthalmic parameters for each prescription
    """
    _require_app_context()
    if customer_id is None and prescription_id is None:
        return _err("Provide either customer_id or prescription_id to view prescriptions.")

    try:
        limit = max(1, min(int(limit or 5), 20))
        prescriptions = []

        if prescription_id is not None:
            p = db.session.get(Prescription, int(prescription_id))
            if p:
                prescriptions.append(p)
            else:
                return _err(f"Prescription with ID {prescription_id} not found.")
        elif customer_id is not None:
            c = db.session.get(Customer, int(customer_id))
            if not c:
                return _err(f"Customer with ID {customer_id} not found.")
            prescriptions = (
                Prescription.query.filter_by(customer_id=c.id)
                .order_by(Prescription.created_at.desc())
                .limit(limit)
                .all()
            )

        if not prescriptions:
            return _ok({"prescriptions": [], "count": 0, "message": "No prescriptions found."})

        results = []
        for p in prescriptions:
            rx_date = str(p.created_at.date()) if p.created_at else None
            results.append({
                "id": p.id,
                "customer_id": p.customer_id,
                "customer_name": p.customer.name if p.customer else None,
                "prescription_date": rx_date,
                "doctor_name": getattr(p, 'referred_by', None) or "N/A",
                "right_eye": {
                    "sph": float(p.re_sph or 0),
                    "cyl": float(p.re_cyl or 0),
                    "axis": p.re_axis or 0,
                    "va": getattr(p, 're_visual_acuity', "") or "",
                    "addition": float(p.addition or 0) if getattr(p, 'addition', None) is not None else None,
                },
                "left_eye": {
                    "sph": float(p.le_sph or 0),
                    "cyl": float(p.le_cyl or 0),
                    "axis": p.le_axis or 0,
                    "va": getattr(p, 'le_visual_acuity', "") or "",
                    "addition": float(p.addition or 0) if getattr(p, 'addition', None) is not None else None,
                },
                "pd": float(p.pd_total or 0) if getattr(p, 'pd_total', None) is not None else None,
                "notes": p.notes or "",
                "navigate_to": f"/prescriptions/history/{p.customer_id}",
            })

        first_cid = prescriptions[0].customer_id
        return _ok({
            "prescriptions": results,
            "count": len(results),
            "navigate_to": f"/prescriptions/history/{first_cid}",
        })
    except Exception as exc:
        log.exception("[Tool:get_prescriptions]")
        return _err(str(exc))


# ===========================================================================
# TOOL 16 — get_tax_rates
# ===========================================================================

def get_tax_rates(active_only: bool = True) -> str:
    """
    Get configured GST tax rates and default tax configuration for billing.
    Useful to verify tax percentage when customer asks for GST invoice.

    Args:
        active_only: If true, return only currently active tax rates (default True)

    Returns:
        JSON list of configured tax rates and the default GST slab
    """
    _require_app_context()
    try:
        q = TaxConfig.query
        if active_only:
            q = q.filter_by(is_active=True)
        rates = q.order_by(TaxConfig.is_default.desc(), TaxConfig.rate.asc()).all()

        results = [
            {
                "id": r.id,
                "name": r.name,
                "rate_percent": float(r.rate),
                "is_default": r.is_default,
                "is_active": r.is_active,
            }
            for r in rates
        ]
        default_rate = next((r["rate_percent"] for r in results if r["is_default"]), 0.0)
        return _ok({
            "tax_rates": results,
            "default_rate_percent": default_rate,
            "count": len(results),
            "navigate_to": "/taxes/",
        })
    except Exception as exc:
        log.exception("[Tool:get_tax_rates]")
        return _err(str(exc))


# ===========================================================================
# TOOL 17 — get_low_stock_inventory
# ===========================================================================

def get_low_stock_inventory(threshold: int = 5, limit: int = 15) -> str:
    """
    List inventory frames, lenses, and optical goods that are running low on stock.
    Helps staff quickly reorder items before they run out.

    Args:
        threshold: Maximum quantity to consider low stock (default 5)
        limit: Maximum number of low stock items to return (default 15)

    Returns:
        JSON list of low stock items sorted by lowest quantity first
    """
    _require_app_context()
    try:
        th = max(0, int(threshold or 5))
        lim = max(1, min(int(limit or 15), 50))
        items = (
            Inventory.query
            .filter(Inventory.quantity <= th)
            .order_by(Inventory.quantity.asc(), Inventory.model_name.asc())
            .limit(lim)
            .all()
        )
        results = [
            {
                "id": i.id,
                "model_name": i.model_name,
                "brand": i.brand or "N/A",
                "quantity": i.quantity,
                "location": i.location or "N/A",
                "cost_price": float(i.cost_price or 0),
                "selling_price": float(i.selling_price or 0),
                "navigate_to": f"/inventory/edit/{i.id}",
            }
            for i in items
        ]
        return _ok({
            "low_stock_items": results,
            "threshold": th,
            "count": len(results),
            "navigate_to": "/inventory/",
        })
    except Exception as exc:
        log.exception("[Tool:get_low_stock_inventory]")
        return _err(str(exc))


# ===========================================================================
# TOOL 18 — get_order_document_links
# ===========================================================================

def get_order_document_links(order_id: int = None, order_no: str = None) -> str:
    """
    Get printable and viewable links for an order: Printable Tax Invoice,
    Workshop Job Slip, and Payment Receipts.

    Args:
        order_id: Exact numeric order ID (optional if order_no provided)
        order_no: Exact order number, for example 'ORD-0001' (optional if order_id provided)

    Returns:
        JSON with printable URLs for Invoice, Workshop Slip, and Payment Receipts
    """
    _require_app_context()
    if order_id is None and not (order_no or "").strip():
        return _err("Provide an order_id or order_no to get document links.")

    try:
        order = None
        if order_id is not None:
            order = db.session.get(Order, int(order_id))
        if order is None and order_id is None and (order_no or "").strip():
            order = Order.query.filter(db.func.lower(Order.order_no) == order_no.strip().lower()).first()

        if not order or (order_no and order.order_no.lower() != order_no.strip().lower()):
            return _err("Order was not found or its number did not match.")

        receipts = [
            {
                "receipt_no": p.receipt_no,
                "amount": float(p.amount),
                "url": f"/payments/receipt/{p.receipt_no}" if p.receipt_no else None,
            }
            for p in (order.payments or [])
            if p.receipt_no
        ]

        return _ok({
            "order_id": order.id,
            "order_no": order.order_no,
            "customer_name": order.customer.name if order.customer else None,
            "printable_invoice": f"/orders/invoice/{order.id}",
            "workshop_slip": f"/orders/workshop/{order.id}",
            "order_details": f"/orders/{order.id}",
            "receipts": receipts,
            "navigate_to": f"/orders/invoice/{order.id}",
        })
    except Exception as exc:
        log.exception("[Tool:get_order_document_links]")
        return _err(str(exc))


# ===========================================================================
# TOOL 14 — navigate_to_page
# ===========================================================================

# URL map for every navigation target
_NAV_MAP = {
    "dashboard":          "/dashboard",
    "customers":          "/customers/",
    "add_customer":       "/customers/add",
    "edit_customer":      "/customers/edit/{id}",
    "orders":             "/orders/",
    "new_order":          "/orders/new/{id}",
    "view_order":         "/orders/{id}",
    "edit_order":         "/orders/edit/{id}",
    "invoice":            "/orders/invoice/{id}",
    "workshop":           "/orders/workshop/{id}",
    "receipt":            "/payments/receipt/{id}",
    "taxes":              "/taxes/",
    "inventory":          "/inventory/",
    "add_inventory":      "/inventory/add",
    "edit_inventory":     "/inventory/edit/{id}",
    "prescriptions":      "/prescriptions/history/{id}",
    "add_prescription":   "/prescriptions/add/{id}",
    "users":              "/users",
    "audit_logs":         "/audit/",
    "deletion_requests":  "/deletion-requests/",
}
_NEEDS_ID = {k for k, v in _NAV_MAP.items() if "{id}" in v}


def navigate_to_page(page_name: str, entity_id: int = None) -> str:
    """
    Navigate the user's browser to a specific page in the ERP system.
    Use this for 'show me', 'go to', 'open', 'dikhao', 'juo' style requests.

    Args:
        page_name: Target page. One of:
                   dashboard, customers, add_customer, edit_customer,
                   orders, new_order, view_order, edit_order,
                   inventory, add_inventory, edit_inventory,
                   prescriptions, add_prescription,
                   users, audit_logs, deletion_requests
        entity_id: Required for pages that show/edit a specific record.
                   E.g. edit_customer needs the customer ID.

    Returns:
        JSON with the URL to navigate to (client JS handles the redirect)
    """
    page_name = (page_name or "").strip().lower()
    if page_name not in _NAV_MAP:
        known = ", ".join(sorted(_NAV_MAP.keys()))
        return _err(f"Unknown page '{page_name}'. Known pages: {known}.")

    if page_name in _NEEDS_ID:
        if entity_id is None:
            return _err(f"Page '{page_name}' requires an entity_id (e.g. customer ID or order ID).")
        url = _NAV_MAP[page_name].replace("{id}", str(int(entity_id)))
    else:
        url = _NAV_MAP[page_name]

    return _ok({"action": "navigate", "url": url, "page": page_name})


# ===========================================================================
# Approved deletion tools (the assistant service stages these before execution)
# ===========================================================================

def delete_customer(customer_id: int) -> str:
    """Permanently delete a customer and their related orders, payments and prescriptions.

    Args:
        customer_id: Exact customer ID to delete after the operator approves.

    Returns:
        JSON outcome with the deleted customer's name.
    """
    _require_app_context()
    if not current_user.is_authenticated or not current_user.is_admin:
        return _err('Only admins can delete customers.')
    customer = db.session.get(Customer, int(customer_id))
    if not customer:
        return _err('Customer no longer exists.')
    try:
        from services.order_deletion import delete_order_records
        for order in Order.query.filter_by(customer_id=customer.id).all():
            delete_order_records(order)
        # Flush order deletes before removing prescriptions referenced by them.
        db.session.flush()
        Prescription.query.filter_by(customer_id=customer.id).delete(synchronize_session=False)
        name = customer.name
        db.session.delete(customer)
        db.session.commit()
        return _ok({'message': f'Customer {name} and related records deleted.', 'customer_id': customer_id})
    except Exception as exc:
        db.session.rollback()
        log.exception('[Tool:delete_customer]')
        return _err(str(exc))


def delete_order(order_id: int = None, order_no: str = None,
                 approval_snapshot: str = None) -> str:
    """Permanently delete an exact order, its lines and payment receipts after approval.

    Only admins can delete orders. A delivered order's stock is restored. The
    assistant first shows an approval button; no deletion occurs before it is clicked.

    Args:
        order_id: Exact numeric order ID, if known.
        order_no: Exact bill/order number, for example ORD-0001, if known.
        approval_snapshot: Server-generated approval token for the reviewed order.

    Returns:
        JSON outcome with the deleted order number.
    """
    _require_app_context()
    if not current_user.is_authenticated or not current_user.is_admin:
        return _err('Only admins can delete orders.')
    if order_id is None and not (order_no or '').strip():
        return _err('Provide an exact order number or ID.')
    try:
        order = db.session.get(Order, int(order_id)) if order_id is not None else None
    except (TypeError, ValueError):
        return _err('The order ID must be a number.')
    if order is None and order_id is None:
        order = Order.query.filter(db.func.lower(Order.order_no) == order_no.strip().lower()).first()
    if not order or (order_no and order.order_no.lower() != order_no.strip().lower()):
        return _err('Order was not found or its number changed. No order was deleted.')
    try:
        from services.order_deletion import delete_order_records, order_deletion_fingerprint
        if not approval_snapshot or approval_snapshot != order_deletion_fingerprint(order):
            return _err('Order details changed after approval was requested. Review the order and ask to delete it again.')
        deleted_id, deleted_no = order.id, order.order_no
        delete_order_records(order)
        db.session.commit()
        return _ok({'message': f'Order #{deleted_no} deleted.', 'order_id': deleted_id,
                    'order_no': deleted_no})
    except Exception as exc:
        db.session.rollback()
        log.exception('[Tool:delete_order]')
        return _err(str(exc))


def delete_inventory_item(inventory_id: int) -> str:
    """Permanently delete an inventory item while preserving past order lines.

    Args:
        inventory_id: Exact inventory ID to delete after the operator approves.

    Returns:
        JSON outcome with the deleted item's name.
    """
    _require_app_context()
    if not current_user.is_authenticated or not current_user.is_admin:
        return _err('Only admins can delete inventory items.')
    item = db.session.get(Inventory, int(inventory_id))
    if not item:
        return _err('Inventory item no longer exists.')
    try:
        OrderItem.query.filter_by(inventory_id=item.id).update({'inventory_id': None}, synchronize_session=False)
        name = item.display_name
        db.session.delete(item)
        db.session.commit()
        return _ok({'message': f'Inventory item {name} deleted.', 'inventory_id': inventory_id})
    except Exception as exc:
        db.session.rollback()
        log.exception('[Tool:delete_inventory_item]')
        return _err(str(exc))


# ===========================================================================
# Tool registry — imported by ai_service.py
# ===========================================================================
TOOLS = [
    search_customers,
    create_customer,
    get_customer_details,
    get_customers_details,
    edit_customer,
    create_order,
    search_orders,
    get_order_details,
    update_order_status,
    record_payment,
    add_prescription,
    get_prescriptions,
    get_tax_rates,
    get_low_stock_inventory,
    get_order_document_links,
    search_inventory,
    add_inventory_item,
    update_inventory_stock,
    get_dashboard_stats,
    create_user,
    delete_customer,
    delete_order,
    delete_inventory_item,
    navigate_to_page,
]
