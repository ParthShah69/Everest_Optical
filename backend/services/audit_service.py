import json

from flask_login import current_user
from sqlalchemy import event, inspect
from extensions import db
from models.audit_log import AuditLog
from models.customer import Customer
from models.inventory import Inventory
from models.prescription import Prescription
from models.order import Order, OrderItem


def register_audit_listeners():
    models_to_audit = [Customer, Inventory, Prescription, Order, OrderItem]

    for model in models_to_audit:
        event.listen(model, 'after_insert', log_insert)
        event.listen(model, 'after_update', log_update)
        event.listen(model, 'after_delete', log_delete)


def get_current_user_id():
    """Helper to safely get user ID even if outside request context."""
    try:
        if current_user and current_user.is_authenticated:
            return current_user.id
        return None
    except Exception:
        return None


def log_insert(mapper, connection, target):
    """Log an INSERT event via the raw connection to avoid session conflicts."""
    table_name = target.__tablename__
    user_id = get_current_user_id()

    # For INSERT, log the whole record as a string representation
    connection.execute(
        AuditLog.__table__.insert().values(
            user_id=user_id,
            action='INSERT',
            table_name=table_name,
            record_id=target.id,
            new_value=str(target),
        )
    )


def log_update(mapper, connection, target):
    """Log changed fields for an UPDATE event via the raw connection."""
    table_name = target.__tablename__
    user_id = get_current_user_id()

    state = inspect(target)

    for attr in state.attrs:
        if attr.history.has_changes():
            old_val = attr.history.deleted[0] if attr.history.deleted else None
            new_val = attr.history.added[0] if attr.history.added else None

            # Skip internal timestamps
            if attr.key in ['updated_at', 'created_at']:
                continue

            connection.execute(
                AuditLog.__table__.insert().values(
                    user_id=user_id,
                    action='UPDATE',
                    table_name=table_name,
                    record_id=target.id,
                    field_name=attr.key,
                    old_value=str(old_val),
                    new_value=str(new_val),
                )
            )


def log_delete(mapper, connection, target):
    """Log a DELETE event, capturing the full record state via the raw connection."""
    table_name = target.__tablename__
    user_id = get_current_user_id()

    record_dict = {}
    try:
        state = inspect(target)
        for attr in state.attrs:
            # Skip relationships to avoid recursive calls
            if attr.key in state.mapper.relationships.keys():
                continue
            val = attr.value
            if val is not None:
                record_dict[attr.key] = str(val)
    except Exception:
        record_dict = {'summary': str(target)}

    connection.execute(
        AuditLog.__table__.insert().values(
            user_id=user_id,
            action='DELETE',
            table_name=table_name,
            record_id=target.id,
            old_value=json.dumps(record_dict, default=str),
        )
    )
