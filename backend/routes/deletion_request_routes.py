from flask import Blueprint, render_template, redirect, url_for, flash, request
from flask_login import login_required, current_user
from datetime import datetime
from extensions import db
from models.deletion_request import DeletionRequest
from models.customer import Customer
from models.order import Order
from models.prescription import Prescription

deletion_request_bp = Blueprint('deletion_request', __name__, url_prefix='/deletion-requests')

def admin_required(func):
    """Decorator to enforce admin access."""
    from functools import wraps
    @wraps(func)
    def decorated_view(*args, **kwargs):
        if not current_user.is_authenticated or not current_user.is_admin:
            flash('Access denied. Admin rights required.', 'danger')
            return redirect(url_for('dashboard.index'))
        return func(*args, **kwargs)
    return decorated_view

@deletion_request_bp.route('/')
@login_required
@admin_required
def index():
    status_filter = request.args.get('status', 'Pending')
    page = request.args.get('page', 1, type=int)

    query = DeletionRequest.query
    if status_filter != 'all':
        query = query.filter_by(status=status_filter)

    requests = query.order_by(DeletionRequest.created_at.desc()).paginate(page=page, per_page=15)
    
    # Counts for tab badges
    pending_count = DeletionRequest.query.filter_by(status='Pending').count()
    approved_count = DeletionRequest.query.filter_by(status='Approved').count()
    rejected_count = DeletionRequest.query.filter_by(status='Rejected').count()
    total_count = DeletionRequest.query.count()

    return render_template(
        'deletion_requests/list.html',
        requests=requests,
        status_filter=status_filter,
        pending_count=pending_count,
        approved_count=approved_count,
        rejected_count=rejected_count,
        total_count=total_count
    )

@deletion_request_bp.route('/<int:id>/approve', methods=['POST'])
@login_required
@admin_required
def approve(id):
    req = DeletionRequest.query.get_or_404(id)
    if req.status != 'Pending':
        flash('This deletion request has already been processed.', 'warning')
        return redirect(url_for('deletion_request.index'))

    admin_notes = request.form.get('admin_notes', '')

    try:
        # Delete target record based on entity_type
        if req.entity_type == 'customer':
            customer = Customer.query.get(req.entity_id)
            if customer:
                # Also delete associated prescriptions & orders if necessary
                Order.query.filter_by(customer_id=customer.id).delete()
                Prescription.query.filter_by(customer_id=customer.id).delete()
                db.session.delete(customer)
        elif req.entity_type == 'order':
            order = Order.query.get(req.entity_id)
            if order:
                db.session.delete(order) # OrderItems deleted via cascade
        elif req.entity_type == 'prescription':
            prescription = Prescription.query.get(req.entity_id)
            if prescription:
                db.session.delete(prescription)
        elif req.entity_type == 'inventory':
            from models.inventory import Inventory
            item = Inventory.query.get(req.entity_id)
            if item:
                db.session.delete(item)

        req.status = 'Approved'
        req.admin_notes = admin_notes
        req.reviewed_by_id = current_user.id
        req.reviewed_at = datetime.utcnow()

        db.session.commit()
        flash(f'Deletion request #{req.id} for {req.entity_type.capitalize()} "{req.entity_identifier}" approved and deleted.', 'success')
    except Exception as e:
        db.session.rollback()
        flash(f'Error processing approval: {str(e)}', 'danger')

    return redirect(url_for('deletion_request.index'))

@deletion_request_bp.route('/<int:id>/reject', methods=['POST'])
@login_required
@admin_required
def reject(id):
    req = DeletionRequest.query.get_or_404(id)
    if req.status != 'Pending':
        flash('This deletion request has already been processed.', 'warning')
        return redirect(url_for('deletion_request.index'))

    admin_notes = request.form.get('admin_notes', '')

    try:
        req.status = 'Rejected'
        req.admin_notes = admin_notes
        req.reviewed_by_id = current_user.id
        req.reviewed_at = datetime.utcnow()

        db.session.commit()
        flash(f'Deletion request #{req.id} for {req.entity_type.capitalize()} "{req.entity_identifier}" rejected.', 'info')
    except Exception as e:
        db.session.rollback()
        flash(f'Error rejecting request: {str(e)}', 'danger')

    return redirect(url_for('deletion_request.index'))
