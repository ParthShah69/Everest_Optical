"""
ai_tools.py
===========
All 14 ERP tool functions exposed to the LLM.

Each function:
  - Has a docstring that the Ollama SDK converts to JSON schema.
  - Validates inputs BEFORE touching the database.
  - Returns a JSON string (str) that the LLM reads as the tool result.
  - Never trusts the LLM for arithmetic — recalculates server-side.

Tool registry (imported by ai_service.py):
    TOOLS = [
        search_customers, create_customer, get_customer_details, edit_customer,
        create_order, search_orders, update_order_status,
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
from models.order import Order, OrderItem
from models.prescription import Prescription
from models.inventory import Inventory
from models.user import User

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
) -> str:
    """
    Create a new order (bill) for a customer.

    Args:
        customer_id: Customer ID (required — search/create customer first)
        items: List of order line items. Each item must have:
               'description' (str), 'quantity' (int), 'unit_price' (float)
        prescription_id: Existing prescription ID to link (optional)
        delivery_date: Expected delivery in YYYY-MM-DD, 'today', or 'tomorrow' (optional)
        advance_amount: Advance payment received (default 0.0)
        discount: Discount amount off the subtotal (default 0.0)
        status: 'Pending' | 'Ready' | 'Delivered' (default 'Pending')
        delivery_mode: 'Self' | 'Courier' | 'Home' (default 'Self')

    Returns:
        JSON with order_no, total, advance, balance, and a link to view the order
    """
    _require_app_context()
    VALID_STATUSES = {"Pending", "Ready", "Delivered"}
    VALID_MODES    = {"Self", "Courier", "Home"}

    try:
        customer = db.session.get(Customer, int(customer_id))
        if not customer:
            return _err(f"Customer ID {customer_id} not found. Search or create a customer first.")

        if not items or len(items) == 0:
            return _err("An order needs at least one item. Please provide item details.")

        if status not in VALID_STATUSES:
            return _err(f"Invalid status '{status}'. Choose from: {', '.join(VALID_STATUSES)}.")
        if delivery_mode not in VALID_MODES:
            return _err(f"Invalid delivery_mode '{delivery_mode}'. Choose from: {', '.join(VALID_MODES)}.")

        # Parse and validate items
        parsed_items = []
        subtotal = Decimal("0.00")
        for idx, item in enumerate(items, 1):
            desc  = str(item.get("description") or item.get("desc") or "").strip()
            qty   = int(item.get("quantity") or item.get("qty") or 0)
            price = _money(item.get("unit_price") or item.get("price") or 0)

            if not desc:
                return _err(f"Item {idx}: description is required.")
            if qty < 1:
                return _err(f"Item {idx} '{desc}': quantity must be at least 1.")
            if price <= 0:
                return _err(f"Item {idx} '{desc}': unit price must be greater than 0.")

            line_total = _money(qty) * price
            subtotal  += line_total
            parsed_items.append({"desc": desc, "qty": qty, "price": price})

        discount_dec     = _money(discount)
        advance_dec      = _money(advance_amount)
        total_amount     = max(subtotal - discount_dec, Decimal("0.00"))
        balance          = total_amount - advance_dec

        if advance_dec > total_amount:
            return _err(
                f"Advance amount ({advance_dec}) cannot exceed order total ({total_amount})."
            )

        # Generate order number
        order_no = f"ORD-{datetime.utcnow().strftime('%Y%m%d')}-{_short_uid()}"

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
            ))

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
                "total": float(total_amount),
                "advance": float(advance_dec),
                "balance": float(balance),
                "items": [{"description": i["desc"], "quantity": i["qty"], "unit_price": float(i["price"])} for i in parsed_items],
            },
            "navigate_to": f"/orders/{new_order.id}",
        })
    except Exception as exc:
        db.session.rollback()
        log.exception("[Tool:create_order]")
        return _err(str(exc))


def _short_uid(length=6) -> str:
    import uuid
    return uuid.uuid4().hex[:length].upper()

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
    order_no: str = None,
    status: str = None,
    limit: int = 10,
) -> str:
    """
    Search orders by customer name, order number, or status filter.

    Args:
        customer_name: Partial customer name to search for (optional)
        order_no: Order number (partial match accepted) (optional)
        status: Filter by status — 'Pending', 'Ready', or 'Delivered' (optional)
        limit: Maximum results (default 10, max 50)

    Returns:
        JSON list of matching orders with id, order_no, customer, status, total, balance
    """
    _require_app_context()
    try:
        limit = min(int(limit), 50)
        q = Order.query.join(Customer)

        if customer_name:
            q = q.filter(Customer.name.ilike(f"%{customer_name.strip()}%"))
        if order_no:
            q = q.filter(Order.order_no.ilike(f"%{order_no.strip()}%"))
        if status:
            q = q.filter(Order.status == status)

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
                "issue_date": str(o.issue_date),
                "delivery_date": str(o.delivery_date) if o.delivery_date else None,
            }
            for o in orders
        ]
        return _ok({"orders": results, "count": len(results)})
    except Exception as exc:
        log.exception("[Tool:search_orders]")
        return _err(str(exc))


# ===========================================================================
# TOOL 7 — update_order_status
# ===========================================================================

def update_order_status(order_id: int, status: str) -> str:
    """
    Update the status of an existing order.

    Args:
        order_id: The numeric ID of the order to update
        status: New status — must be one of: 'Pending', 'Ready', 'Delivered'

    Returns:
        JSON with updated order details
    """
    _require_app_context()
    VALID_STATUSES = {"Pending", "Ready", "Delivered"}
    if status not in VALID_STATUSES:
        return _err(f"Invalid status '{status}'. Valid options: {', '.join(VALID_STATUSES)}.")

    try:
        order = db.session.get(Order, int(order_id))
        if not order:
            return _err(f"Order ID {order_id} not found.")

        old_status   = order.status
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
    limit: int = 15,
) -> str:
    """
    Search inventory items by model name, brand, or frame type.

    Args:
        query: Search term (model name, brand, frame type) — optional
        low_stock_only: If True, only return items at or below low-stock threshold
        limit: Maximum results (default 15, max 50)

    Returns:
        JSON list of inventory items with id, model, brand, quantity, selling_price, is_low_stock
    """
    _require_app_context()
    try:
        limit = min(int(limit), 50)
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

        items = q.order_by(Inventory.quantity.asc()).limit(limit).all()

        results = [
            {
                "id": i.id,
                "model": i.model_name,
                "brand": i.brand or "",
                "frame_type": i.frame_type or "",
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

    cost    = _money(cost_price)
    selling = _money(selling_price)

    if cost <= 0:
        return _err("Cost price must be greater than 0.")
    if selling <= 0:
        return _err("Selling price must be greater than 0.")

    warnings = []
    if cost > selling:
        warnings.append(f"Cost price ({cost}) is higher than selling price ({selling}). Please verify.")

    qty = int(quantity) if quantity is not None else 0

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
            low_stock_threshold=int(low_stock_threshold),
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

        new_qty = item.quantity + int(quantity_change)
        if new_qty < 0:
            return _err(
                f"Cannot reduce stock below zero. "
                f"Current stock: {item.quantity}, reduction requested: {abs(quantity_change)}."
            )

        old_qty      = item.quantity
        item.quantity = new_qty
        db.session.commit()

        direction = "added" if quantity_change > 0 else "removed"
        return _ok({
            "message": f"Stock {direction} for '{item.model_name}': {old_qty} → {new_qty}.",
            "item": {
                "id": item.id,
                "model": item.model_name,
                "old_quantity": old_qty,
                "quantity_change": quantity_change,
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

        return _ok({
            "stats": {
                "total_customers": total_customers,
                "total_orders": total_orders,
                "pending_orders": pending_orders,
                "delivered_orders": delivered_orders,
                "low_stock_items": low_stock,
                "total_revenue": round(total_revenue, 2),
                "total_advance_collected": round(total_advance, 2),
                "outstanding_balance": round(total_revenue - total_advance, 2),
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
    # Role check
    try:
        if not (current_user and current_user.is_authenticated and current_user.is_admin):
            return _err("Only admins can create new users.")
    except Exception:
        return _err("Could not verify admin privileges.")

    username = (username or "").strip()
    if not username:
        return _err("Username is required.")
    if len(password or "") < 6:
        return _err("Password must be at least 6 characters.")
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
# Tool registry — imported by ai_service.py
# ===========================================================================
TOOLS = [
    search_customers,
    create_customer,
    get_customer_details,
    edit_customer,
    create_order,
    search_orders,
    update_order_status,
    add_prescription,
    search_inventory,
    add_inventory_item,
    update_inventory_stock,
    get_dashboard_stats,
    create_user,
    navigate_to_page,
]
