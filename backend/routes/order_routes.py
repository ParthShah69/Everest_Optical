from flask import Blueprint, render_template, redirect, url_for, flash, request
from flask_login import login_required, current_user
from extensions import db
from models.order import Order, OrderItem
from models.customer import Customer
from models.prescription import Prescription
import uuid
from datetime import datetime

order_bp = Blueprint('order', __name__, url_prefix='/orders')

@order_bp.route('/')
@login_required
def index():
    page = request.args.get('page', 1, type=int)
    orders = Order.query.order_by(Order.created_at.desc()).paginate(page=page, per_page=10)
    return render_template('orders/list.html', orders=orders)

@order_bp.route('/new/<int:customer_id>', methods=['GET', 'POST'])
@login_required
def new(customer_id):
    customer = Customer.query.get_or_404(customer_id)
    # Get recent prescriptions to link
    prescriptions = Prescription.query.filter_by(customer_id=customer_id).order_by(Prescription.created_at.desc()).limit(5).all()
    
    if request.method == 'POST':
        try:
            # Form Data
            prescription_id = request.form.get('prescription_id') or None
            delivery_date_str = request.form.get('delivery_date')
            status = request.form.get('status', 'Pending')
            advance_amount = float(request.form.get('advance_amount', 0))
            
            # Line Items (Manual parsing for now)
            # Expecting arrays: item_desc[], quantity[], unit_price[]
            descriptions = request.form.getlist('item_desc[]')
            quantities = request.form.getlist('quantity[]')
            prices = request.form.getlist('unit_price[]')
            
            total_amount = 0
            items_to_add = []
            
            for i in range(len(descriptions)):
                if descriptions[i]: # If description exists
                    qty = int(quantities[i])
                    price = float(prices[i])
                    total_amount += (qty * price)
                    
                    items_to_add.append({
                        'desc': descriptions[i],
                        'qty': qty,
                        'price': price
                    })
            
            # Generate Order No
            order_no = f"ORD-{datetime.utcnow().strftime('%Y%m%d')}-{uuid.uuid4().hex[:6].upper()}"
            
            delivery_date = datetime.strptime(delivery_date_str, '%Y-%m-%d').date() if delivery_date_str else None

            new_order = Order(
                order_no=order_no,
                customer_id=customer_id,
                prescription_id=prescription_id,
                status=status,
                delivery_date=delivery_date,
                advance_amount=advance_amount,
                total_amount=total_amount,
                created_by=current_user.id
            )
            
            db.session.add(new_order)
            db.session.flush() # Get ID
            
            for item in items_to_add:
                order_item = OrderItem(
                    order_id=new_order.id,
                    description=item['desc'],
                    quantity=item['qty'],
                    unit_price=item['price']
                )
                db.session.add(order_item)
                
            db.session.commit()
            flash('Order created successfully!', 'success')
            return redirect(url_for('order.view', id=new_order.id))
            
        except Exception as e:
            db.session.rollback()
            flash(f'Error creating order: {str(e)}', 'danger')

    return render_template('orders/new.html', customer=customer, prescriptions=prescriptions)

@order_bp.route('/<int:id>')
@login_required
def view(id):
    order = Order.query.get_or_404(id)
    return render_template('orders/view.html', order=order)

@order_bp.route('/edit/<int:id>', methods=['GET', 'POST'])
@login_required
def edit(id):
    order = Order.query.get_or_404(id)
    
    if request.method == 'POST':
        try:
            # 1. Update Basic Fields
            order.status = request.form.get('status')
            order.delivery_mode = request.form.get('delivery_mode')
            order.advance_amount = float(request.form.get('advance_amount', 0))
            order.discount = float(request.form.get('discount', 0))
            
            date_str = request.form.get('delivery_date')
            if date_str:
                order.delivery_date = datetime.strptime(date_str, '%Y-%m-%d').date()

            # 2. Re-create Line Items (Delete Old -> Add New)
            # Remove existing items properly
            OrderItem.query.filter_by(order_id=order.id).delete()
            
            # Parse new items
            descriptions = request.form.getlist('item_desc[]')
            quantities = request.form.getlist('quantity[]')
            prices = request.form.getlist('unit_price[]')
            
            subtotal = 0
            
            for i in range(len(descriptions)):
                if descriptions[i]:
                    qty = int(quantities[i])
                    price = float(prices[i])
                    subtotal += (qty * price)

                    new_item = OrderItem(
                        order_id=order.id,
                        description=descriptions[i],
                        quantity=qty,
                        unit_price=price
                    )
                    db.session.add(new_item)

            # 3. Recalculate Total
            total_amount = subtotal - order.discount
            if total_amount < 0: total_amount = 0
            order.total_amount = total_amount
            
            db.session.commit()
            flash('Order updated successfully!', 'success')
            return redirect(url_for('order.view', id=order.id))
            
        except Exception as e:
            db.session.rollback()
            flash(f'Error updating order: {str(e)}', 'danger')

    return render_template('orders/edit.html', order=order)

@order_bp.route('/delete/<int:id>', methods=['POST'])
@login_required
def delete(id):
    order = Order.query.get_or_404(id)
    customer_name = order.customer.name if order.customer else "Unknown"
    
    if current_user.is_admin:
        try:
            db.session.delete(order)
            db.session.commit()
            flash(f'Order #{order.order_no} deleted permanently.', 'success')
        except Exception as e:
            db.session.rollback()
            flash(f'Error deleting order: {str(e)}', 'danger')
    else:
        reason = request.form.get('reason', '').strip()
        if not reason:
            flash('Please provide a reason for deletion.', 'warning')
            return redirect(url_for('order.index'))
            
        from models.deletion_request import DeletionRequest
        
        existing_req = DeletionRequest.query.filter_by(
            entity_type='order', entity_id=order.id, status='Pending'
        ).first()
        
        if existing_req:
            flash(f'A deletion request for Order #{order.order_no} is already pending admin review.', 'info')
            return redirect(url_for('order.index'))

        del_req = DeletionRequest(
            entity_type='order',
            entity_id=order.id,
            entity_identifier=f"Order #{order.order_no} (Customer: {customer_name})",
            reason=reason,
            requested_by_id=current_user.id
        )
        try:
            db.session.add(del_req)
            db.session.commit()
            flash(f'Deletion request for Order #{order.order_no} submitted to admin.', 'success')
        except Exception as e:
            db.session.rollback()
            flash(f'Error submitting deletion request: {str(e)}', 'danger')
            
    return redirect(url_for('order.index'))

