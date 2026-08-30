from flask import Blueprint, render_template, redirect, url_for, flash, request
from flask_login import login_required, current_user
from extensions import db
from models.inventory import Inventory

inventory_bp = Blueprint('inventory', __name__, url_prefix='/inventory')

@inventory_bp.route('/')
@login_required
def index():
    page = request.args.get('page', 1, type=int)
    search = request.args.get('search', '')
    
    query = Inventory.query
    
    if search:
        query = query.filter(
            (Inventory.model_name.ilike(f'%{search}%')) |
            (Inventory.brand.ilike(f'%{search}%')) |
            (Inventory.frame_type.ilike(f'%{search}%'))
        )
    
    # Sort by low stock first, then name
    # We can't easily sort by property 'is_low_stock' in SQL, so we'll just sort by updated_at for now
    # or we can do quantity ASC to show low stock first.
    items = query.order_by(Inventory.quantity.asc()).paginate(page=page, per_page=15)
    
    return render_template('inventory/list.html', items=items, search=search)

import os
import uuid
from werkzeug.utils import secure_filename

ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg', 'webp', 'gif'}

def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

def save_inventory_image(file):
    if file and file.filename != '' and allowed_file(file.filename):
        ext = file.filename.rsplit('.', 1)[1].lower()
        filename = secure_filename(f"inv_{uuid.uuid4().hex[:8]}.{ext}")
        save_dir = os.path.join('static', 'uploads', 'inventory')
        os.makedirs(save_dir, exist_ok=True)
        filepath = os.path.join(save_dir, filename)
        file.save(filepath)
        return filepath.replace('\\', '/')
    return None

@inventory_bp.route('/add', methods=['GET', 'POST'])
@login_required
def add():
    if request.method == 'POST':
        try:
            image_path = None
            if 'image' in request.files:
                image_path = save_inventory_image(request.files['image'])

            new_item = Inventory(
                model_name=request.form.get('model_name'),
                brand=request.form.get('brand'),
                frame_type=request.form.get('frame_type'),
                quantity=int(request.form.get('quantity', 0)),
                location=request.form.get('location'),
                shop_branch=request.form.get('shop_branch'),
                cost_price=float(request.form.get('cost_price', 0)),
                selling_price=float(request.form.get('selling_price', 0)),
                low_stock_threshold=int(request.form.get('low_stock_threshold', 5)),
                color_stock=request.form.get('color_stock') or None,
                image_path=image_path
            )
            db.session.add(new_item)
            db.session.commit()
            flash('Item added to inventory!', 'success')
            return redirect(url_for('inventory.index'))
        except Exception as e:
            db.session.rollback()
            flash(f'Error adding item: {str(e)}', 'danger')

    return render_template('inventory/add.html')

@inventory_bp.route('/edit/<int:id>', methods=['GET', 'POST'])
@login_required
def edit(id):
    item = Inventory.query.get_or_404(id)
    
    if request.method == 'POST':
        try:
            item.model_name = request.form.get('model_name')
            item.brand = request.form.get('brand')
            item.frame_type = request.form.get('frame_type')
            item.quantity = int(request.form.get('quantity', 0))
            item.location = request.form.get('location')
            item.shop_branch = request.form.get('shop_branch')
            item.cost_price = float(request.form.get('cost_price', 0))
            item.selling_price = float(request.form.get('selling_price', 0))
            item.low_stock_threshold = int(request.form.get('low_stock_threshold', 5))
            item.color_stock = request.form.get('color_stock') or None
            
            if 'image' in request.files:
                new_image = save_inventory_image(request.files['image'])
                if new_image:
                    item.image_path = new_image

            db.session.commit()
            flash('Inventory updated!', 'success')
            return redirect(url_for('inventory.index'))
        except Exception as e:
            db.session.rollback()
            flash(f'Error updating item: {str(e)}', 'danger')

    return render_template('inventory/edit.html', item=item)

@inventory_bp.route('/delete/<int:id>', methods=['POST'])
@login_required
def delete(id):
    item = Inventory.query.get_or_404(id)
    item_identifier = f"{item.brand or ''} {item.model_name}".strip()

    if current_user.is_admin:
        try:
            db.session.delete(item)
            db.session.commit()
            flash(f'Inventory item "{item_identifier}" deleted permanently.', 'success')
        except Exception as e:
            db.session.rollback()
            flash(f'Error deleting inventory item: {str(e)}', 'danger')
    else:
        reason = request.form.get('reason', '').strip()
        if not reason:
            flash('Please provide a reason for deletion.', 'warning')
            return redirect(url_for('inventory.index'))

        from models.deletion_request import DeletionRequest
        existing_req = DeletionRequest.query.filter_by(
            entity_type='inventory', entity_id=item.id, status='Pending'
        ).first()

        if existing_req:
            flash(f'A deletion request for item "{item_identifier}" is already pending admin review.', 'info')
            return redirect(url_for('inventory.index'))

        del_req = DeletionRequest(
            entity_type='inventory',
            entity_id=item.id,
            entity_identifier=f"Item: {item_identifier} (Qty: {item.quantity})",
            reason=reason,
            requested_by_id=current_user.id
        )
        try:
            db.session.add(del_req)
            db.session.commit()
            flash(f'Deletion request for inventory item "{item_identifier}" submitted to admin.', 'success')
        except Exception as e:
            db.session.rollback()
            flash(f'Error submitting deletion request: {str(e)}', 'danger')

    return redirect(url_for('inventory.index'))

