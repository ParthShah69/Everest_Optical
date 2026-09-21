from flask import Blueprint, render_template, redirect, url_for, flash, request
from flask_login import login_required, current_user
from extensions import db
from models.customer import Customer

customer_bp = Blueprint('customer', __name__, url_prefix='/customers')

@customer_bp.route('/')
@login_required
def index():
    page = request.args.get('page', 1, type=int)
    search_query = request.args.get('search', '')
    
    query = Customer.query

    if search_query:
        search_filter = f"%{search_query}%"
        query = query.filter(
            (Customer.name.ilike(search_filter)) | 
            (Customer.phone.ilike(search_filter)) |
            (Customer.care_of.ilike(search_filter)) |
            (Customer.email.ilike(search_filter)) |
            (Customer.city.ilike(search_filter))
        )
    
    # Order by most recently updated/created
    customers = query.order_by(Customer.updated_at.desc()).paginate(page=page, per_page=10)
    
    return render_template('customers/list.html', customers=customers, search_query=search_query)

@customer_bp.route('/add', methods=['GET', 'POST'])
@login_required
def add():
    if request.method == 'POST':
        name = request.form.get('name')
        care_of = request.form.get('care_of')
        phone = request.form.get('phone')
        email = request.form.get('email')
        address = request.form.get('address')
        city = request.form.get('city')
        state = request.form.get('state')
        pincode = request.form.get('pincode')
        credit_limit = float(request.form.get('credit_limit', 0) or 0)
        
        # Basic validation
        if not name or not phone:
            flash('Name and Phone are required!', 'danger')
            return redirect(url_for('customer.add'))

        new_customer = Customer(
            name=name, care_of=care_of, phone=phone,
            email=email, address=address, city=city,
            state=state, pincode=pincode, credit_limit=credit_limit
        )
        
        try:
            db.session.add(new_customer)
            db.session.commit()
            flash('Customer added successfully!', 'success')
            return redirect(url_for('customer.index'))
        except Exception as e:
            db.session.rollback()
            flash(f'Error adding customer: {str(e)}', 'danger')

    return render_template('customers/add.html')

@customer_bp.route('/edit/<int:id>', methods=['GET', 'POST'])
@login_required
def edit(id):
    customer = Customer.query.get_or_404(id)
    
    if request.method == 'POST':
        customer.name = request.form.get('name')
        customer.care_of = request.form.get('care_of')
        customer.phone = request.form.get('phone')
        customer.email = request.form.get('email')
        customer.address = request.form.get('address')
        customer.city = request.form.get('city')
        customer.state = request.form.get('state')
        customer.pincode = request.form.get('pincode')
        customer.credit_limit = float(request.form.get('credit_limit', 0) or 0)
        
        try:
            db.session.commit()
            flash('Customer updated successfully!', 'success')
            return redirect(url_for('customer.index'))
        except Exception as e:
            db.session.rollback()
            flash(f'Error updating customer: {str(e)}', 'danger')
            
    return render_template('customers/edit.html', customer=customer)

@customer_bp.route('/delete/<int:id>', methods=['POST'])
@login_required
def delete(id):
    customer = Customer.query.get_or_404(id)
    
    if current_user.is_admin:
        try:
            from models.order import Order
            from models.prescription import Prescription
            Order.query.filter_by(customer_id=customer.id).delete()
            Prescription.query.filter_by(customer_id=customer.id).delete()
            db.session.delete(customer)
            db.session.commit()
            flash(f'Customer "{customer.name}" and associated records deleted permanently.', 'success')
        except Exception as e:
            db.session.rollback()
            flash(f'Error deleting customer: {str(e)}', 'danger')
    else:
        reason = request.form.get('reason', '').strip()
        if not reason:
            flash('Please provide a reason for deletion.', 'warning')
            return redirect(url_for('customer.index'))
            
        from models.deletion_request import DeletionRequest
        
        # Check if there is already a pending request for this customer
        existing_req = DeletionRequest.query.filter_by(
            entity_type='customer', entity_id=customer.id, status='Pending'
        ).first()
        
        if existing_req:
            flash(f'A deletion request for customer "{customer.name}" is already pending admin review.', 'info')
            return redirect(url_for('customer.index'))

        del_req = DeletionRequest(
            entity_type='customer',
            entity_id=customer.id,
            entity_identifier=f"{customer.name} (Phone: {customer.phone})",
            reason=reason,
            requested_by_id=current_user.id
        )
        try:
            db.session.add(del_req)
            db.session.commit()
            flash(f'Deletion request for customer "{customer.name}" submitted to admin.', 'success')
        except Exception as e:
            db.session.rollback()
            flash(f'Error submitting deletion request: {str(e)}', 'danger')
            
    return redirect(url_for('customer.index'))
