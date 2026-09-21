from flask import Blueprint, render_template
from flask_login import login_required, current_user
from extensions import db
from models.customer import Customer
from models.order import Order
from models.inventory import Inventory
from models.audit_log import AuditLog
from models.payment import Payment
from sqlalchemy import func
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

    # ── Key Metrics ──
    total_customers = Customer.query.count()
    total_orders = Order.query.count()
    pending_orders = Order.query.filter_by(status='Pending').count()
    low_stock_items = Inventory.query.filter(Inventory.quantity <= Inventory.low_stock_threshold).count()
    
    # Total Revenue (Sum of total_amount from Orders)
    revenue_result = db.session.query(func.sum(Order.total_amount)).scalar()
    total_revenue = revenue_result if revenue_result else 0.0

    # ── Today's Metrics ──
    today_orders = Order.query.filter(
        func.date(Order.created_at) == today
    ).count()
    
    today_sales_result = db.session.query(func.sum(Order.total_amount)).filter(
        func.date(Order.created_at) == today
    ).scalar()
    today_sales = today_sales_result if today_sales_result else 0.0

    # Today's Collection (sum of payments received today)
    today_collection_result = db.session.query(func.sum(Payment.amount)).filter(
        func.date(Payment.created_at) == today
    ).scalar()
    today_collection = today_collection_result if today_collection_result else 0.0

    # Today's Dues (sum of balance on today's orders)
    today_dues_result = db.session.query(
        func.sum(Order.total_amount - Order.advance_amount)
    ).filter(
        func.date(Order.created_at) == today,
        Order.total_amount > Order.advance_amount
    ).scalar()
    today_dues = today_dues_result if today_dues_result else 0.0

    # ── Monthly Comparison ──
    this_month_revenue_result = db.session.query(func.sum(Order.total_amount)).filter(
        func.date(Order.created_at) >= first_of_month
    ).scalar()
    this_month_revenue = this_month_revenue_result if this_month_revenue_result else 0.0

    last_month_revenue_result = db.session.query(func.sum(Order.total_amount)).filter(
        func.date(Order.created_at) >= last_month_start,
        func.date(Order.created_at) <= last_month_end
    ).scalar()
    last_month_revenue = last_month_revenue_result if last_month_revenue_result else 0.0

    # ── Total Outstanding Dues ──
    total_dues_result = db.session.query(
        func.sum(Order.total_amount - Order.advance_amount)
    ).filter(
        Order.total_amount > Order.advance_amount,
        Order.status != 'Cancelled'
    ).scalar()
    total_dues = total_dues_result if total_dues_result else 0.0

    # Recent Activity (Last 5 Audit Logs)
    recent_activity = AuditLog.query.order_by(AuditLog.timestamp.desc()).limit(5).all()
    
    # Recent Orders (Last 5)
    recent_orders = Order.query.order_by(Order.created_at.desc()).limit(5).all()

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
