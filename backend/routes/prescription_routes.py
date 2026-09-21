from flask import Blueprint, render_template, redirect, url_for, flash, request, jsonify
from flask_login import login_required, current_user
from extensions import db
from models.prescription import Prescription
from models.customer import Customer
from services.ocr_service import process_prescription_image, allowed_file
import os
import uuid
from datetime import datetime
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
            # DV (Distant Vision) fields
            re_sph = request.form.get('re_sph') or None
            re_cyl = request.form.get('re_cyl') or None
            re_axis = request.form.get('re_axis') or None
            re_visual_acuity = request.form.get('re_visual_acuity') or None
            le_sph = request.form.get('le_sph') or None
            le_cyl = request.form.get('le_cyl') or None
            le_axis = request.form.get('le_axis') or None
            le_visual_acuity = request.form.get('le_visual_acuity') or None
            
            # NV (Near Vision) fields
            re_nv_sph = request.form.get('re_nv_sph') or None
            re_nv_cyl = request.form.get('re_nv_cyl') or None
            re_nv_axis = request.form.get('re_nv_axis') or None
            le_nv_sph = request.form.get('le_nv_sph') or None
            le_nv_cyl = request.form.get('le_nv_cyl') or None
            le_nv_axis = request.form.get('le_nv_axis') or None
            
            addition = request.form.get('addition') or None
            
            # Pupillary Distance
            pd_right = request.form.get('pd_right') or None
            pd_left = request.form.get('pd_left') or None
            pd_total = request.form.get('pd_total') or None
            
            # Referring Doctor
            referred_by = request.form.get('referred_by') or None
            
            # Scheduling
            next_visit_str = request.form.get('next_visit_date')
            next_visit_date = datetime.strptime(next_visit_str, '%Y-%m-%d').date() if next_visit_str else None
            lens_expiry_str = request.form.get('lens_expiry_date')
            lens_expiry_date = datetime.strptime(lens_expiry_str, '%Y-%m-%d').date() if lens_expiry_str else None
            
            # Lens Classification & Type
            lens_classification = request.form.get('lens_classification') or None
            lens_type_tags_list = request.form.getlist('lens_type_tags[]')
            lens_type_tags = ','.join(lens_type_tags_list) if lens_type_tags_list else None
            
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
                # DV
                re_sph=re_sph, re_cyl=re_cyl, re_axis=re_axis,
                re_visual_acuity=re_visual_acuity,
                le_sph=le_sph, le_cyl=le_cyl, le_axis=le_axis,
                le_visual_acuity=le_visual_acuity,
                # NV
                re_nv_sph=re_nv_sph, re_nv_cyl=re_nv_cyl, re_nv_axis=re_nv_axis,
                le_nv_sph=le_nv_sph, le_nv_cyl=le_nv_cyl, le_nv_axis=le_nv_axis,
                # Addition & PD
                addition=addition,
                pd_right=pd_right, pd_left=pd_left, pd_total=pd_total,
                # Doctor & Scheduling
                referred_by=referred_by,
                next_visit_date=next_visit_date,
                lens_expiry_date=lens_expiry_date,
                # Lens
                lens_classification=lens_classification,
                lens_type_tags=lens_type_tags,
                # Other
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
            # DV fields
            prescription.re_sph = request.form.get('re_sph') or None
            prescription.re_cyl = request.form.get('re_cyl') or None
            prescription.re_axis = request.form.get('re_axis') or None
            prescription.re_visual_acuity = request.form.get('re_visual_acuity') or None
            prescription.le_sph = request.form.get('le_sph') or None
            prescription.le_cyl = request.form.get('le_cyl') or None
            prescription.le_axis = request.form.get('le_axis') or None
            prescription.le_visual_acuity = request.form.get('le_visual_acuity') or None
            
            # NV fields
            prescription.re_nv_sph = request.form.get('re_nv_sph') or None
            prescription.re_nv_cyl = request.form.get('re_nv_cyl') or None
            prescription.re_nv_axis = request.form.get('re_nv_axis') or None
            prescription.le_nv_sph = request.form.get('le_nv_sph') or None
            prescription.le_nv_cyl = request.form.get('le_nv_cyl') or None
            prescription.le_nv_axis = request.form.get('le_nv_axis') or None
            
            prescription.addition = request.form.get('addition') or None
            
            # PD
            prescription.pd_right = request.form.get('pd_right') or None
            prescription.pd_left = request.form.get('pd_left') or None
            prescription.pd_total = request.form.get('pd_total') or None
            
            # Doctor & Scheduling
            prescription.referred_by = request.form.get('referred_by') or None
            next_visit_str = request.form.get('next_visit_date')
            prescription.next_visit_date = datetime.strptime(next_visit_str, '%Y-%m-%d').date() if next_visit_str else None
            lens_expiry_str = request.form.get('lens_expiry_date')
            prescription.lens_expiry_date = datetime.strptime(lens_expiry_str, '%Y-%m-%d').date() if lens_expiry_str else None
            
            # Lens
            prescription.lens_classification = request.form.get('lens_classification') or None
            lens_type_tags_list = request.form.getlist('lens_type_tags[]')
            prescription.lens_type_tags = ','.join(lens_type_tags_list) if lens_type_tags_list else None
            
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
