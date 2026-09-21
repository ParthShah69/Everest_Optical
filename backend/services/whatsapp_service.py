"""WhatsApp integration service for sending customer notifications."""
import urllib.parse


def get_whatsapp_url(phone, message):
    """Generate a WhatsApp click-to-chat URL.
    
    Args:
        phone: Customer phone number (any format)
        message: Pre-formatted message text
    
    Returns:
        WhatsApp URL string
    """
    clean_phone = phone.replace(' ', '').replace('-', '').replace('(', '').replace(')', '')
    if not clean_phone.startswith('+'):
        # Default to India country code
        if clean_phone.startswith('0'):
            clean_phone = clean_phone[1:]
        clean_phone = '91' + clean_phone
    else:
        clean_phone = clean_phone[1:]  # Remove the + for wa.me format
    
    encoded_msg = urllib.parse.quote(message)
    return f"https://wa.me/{clean_phone}?text={encoded_msg}"


def format_order_confirmation(order):
    """Format an order confirmation message for WhatsApp."""
    items_text = ""
    for item in order.items:
        items_text += f"• {item.display_name} x{item.quantity} — ₹{item.total_price}\n"
    
    msg = (
        f"🧾 *Order Confirmation*\n"
        f"━━━━━━━━━━━━━━━━\n"
        f"Order No: *{order.order_no}*\n"
        f"Date: {order.issue_date.strftime('%d %b %Y') if order.issue_date else 'N/A'}\n\n"
        f"*Items:*\n{items_text}\n"
        f"Total: *₹{order.total_amount}*\n"
        f"Advance Paid: ₹{order.advance_amount or 0}\n"
        f"Balance Due: *₹{order.balance_amount}*\n\n"
    )
    
    if order.delivery_date:
        msg += f"📅 Expected Delivery: *{order.delivery_date.strftime('%d %b %Y')}*\n\n"
    
    msg += "Thank you for choosing our optical shop! 👓"
    return msg


def format_ready_notification(order):
    """Format a 'Ready for Pickup' message for WhatsApp."""
    msg = (
        f"✅ *Your Order is Ready!*\n"
        f"━━━━━━━━━━━━━━━━\n"
        f"Order No: *{order.order_no}*\n"
        f"Customer: {order.customer.name}\n\n"
        f"Your order is ready for pickup. Please visit our store at your convenience.\n\n"
    )
    
    if order.balance_amount > 0:
        msg += f"💰 Balance Due: *₹{order.balance_amount}*\n\n"
    
    msg += "Thank you! 🙏"
    return msg


def format_payment_receipt(order, payment):
    """Format a payment receipt message for WhatsApp."""
    msg = (
        f"💳 *Payment Received*\n"
        f"━━━━━━━━━━━━━━━━\n"
        f"Receipt No: *{payment.receipt_no}*\n"
        f"Order No: {order.order_no}\n\n"
        f"Amount: *₹{payment.amount}*\n"
        f"Method: {payment.payment_method}\n"
        f"Date: {payment.created_at.strftime('%d %b %Y %I:%M %p') if payment.created_at else 'N/A'}\n\n"
        f"Remaining Balance: *₹{order.remaining_due}*\n\n"
        f"Thank you! 🙏"
    )
    return msg
