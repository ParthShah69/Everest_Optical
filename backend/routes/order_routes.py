from flask import Blueprint, render_template, redirect, url_for, flash, request
from flask_login import login_required, current_user
from extensions import db
from models.order import Order, OrderItem, ORDER_STATUSES, STATUS_CHOICES
from models.customer import Customer
from models.prescription import Prescription
from models.inventory import Inventory
from models.sequence import get_next_number
from models.tax_config import TaxConfig
from services.whatsapp_service import get_whatsapp_url, format_order_confirmation, format_ready_notification
from datetime import datetime, timedelta

order_bp = Blueprint('order', __name__, url_prefix='/orders')


def _delivery_schedule_from_form():
    """Read an optional relative delivery promise and derive its target date/time.

    Returns persisted relative values plus a date/time override, keeping the legacy
    manual delivery date/time workflow intact when no relative promise is entered.
    """
    def non_negative_int(name):
        raw_value = (request.form.get(name) or '').strip()
        if not raw_value:
            return None
        try:
            parsed_value = int(raw_value)
            if parsed_value < 0:
                raise ValueError
            return parsed_value
        except ValueError:
            raise ValueError(f'{name.replace("_", " ").title()} must be a non-negative whole number.')

    days = non_negative_int('delivery_in_days')
    hours = non_negative_int('delivery_in_hours')
    if not days and not hours:
        return days, hours, None, None

    scheduled_for = datetime.now() + timedelta(days=days or 0, hours=hours or 0)
    return days, hours, scheduled_for.date(), scheduled_for.time().replace(second=0, microsecond=0)

