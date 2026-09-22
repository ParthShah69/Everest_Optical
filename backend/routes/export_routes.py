"""Authenticated CSV exports for the main operational lists.

Exports deliberately use Python's standard library so they are available on
every deployment.  Values that spreadsheet programs may interpret as formulas
are escaped before writing the CSV.
"""

import csv
import io
from datetime import date, datetime

from flask import Blueprint, Response, request
from flask_login import login_required
from sqlalchemy.orm import joinedload, selectinload

from models.customer import Customer
from models.inventory import Inventory
from models.order import Order


export_bp = Blueprint('export', __name__, url_prefix='/exports')


def _safe_cell(value):
    """Return a spreadsheet-safe display value.

    Excel and similar tools evaluate cells starting with a formula prefix.  A
    leading apostrophe makes those values literal text while retaining their
    visible value when opened in a spreadsheet.
    """
    if value is None:
        return ''
    if isinstance(value, (date, datetime)):
        value = value.isoformat(sep=' ') if isinstance(value, datetime) else value.isoformat()
    value = str(value).replace('\x00', '')
    if value.lstrip().startswith(('=', '+', '-', '@')):
        return "'" + value
    return value


def _money(value):
    return f'{float(value or 0):.2f}'


def _csv_response(filename, headers, rows):
    output = io.StringIO(newline='')
    writer = csv.writer(output)
    writer.writerow(headers)
    for row in rows:
        writer.writerow([_safe_cell(value) for value in row])

    # utf-8-sig lets Microsoft Excel recognise non-Latin customer data without
    # making consumers that correctly respect the charset do any extra work.
    return Response(
        '\ufeff' + output.getvalue(),
        mimetype='text/csv',
        headers={'Content-Disposition': f'attachment; filename="{filename}"'},
        content_type='text/csv; charset=utf-8',
    )


@export_bp.route('/customers.csv')
@login_required
def customers_csv():
    search = request.args.get('search', '').strip()
    query = Customer.query
    if search:
        term = f'%{search}%'
        query = query.filter(
            Customer.name.ilike(term)
            | Customer.phone.ilike(term)
            | Customer.care_of.ilike(term)
            | Customer.email.ilike(term)
            | Customer.city.ilike(term)
        )
    customers = query.order_by(Customer.updated_at.desc()).all()
    rows = (
        (
            customer.id, customer.name, customer.care_of, customer.phone,
            customer.email, customer.address, customer.city, customer.state,
            customer.pincode, _money(customer.credit_limit),
            customer.loyalty_points or 0, customer.created_at,
        )
        for customer in customers
    )
    return _csv_response(
        'customers.csv',
        ('ID', 'Name', 'Care Of', 'Phone', 'Email', 'Address', 'City', 'State',
         'Pincode', 'Credit Limit', 'Loyalty Points', 'Created At'),
        rows,
    )


@export_bp.route('/orders.csv')
@login_required
def orders_csv():
    search = request.args.get('search', '').strip()
    status = request.args.get('status', '').strip()
    query = Order.query.options(joinedload(Order.customer), selectinload(Order.payments))
    if status:
        query = query.filter(Order.status == status)
    if search:
        term = f'%{search}%'
        query = query.join(Customer).filter(
            Order.order_no.ilike(term)
            | Customer.name.ilike(term)
            | Customer.phone.ilike(term)
        )
    orders = query.order_by(Order.created_at.desc()).all()
    rows = (
        (
            order.order_no, order.issue_date, order.customer.name if order.customer else '',
            order.customer.phone if order.customer else '', order.status,
            order.delivery_date, order.delivery_time, order.delivery_mode,
            _money(order.total_amount), _money(order.total_paid),
            _money(order.remaining_due), _money(order.discount), order.tax_mode,
            _money(order.tax_percent), _money(order.tax_amount), order.created_at,
        )
        for order in orders
    )
    return _csv_response(
        'orders.csv',
        ('Order Number', 'Order Date', 'Customer', 'Phone', 'Status',
         'Delivery Date', 'Delivery Time', 'Delivery Mode', 'Total', 'Paid',
         'Due', 'Discount', 'Tax Mode', 'Tax %', 'Tax Amount', 'Created At'),
        rows,
    )


@export_bp.route('/inventory.csv')
@login_required
def inventory_csv():
    search = request.args.get('search', '').strip()
    query = Inventory.query
    if search:
        term = f'%{search}%'
        query = query.filter(
            Inventory.model_name.ilike(term)
            | Inventory.brand.ilike(term)
            | Inventory.frame_type.ilike(term)
        )
    items = query.order_by(Inventory.quantity.asc(), Inventory.model_name.asc()).all()
    rows = (
        (
            item.id, item.barcode, item.model_name, item.brand, item.item_type,
            item.frame_type, item.quantity, item.location, item.shop_branch,
            item.color_stock, _money(item.cost_price), _money(item.selling_price),
            item.low_stock_threshold, 'Yes' if item.is_low_stock else 'No', item.updated_at,
        )
        for item in items
    )
    return _csv_response(
        'inventory.csv',
        ('ID', 'Barcode', 'Model Name', 'Brand', 'Item Type', 'Frame Type',
         'Quantity', 'Location', 'Branch', 'Colour Stock', 'Cost Price',
         'Selling Price', 'Low Stock Threshold', 'Low Stock', 'Updated At'),
        rows,
    )
