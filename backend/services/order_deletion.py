"""Delete an order and its dependent records in the caller's transaction."""

import hashlib
import json

from extensions import db
from models.inventory import Inventory
from models.payment import Payment


def order_deletion_fingerprint(order):
    """Bind approval to the exact order details the admin reviewed."""
    details = {
        'order_no': order.order_no,
        'customer_id': order.customer_id,
        'status': order.status,
        'total_amount': str(order.total_amount),
        'advance_amount': str(order.advance_amount),
        'items': sorted((line.id, line.inventory_id, line.quantity)
                        for line in order.items),
        'payments': sorted((payment.id, str(payment.amount))
                           for payment in Payment.query.filter_by(order_id=order.id).all()),
    }
    return hashlib.sha256(json.dumps(details, sort_keys=True).encode('utf-8')).hexdigest()


def delete_order_records(order):
    """Restore delivered stock, remove receipts, and delete the order and lines.

    The caller commits or rolls back so these changes remain atomic with any
    approval record or surrounding workflow.
    """
    if order.status == 'Delivered':
        for line in order.items:
            if line.inventory_id:
                inventory = db.session.get(Inventory, line.inventory_id)
                if inventory:
                    inventory.quantity = (inventory.quantity or 0) + (line.quantity or 0)

    for payment in Payment.query.filter_by(order_id=order.id).all():
        db.session.delete(payment)
    db.session.delete(order)  # Order.items cascades to order lines.
