from flask import Blueprint, request, jsonify
from flask_login import login_required
from models.customer import Customer
from models.order import Order
from models.inventory import Inventory

search_bp = Blueprint('search', __name__, url_prefix='/api')


@search_bp.route('/search')
@login_required
def global_search():
    """Global smart search — searches customers by name/phone, orders by order_no."""
    q = request.args.get('q', '').strip()
    if not q or len(q) < 2:
        return jsonify({'customers': [], 'orders': []})
    
    search_filter = f"%{q}%"
    
    # Search customers
    customers = Customer.query.filter(
        (Customer.name.ilike(search_filter)) |
        (Customer.phone.ilike(search_filter)) |
        (Customer.care_of.ilike(search_filter))
    ).limit(5).all()
    
    # Search orders
    orders = Order.query.filter(
        Order.order_no.ilike(search_filter)
    ).limit(5).all()
    
    return jsonify({
        'customers': [{
            'id': c.id,
            'name': c.name,
            'phone': c.phone,
            'care_of': c.care_of or ''
        } for c in customers],
        'orders': [{
            'id': o.id,
            'order_no': o.order_no,
            'customer_name': o.customer.name if o.customer else '',
            'status': o.status,
            'total': float(o.total_amount or 0)
        } for o in orders]
    })


@search_bp.route('/inventory/search')
@login_required
def inventory_search():
    """Search inventory items for order billing autocomplete."""
    q = request.args.get('q', '').strip()
    if not q or len(q) < 1:
        return jsonify([])
    
    search_filter = f"%{q}%"
    
    items = Inventory.query.filter(
        (Inventory.model_name.ilike(search_filter)) |
        (Inventory.brand.ilike(search_filter)) |
        (Inventory.barcode.ilike(search_filter))
    ).limit(10).all()
    
    return jsonify([{
        'id': i.id,
        'name': i.display_name,
        'model_name': i.model_name,
        'brand': i.brand or '',
        'barcode': i.barcode or '',
        'selling_price': float(i.selling_price),
        'cost_price': float(i.cost_price),
        'quantity': i.quantity,
        'is_low_stock': i.is_low_stock,
        'item_type': i.item_type or 'Frame'
    } for i in items])
