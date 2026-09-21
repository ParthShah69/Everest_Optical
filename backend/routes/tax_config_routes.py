from decimal import Decimal, InvalidOperation

from flask import Blueprint, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required

from extensions import db
from models.tax_config import TaxConfig


tax_config_bp = Blueprint('tax_config', __name__, url_prefix='/tax-config')


def _admin_required():
    if not current_user.is_admin:
        flash('Access denied. Admin rights required.', 'danger')
        return False
    return True


def _parse_rate(value):
    try:
        rate = Decimal(value)
    except (InvalidOperation, TypeError):
        raise ValueError('GST percentage must be a valid number.')
    if not Decimal('0') <= rate <= Decimal('100'):
        raise ValueError('GST percentage must be between 0 and 100.')
    return rate.quantize(Decimal('0.01'))


@tax_config_bp.route('/')
@login_required
def index():
    if not _admin_required():
        return redirect(url_for('dashboard.index'))
    configs = TaxConfig.query.order_by(
        TaxConfig.is_default.desc(), TaxConfig.rate.asc(), TaxConfig.name.asc()
    ).all()
    return render_template('tax_config/index.html', configs=configs, editing_config=None)


@tax_config_bp.route('/<int:id>/edit')
@login_required
def edit(id):
    if not _admin_required():
        return redirect(url_for('dashboard.index'))
    configs = TaxConfig.query.order_by(
        TaxConfig.is_default.desc(), TaxConfig.rate.asc(), TaxConfig.name.asc()
    ).all()
    return render_template(
        'tax_config/index.html', configs=configs,
        editing_config=TaxConfig.query.get_or_404(id)
    )


@tax_config_bp.route('/save', methods=['POST'])
@login_required
def save():
    if not _admin_required():
        return redirect(url_for('dashboard.index'))

    config_id = request.form.get('id', type=int)
    config = TaxConfig.query.get_or_404(config_id) if config_id else TaxConfig()
    name = request.form.get('name', '').strip()
    try:
        if not name:
            raise ValueError('Tax name is required.')
        config.name = name
        config.rate = _parse_rate(request.form.get('rate', ''))
        config.is_active = request.form.get('is_active') == 'on'
        config.is_default = request.form.get('is_default') == 'on'

        if config.is_default:
            TaxConfig.query.filter(TaxConfig.id != config.id).update(
                {TaxConfig.is_default: False}, synchronize_session=False
            )

        db.session.add(config)
        db.session.commit()
        flash('GST configuration saved.', 'success')
    except ValueError as error:
        db.session.rollback()
        flash(str(error), 'danger')
    except Exception:
        db.session.rollback()
        flash('Unable to save GST configuration. The name may already be in use.', 'danger')
    return redirect(url_for('tax_config.index'))


@tax_config_bp.route('/<int:id>/toggle', methods=['POST'])
@login_required
def toggle(id):
    if not _admin_required():
        return redirect(url_for('dashboard.index'))
    config = TaxConfig.query.get_or_404(id)
    config.is_active = not config.is_active
    if not config.is_active:
        config.is_default = False
    db.session.commit()
    flash(f"GST configuration {'enabled' if config.is_active else 'disabled'}.", 'success')
    return redirect(url_for('tax_config.index'))
