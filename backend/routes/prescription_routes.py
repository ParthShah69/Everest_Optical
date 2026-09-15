from flask import Blueprint, render_template, redirect, url_for, flash, request, jsonify
from flask_login import login_required, current_user
from extensions import db
from models.prescription import Prescription
from models.customer import Customer
from services.ocr_service import process_prescription_image, allowed_file
import os
import uuid
from werkzeug.utils import secure_filename

prescription_bp = Blueprint('prescription', __name__, url_prefix='/prescriptions')

# Configure Upload Folder
UPLOAD_FOLDER = os.path.join('static', 'uploads')
try:
    os.makedirs(UPLOAD_FOLDER, exist_ok=True)
except OSError:
    pass

@prescription_bp.route('/add/<int:customer_id>', methods=['GET', 'POST'])
@login_required
def add(customer_id):
    customer = Customer.query.get_or_404(customer_id)
    
    if request.method == 'POST':
        try:
            re_sph = request.form.get('re_sph') or None
            re_cyl = request.form.get('re_cyl') or None
            re_axis = request.form.get('re_axis') or None
            le_sph = request.form.get('le_sph') or None
            le_cyl = request.form.get('le_cyl') or None
            le_axis = request.form.get('le_axis') or None
            addition = request.form.get('addition') or None
            notes = request.form.get('notes')
            
            image_path = None
            if 'prescription_image' in request.files:
                file = request.files['prescription_image']
                if file and file.filename != '' and allowed_file(file.filename):
                    filename = secure_filename(f"presc_{customer_id}_{uuid.uuid4().hex[:8]}.{file.filename.rsplit('.', 1)[1].lower()}")
                    save_dir = os.path.join('static', 'uploads')
                    try:
                        os.makedirs(save_dir, exist_ok=True)
                        filepath = os.path.join(save_dir, filename)
                        file.save(filepath)
                        image_path = filepath.replace('\\', '/')
                    except OSError:
                        tmp_dir = os.path.join('/tmp', 'uploads')
                        os.makedirs(tmp_dir, exist_ok=True)
                        filepath = os.path.join(tmp_dir, filename)
                        file.save(filepath)
                        image_path = filepath.replace('\\', '/')

            new_prescription = Prescription(
                customer_id=customer_id,
                re_sph=re_sph, re_cyl=re_cyl, re_axis=re_axis,
                le_sph=le_sph, le_cyl=le_cyl, le_axis=le_axis,
                addition=addition,
                notes=notes,
                image_path=image_path,
                created_by=current_user.id
            )
            
            db.session.add(new_prescription)
            db.session.commit()
            flash('Prescription added successfully!', 'success')
            return redirect(url_for('prescription.history', customer_id=customer_id))
            
        except Exception as e:
            db.session.rollback()
            flash(f'Error adding prescription: {str(e)}', 'danger')

    return render_template('prescriptions/add.html', customer=customer)

@prescription_bp.route('/history/<int:customer_id>')
@login_required
def history(customer_id):
    customer = Customer.query.get_or_404(customer_id)
    page = request.args.get('page', 1, type=int)
    prescriptions = Prescription.query.filter_by(customer_id=customer_id).order_by(Prescription.created_at.desc()).paginate(page=page, per_page=20)
    return render_template('prescriptions/history.html', customer=customer, prescriptions=prescriptions)

@prescription_bp.route('/view/<int:id>')
@login_required
def view(id):
    prescription = Prescription.query.get_or_404(id)
    return render_template('prescriptions/view.html', prescription=prescription)

@prescription_bp.route('/edit/<int:id>', methods=['GET', 'POST'])
@login_required
def edit(id):
    prescription = Prescription.query.get_or_404(id)
    customer = prescription.customer

    if request.method == 'POST':
        try:
            prescription.re_sph = request.form.get('re_sph') or None
            prescription.re_cyl = request.form.get('re_cyl') or None
            prescription.re_axis = request.form.get('re_axis') or None
            prescription.le_sph = request.form.get('le_sph') or None
            prescription.le_cyl = request.form.get('le_cyl') or None
            prescription.le_axis = request.form.get('le_axis') or None
            prescription.addition = request.form.get('addition') or None
            prescription.notes = request.form.get('notes')
            db.session.commit()
            flash('Prescription updated successfully!', 'success')
            return redirect(url_for('prescription.history', customer_id=customer.id))
        except Exception as e:
            db.session.rollback()
            flash(f'Error updating prescription: {str(e)}', 'danger')

    return render_template('prescriptions/edit.html', prescription=prescription, customer=customer)

@prescription_bp.route('/delete/<int:id>', methods=['POST'])
@login_required
def delete(id):
    prescription = Prescription.query.get_or_404(id)
    customer_id = prescription.customer_id
    customer_name = prescription.customer.name if prescription.customer else "Unknown"

    if current_user.is_admin:
        try:
            db.session.delete(prescription)
            db.session.commit()
            flash('Prescription deleted permanently.', 'success')
        except Exception as e:
            db.session.rollback()
            flash(f'Error deleting: {str(e)}', 'danger')
    else:
        reason = request.form.get('reason', '').strip()
        if not reason:
            flash('Please provide a reason for deletion.', 'warning')
            return redirect(url_for('prescription.history', customer_id=customer_id))
            
        from models.deletion_request import DeletionRequest
        
        existing_req = DeletionRequest.query.filter_by(
            entity_type='prescription', entity_id=prescription.id, status='Pending'
        ).first()
        
        if existing_req:
            flash('A deletion request for this prescription is already pending admin review.', 'info')
            return redirect(url_for('prescription.history', customer_id=customer_id))

        del_req = DeletionRequest(
            entity_type='prescription',
            entity_id=prescription.id,
            entity_identifier=f"Prescription #{prescription.id} (Customer: {customer_name})",
            reason=reason,
            requested_by_id=current_user.id
        )
        try:
            db.session.add(del_req)
            db.session.commit()
            flash('Deletion request for prescription submitted to admin.', 'success')
        except Exception as e:
            db.session.rollback()
            flash(f'Error submitting deletion request: {str(e)}', 'danger')

    return redirect(url_for('prescription.history', customer_id=customer_id))


@prescription_bp.route('/ocr_scan', methods=['POST'])
@login_required
def ocr_scan():
    if 'file' not in request.files:
        return jsonify({'error': 'No file part'}), 400
    file = request.files['file']
    if file.filename == '':
        return jsonify({'error': 'No selected file'}), 400
    
    if file and allowed_file(file.filename):
        filename = secure_filename(file.filename)
        filepath = os.path.join(UPLOAD_FOLDER, filename)
        file.save(filepath)
        try:
            data = process_prescription_image(filepath)
            os.remove(filepath)
            return jsonify(data)
        except Exception as e:
            return jsonify({'error': str(e)}), 500
            
    return jsonify({'error': 'Invalid file type'}), 400
