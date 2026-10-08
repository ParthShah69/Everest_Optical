from flask import Blueprint, render_template
from flask_login import login_required, current_user
from extensions import db
from models.customer import Customer
from models.order import Order
from models.inventory import Inventory
from models.audit_log import AuditLog
from models.payment import Payment
from sqlalchemy import func, case
from sqlalchemy.orm import joinedload
from datetime import datetime, date, timedelta

dashboard_bp = Blueprint('dashboard', __name__)

@dashboard_bp.route('/dashboard')
@login_required
def index():
    today = date.today()
    first_of_month = today.replace(day=1)

    # Last month range
    last_month_end = first_of_month - timedelta(days=1)
    last_month_start = last_month_end.replace(day=1)

    # ── Query 1: All Order aggregates in ONE pass ─────────────────────────────
    # Replaces 9 separate Order queries with a single aggregate query.
    order_stats = db.session.query(
        func.count(Order.id).label('total_orders'),
        func.sum(case((Order.status == 'Pending', 1), else_=0)).label('pending_orders'),
        func.sum(Order.total_amount).label('total_revenue'),

        # Today's metrics
        func.sum(case((func.date(Order.created_at) == today, 1), else_=0)).label('today_orders'),
        func.sum(case((func.date(Order.created_at) == today, Order.total_amount), else_=0)).label('today_sales'),
        func.sum(case(
            (
                (func.date(Order.created_at) == today) & (Order.total_amount > Order.advance_amount),
                Order.total_amount - Order.advance_amount
            ),
            else_=0
        )).label('today_dues'),

        # Monthly aggregates
        func.sum(case(
            (func.date(Order.created_at) >= first_of_month, Order.total_amount),
            else_=0
        )).label('this_month_revenue'),
        func.sum(case(
            (
                (func.date(Order.created_at) >= last_month_start) &
                (func.date(Order.created_at) <= last_month_end),
                Order.total_amount
            ),
            else_=0
        )).label('last_month_revenue'),

        # Total outstanding dues (non-cancelled, balance > 0)
        func.sum(case(
            (
                (Order.status != 'Cancelled') & (Order.total_amount > Order.advance_amount),
                Order.total_amount - Order.advance_amount
            ),
            else_=0
        )).label('total_dues'),
    ).one()

    total_orders       = order_stats.total_orders or 0
    pending_orders     = int(order_stats.pending_orders or 0)
    total_revenue      = float(order_stats.total_revenue or 0)
    today_orders       = int(order_stats.today_orders or 0)
    today_sales        = float(order_stats.today_sales or 0)
    today_dues         = float(order_stats.today_dues or 0)
    this_month_revenue = float(order_stats.this_month_revenue or 0)
    last_month_revenue = float(order_stats.last_month_revenue or 0)
    total_dues         = float(order_stats.total_dues or 0)

    # ── Query 2: Customer count ───────────────────────────────────────────────
    total_customers = Customer.query.count()

    # ── Query 3: Low-stock inventory count ───────────────────────────────────
    low_stock_items = Inventory.query.filter(
        Inventory.quantity <= Inventory.low_stock_threshold
    ).count()

    # ── Query 4: Today's payment collections ─────────────────────────────────
    today_collection_result = db.session.query(func.sum(Payment.amount)).filter(
        func.date(Payment.created_at) == today
    ).scalar()
    today_collection = float(today_collection_result or 0)

    # ── Query 5: Recent activity (small, lightweight) ─────────────────────────
    recent_activity = AuditLog.query.options(joinedload(AuditLog.user)).order_by(AuditLog.timestamp.desc()).limit(5).all()
    recent_orders   = Order.query.options(joinedload(Order.customer)).order_by(Order.created_at.desc()).limit(5).all()

    return render_template('dashboard/index.html',
                           user=current_user,
                           total_customers=total_customers,
                           total_orders=total_orders,
                           pending_orders=pending_orders,
                           low_stock_items=low_stock_items,
                           total_revenue=total_revenue,
                           today_orders=today_orders,
                           today_sales=today_sales,
                           today_collection=today_collection,
                           today_dues=today_dues,
                           this_month_revenue=this_month_revenue,
                           last_month_revenue=last_month_revenue,
                           total_dues=total_dues,
                           recent_activity=recent_activity,
                           recent_orders=recent_orders)

