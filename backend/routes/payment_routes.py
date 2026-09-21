from flask import Blueprint, request, jsonify, flash, redirect, url_for, render_template
from flask_login import login_required, current_user
from extensions import db
from models.payment import Payment
from models.order import Order
from models.sequence import get_next_number

payment_bp = Blueprint('payment', __name__, url_prefix='/payments')


@payment_bp.route('/add/<int:order_id>', methods=['POST'])
@login_required
def add_payment(order_id):
    """Record a new payment (or multiple split payments) for an order."""
    order = Order.query.get_or_404(order_id)
    
    try:
        # Support split payments: arrays of method[], amount[], remark[]
        methods = request.form.getlist('payment_method[]')
        amounts = request.form.getlist('payment_amount[]')
        remarks = request.form.getlist('payment_remark[]')
        payment_type = request.form.get('payment_type', 'advance')
        
        if not methods:
            # Single payment fallback
            methods = [request.form.get('payment_method', 'Cash')]
            amounts = [request.form.get('payment_amount', '0')]
            remarks = [request.form.get('payment_remark', '')]
        
        total_new_payment = 0
        payment_entries = []

        for i in range(len(methods)):
            amt = float(amounts[i]) if i < len(amounts) and amounts[i] else 0
            if amt <= 0:
                continue
                
            method = methods[i] if i < len(methods) else 'Cash'
            remark = remarks[i] if i < len(remarks) else ''
            
            payment_entries.append((method, amt, remark))
            total_new_payment += amt

        if not payment_entries:
            raise ValueError('Enter at least one payment amount greater than zero.')

        if total_new_payment > max(0, order.remaining_due) + 0.005:
            raise ValueError(
                f'Payment of ₹{total_new_payment:.2f} exceeds the remaining due of ₹{order.remaining_due:.2f}.'
            )

        for method, amt, remark in payment_entries:
            payment = Payment(
                order_id=order.id,
                receipt_no=get_next_number('RCP'),
                amount=amt,
                payment_method=method,
                remark=remark,
                payment_type=payment_type,
                created_by=current_user.id
            )
            db.session.add(payment)
        
        # Keep the legacy aggregate field in sync without losing an advance
        # recorded on an older order before individual receipt rows existed.
        order.advance_amount = order.total_paid + total_new_payment
        
        db.session.commit()
        flash(f'Payment of ₹{total_new_payment:.2f} recorded successfully!', 'success')
        
    except Exception as e:
        db.session.rollback()
        flash(f'Error recording payment: {str(e)}', 'danger')
    
    return redirect(url_for('order.view', id=order_id))


@payment_bp.route('/history/<int:order_id>')
@login_required
def payment_history(order_id):
    """Get payment history for an order (JSON API)."""
    order = Order.query.get_or_404(order_id)
    payments = Payment.query.filter_by(order_id=order_id).order_by(Payment.created_at.desc()).all()
    
    return jsonify({
        'order_no': order.order_no,
        'total_amount': float(order.total_amount or 0),
        'total_paid': order.total_paid,
        'remaining_due': order.remaining_due,
        'payments': [{
            'receipt_no': p.receipt_no,
            'amount': float(p.amount),
            'method': p.payment_method,
            'remark': p.remark,
            'type': p.payment_type,
            'date': p.created_at.strftime('%d %b %Y %I:%M %p') if p.created_at else ''
        } for p in payments]
    })


@payment_bp.route('/<int:payment_id>/print/receipt')
@login_required
def print_receipt(payment_id):
    """Render one immutable payment entry as a printer-friendly receipt."""
    payment = Payment.query.get_or_404(payment_id)
    return render_template('print/payment_receipt.html', payment=payment, order=payment.order)
