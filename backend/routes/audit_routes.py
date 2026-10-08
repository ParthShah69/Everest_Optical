from flask import Blueprint, render_template, request, redirect, url_for, flash
from flask_login import login_required, current_user
from models.audit_log import AuditLog
from sqlalchemy.orm import joinedload

audit_bp = Blueprint('audit', __name__, url_prefix='/audit')

@audit_bp.route('/')
@login_required
def index():
    if not current_user.is_admin:
        flash('Access denied. Audit logs are for admins only.', 'danger')
        return redirect(url_for('dashboard.index'))

    page = request.args.get('page', 1, type=int)
    table_filter = request.args.get('table_name', '')
    
    query = AuditLog.query
    
    if table_filter:
        query = query.filter(AuditLog.table_name == table_filter)
        
    logs = query.options(joinedload(AuditLog.user)).order_by(AuditLog.timestamp.desc()).paginate(page=page, per_page=20)
    
    return render_template('audit/list.html', logs=logs, table_filter=table_filter)