@order_bp.route('/')
@login_required
def index():
    page = request.args.get('page', 1, type=int)
    status_filter = request.args.get('status', '')
    search = request.args.get('search', '')
    
    query = Order.query
    
    if status_filter:
        query = query.filter_by(status=status_filter)
    
    if search:
        query = query.join(Customer).filter(
            (Order.order_no.ilike(f'%{search}%')) |
            (Customer.name.ilike(f'%{search}%')) |
            (Customer.phone.ilike(f'%{search}%'))
        )
    
    orders = query.order_by(Order.created_at.desc()).paginate(page=page, per_page=10)
    return render_template('orders/list.html', orders=orders, 
                          status_filter=status_filter, search=search,
                          all_statuses=STATUS_CHOICES)

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
            delivery_time_str = request.form.get('delivery_time')
            status = request.form.get('status', 'Pending')
            advance_amount = float(request.form.get('advance_amount', 0))
            delivery_mode = request.form.get('delivery_mode', 'Self')
            delivery_in_days, delivery_in_hours, scheduled_date, scheduled_time = _delivery_schedule_from_form()
            
            # Tax
            tax_mode = request.form.get('tax_mode', 'not_apply')
            tax_percent = float(request.form.get('tax_percent', 0))
            
            # Line Items with inventory linking
            descriptions = request.form.getlist('item_desc[]')
            quantities = request.form.getlist('quantity[]')
            prices = request.form.getlist('unit_price[]')
            inventory_ids = request.form.getlist('inventory_id[]')
            item_discount_percents = request.form.getlist('item_discount_percent[]')
            item_discount_amounts = request.form.getlist('item_discount_amount[]')
            
            subtotal = 0
            items_to_add = []
            stock_warnings = []
            
            for i in range(len(descriptions)):
                if descriptions[i]:
                    qty = int(quantities[i])
                    price = float(prices[i])
                    inv_id = int(inventory_ids[i]) if i < len(inventory_ids) and inventory_ids[i] else None
                    disc_pct = float(item_discount_percents[i]) if i < len(item_discount_percents) and item_discount_percents[i] else 0
                    disc_amt = float(item_discount_amounts[i]) if i < len(item_discount_amounts) and item_discount_amounts[i] else 0
                    
                    line_total = qty * price
                    if disc_amt > 0:
                        line_total -= disc_amt
                    elif disc_pct > 0:
                        line_total -= (line_total * disc_pct / 100)
                    
                    subtotal += line_total
                    
                    # Stock validation
                    if inv_id:
                        inv_item = Inventory.query.get(inv_id)
                        if inv_item and inv_item.quantity < qty:
                            stock_warnings.append(
                                f"⚠️ Low stock for '{inv_item.display_name}': Available {inv_item.quantity}, Requested {qty}"
                            )
                    
                    items_to_add.append({
                        'desc': descriptions[i],
                        'qty': qty,
                        'price': price,
                        'inv_id': inv_id,
                        'disc_pct': disc_pct,
                        'disc_amt': disc_amt,
                    })
            
            # Show stock warnings but still allow order creation
            for warning in stock_warnings:
                flash(warning, 'warning')
            
            # Calculate tax
            tax_amount = 0
            if tax_mode == 'calculated' and tax_percent > 0:
                tax_amount = subtotal * tax_percent / 100
            
            # Bill-level discount
            bill_discount = float(request.form.get('discount', 0))
            total_amount = subtotal - bill_discount + tax_amount
            if total_amount < 0:
                total_amount = 0
            
            # Generate Sequential Order No
            order_no = get_next_number('ORD')
            
            delivery_date = datetime.strptime(delivery_date_str, '%Y-%m-%d').date() if delivery_date_str else None
            delivery_time = datetime.strptime(delivery_time_str, '%H:%M').time() if delivery_time_str else None
            if scheduled_date:
                delivery_date, delivery_time = scheduled_date, scheduled_time

            new_order = Order(
                order_no=order_no,
                customer_id=customer_id,
                prescription_id=prescription_id,
                status=status,
                delivery_mode=delivery_mode,
                delivery_date=delivery_date,
                delivery_time=delivery_time,
                delivery_in_days=delivery_in_days,
                delivery_in_hours=delivery_in_hours,
                advance_amount=advance_amount,
                discount=bill_discount,
                total_amount=total_amount,
                tax_mode=tax_mode,
                tax_percent=tax_percent,
                tax_amount=tax_amount,
                created_by=current_user.id
            )
            
            db.session.add(new_order)
            db.session.flush() # Get ID
            
            for item in items_to_add:
                order_item = OrderItem(
                    order_id=new_order.id,
                    description=item['desc'],
                    quantity=item['qty'],
                    unit_price=item['price'],
                    inventory_id=item['inv_id'],
                    discount_percent=item['disc_pct'],
                    discount_amount=item['disc_amt'],
                )
                db.session.add(order_item)
                
            db.session.commit()
            flash('Order created successfully!', 'success')
            return redirect(url_for('order.view', id=new_order.id))
            
        except Exception as e:
            db.session.rollback()
            flash(f'Error creating order: {str(e)}', 'danger')

    tax_configs = TaxConfig.active().all()
    default_tax_config = next((config for config in tax_configs if config.is_default), None)
    return render_template('orders/new.html', customer=customer, prescriptions=prescriptions,
                          all_statuses=STATUS_CHOICES, tax_configs=tax_configs,
                          default_tax_rate=default_tax_config.rate if default_tax_config else 0)

@order_bp.route('/<int:id>')
@login_required
def view(id):
    order = Order.query.get_or_404(id)
    
    # Generate WhatsApp URLs
    whatsapp_confirm_url = ''
    whatsapp_ready_url = ''
    if order.customer and order.customer.phone:
        whatsapp_confirm_url = get_whatsapp_url(
            order.customer.phone, 
            format_order_confirmation(order)
        )
        whatsapp_ready_url = get_whatsapp_url(
            order.customer.phone,
            format_ready_notification(order)
        )
    
    return render_template('orders/view.html', order=order,
                          whatsapp_confirm_url=whatsapp_confirm_url,
                          whatsapp_ready_url=whatsapp_ready_url)


@order_bp.route('/<int:id>/print/invoice')
@login_required
def print_invoice(id):
    """Print-ready customer invoice with totals and payment summary."""
    return render_template('print/invoice.html', order=Order.query.get_or_404(id))


@order_bp.route('/<int:id>/print/job-slip')
@login_required
def print_job_slip(id):
    """Print-ready workshop instruction slip; intentionally excludes prices."""
    return render_template('print/job_slip.html', order=Order.query.get_or_404(id))

@order_bp.route('/edit/<int:id>', methods=['GET', 'POST'])
@login_required
def edit(id):
    order = Order.query.get_or_404(id)
    old_status = order.status
    
    if request.method == 'POST':
        try:
            # 1. Update Basic Fields
            order.status = request.form.get('status')
            order.delivery_mode = request.form.get('delivery_mode')
            order.advance_amount = float(request.form.get('advance_amount', 0))
            order.discount = float(request.form.get('discount', 0))
            delivery_in_days, delivery_in_hours, scheduled_date, scheduled_time = _delivery_schedule_from_form()
            order.delivery_in_days = delivery_in_days
            order.delivery_in_hours = delivery_in_hours
            
            # Tax
            order.tax_mode = request.form.get('tax_mode', 'not_apply')
            order.tax_percent = float(request.form.get('tax_percent', 0))
            
            date_str = request.form.get('delivery_date')
            if date_str:
                order.delivery_date = datetime.strptime(date_str, '%Y-%m-%d').date()

            time_str = request.form.get('delivery_time')
            if time_str:
                order.delivery_time = datetime.strptime(time_str, '%H:%M').time()

            if scheduled_date:
                order.delivery_date, order.delivery_time = scheduled_date, scheduled_time

            # 2. Re-create Line Items (Delete Old -> Add New)
            OrderItem.query.filter_by(order_id=order.id).delete()
            
            descriptions = request.form.getlist('item_desc[]')
            quantities = request.form.getlist('quantity[]')
            prices = request.form.getlist('unit_price[]')
            inventory_ids = request.form.getlist('inventory_id[]')
            item_discount_percents = request.form.getlist('item_discount_percent[]')
            item_discount_amounts = request.form.getlist('item_discount_amount[]')
            
            subtotal = 0
            
            for i in range(len(descriptions)):
                if descriptions[i]:
                    qty = int(quantities[i])
                    price = float(prices[i])
                    inv_id = int(inventory_ids[i]) if i < len(inventory_ids) and inventory_ids[i] else None
                    disc_pct = float(item_discount_percents[i]) if i < len(item_discount_percents) and item_discount_percents[i] else 0
                    disc_amt = float(item_discount_amounts[i]) if i < len(item_discount_amounts) and item_discount_amounts[i] else 0
                    
                    line_total = qty * price
                    if disc_amt > 0:
                        line_total -= disc_amt
                    elif disc_pct > 0:
                        line_total -= (line_total * disc_pct / 100)
                    subtotal += line_total

                    new_item = OrderItem(
                        order_id=order.id,
                        description=descriptions[i],
                        quantity=qty,
                        unit_price=price,
                        inventory_id=inv_id,
                        discount_percent=disc_pct,
                        discount_amount=disc_amt,
                    )
                    db.session.add(new_item)

            # 3. Recalculate Tax
            tax_amount = 0
            if order.tax_mode == 'calculated' and order.tax_percent and float(order.tax_percent) > 0:
                tax_amount = subtotal * float(order.tax_percent) / 100
            order.tax_amount = tax_amount
            
            # 4. Recalculate Total
            total_amount = subtotal - float(order.discount or 0) + tax_amount
            if total_amount < 0:
                total_amount = 0
            order.total_amount = total_amount
            
            # 5. Stock deduction on status change to Delivered
            if order.status == 'Delivered' and old_status != 'Delivered':
                _deduct_stock(order)
            
            db.session.commit()
            flash('Order updated successfully!', 'success')
            return redirect(url_for('order.view', id=order.id))
            
        except Exception as e:
            db.session.rollback()
            flash(f'Error updating order: {str(e)}', 'danger')

    return render_template('orders/edit.html', order=order, all_statuses=STATUS_CHOICES,
                          tax_configs=TaxConfig.active().all())


def _deduct_stock(order):
    """Deduct inventory stock when order is delivered."""
    for item in order.items:
        if item.inventory_id:
            inv = Inventory.query.get(item.inventory_id)
            if inv:
                inv.quantity = max(0, inv.quantity - item.quantity)


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
