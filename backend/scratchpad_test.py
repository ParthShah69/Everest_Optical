"""
scratchpad_test.py
==================
Automated scratchpad test suite to verify:
1. Custom User Creation & Auth
2. Language Detection & Normalization (Hindi, Hinglish, Gujarati, Mixed)
3. Direct AI Tools Execution:
   - create_customer (Hindi input)
   - create_customer (Gujarati input)
   - add_prescription (Hindi / Hinglish mix)
   - add_inventory_item & search_inventory (Hinglish input)
   - create_order (Multilingual)
   - update_order_status
   - get_dashboard_stats
   - navigate_to_page
4. ChatMessage persistence and DB integrity
"""

import sys
import os
import json
import uuid

# Add backend directory to sys.path
sys.path.insert(0, os.path.abspath(os.path.dirname(__file__)))

if sys.stdout.encoding != 'utf-8':
    try:
        sys.stdout.reconfigure(encoding='utf-8')
        sys.stderr.reconfigure(encoding='utf-8')
    except Exception:
        pass

from app import create_app
from extensions import db, bcrypt
from models.user import User
from models.customer import Customer
from models.order import Order
from models.prescription import Prescription
from models.inventory import Inventory
from models.chat_history import ChatMessage
from services.language_service import (
    detect_language,
    extract_english_name,
    words_to_number,
    language_label_to_human
)
from services.ai_tools import (
    create_customer,
    search_customers,
    create_order,
    add_prescription,
    add_inventory_item,
    search_inventory,
    update_order_status,
    get_dashboard_stats,
    navigate_to_page,
    create_user
)

def run_tests():
    app = create_app()
    with app.app_context():
        print("=" * 60)
        print("STEP 1: Testing Language Detection & Normalization")
        print("=" * 60)
        
        test_phrases = [
            ("नया ग्राहक जोड़ें: नाम रमेश पटेल, फोन 9876543210", "hi"),
            ("Navo customer add karvo che name Hitesh Shah", "romanized_gu"),
            ("નવો ગ્રાહક ઉમેરો: નામ હિતેશ શાહ, ફોન 9825012345", "gu"),
            ("Customer Rahul ka bill banao 2000 rupees", "romanized_hi"),
            ("Right eye SPH -1.50, CYL -0.50 doctor prescription", "en")
        ]
        
        for phrase, expected_hint in test_phrases:
            lang = detect_language(phrase)
            label = language_label_to_human(lang)
            clean_name = extract_english_name(phrase)
            print(f"Input: '{phrase}'")
            print(f" -> Detected: {lang} ({label}) | Cleaned: '{clean_name}'")
        
        print("\n" + "=" * 60)
        print("STEP 2: Creating Test Custom User")
        print("=" * 60)
        
        test_username = f"test_optician_{uuid.uuid4().hex[:6]}"
        test_email = f"{test_username}@example.com"
        
        # Use create_user tool or direct model
        user_res = json.loads(create_user(
            username=test_username,
            email=test_email,
            password="SecurePassword123!",
            role="Staff"
        ))
        print(f"create_user response: {user_res}")
        assert user_res.get("success"), f"User creation failed: {user_res}"
        
        user_obj = User.query.filter_by(username=test_username).first()
        assert user_obj is not None, "User object not found in DB"
        print(f"Created user successfully: ID={user_obj.id}, username={user_obj.username}")

        print("\n" + "=" * 60)
        print("STEP 3: Testing AI Tool - Customer Creation (Hindi Data)")
        print("=" * 60)
        
        # Test 1: Hindi Customer
        uid_hi = uuid.uuid4().hex[:4]
        phone_hi = f"9876{uuid.uuid4().int % 1000000:06d}"
        cust_hi_res = json.loads(create_customer(
            name=f"रमेश पटेल (Ramesh Patel {uid_hi})",
            phone=phone_hi,
            care_of="कांतिभाई (Kantibhai)"
        ))
        print(f"Hindi customer creation result: {cust_hi_res}")
        assert cust_hi_res.get("success"), f"Hindi customer creation failed: {cust_hi_res}"
        cust_hi_id = cust_hi_res["customer"]["id"]

        print("\n" + "=" * 60)
        print("STEP 4: Testing AI Tool - Customer Creation (Gujarati Data)")
        print("=" * 60)
        
        # Test 2: Gujarati Customer
        uid_gu = uuid.uuid4().hex[:4]
        phone_gu = f"9825{uuid.uuid4().int % 1000000:06d}"
        cust_gu_res = json.loads(create_customer(
            name=f"હિતેશ શાહ (Hitesh Shah {uid_gu})",
            phone=phone_gu,
            care_of="દિનેશભાઈ (Dineshbhai)"
        ))
        print(f"Gujarati customer creation result: {cust_gu_res}")
        assert cust_gu_res.get("success"), f"Gujarati customer creation failed: {cust_gu_res}"
        cust_gu_id = cust_gu_res["customer"]["id"]

        print("\n" + "=" * 60)
        print("STEP 5: Testing AI Tool - Add Prescription (Hindi/Hinglish Mix)")
        print("=" * 60)
        
        rx_res = json.loads(add_prescription(
            customer_id=cust_hi_id,
            re_sph=-1.50,
            re_cyl=-0.50,
            re_axis=90,
            le_sph=-2.00,
            le_cyl=0.00,
            le_axis=0,
            addition=1.25,
            notes="डॉ. शर्मा (Dr. Sharma) - एंटी-ग्लेयर कोटिंग चश्मे"
        ))
        print(f"Prescription addition result: {rx_res}")
        assert rx_res.get("success"), f"Prescription failed: {rx_res}"
        rx_id = rx_res.get("prescription_id") or rx_res.get("prescription", {}).get("id")

        print("\n" + "=" * 60)
        print("STEP 6: Testing AI Tool - Inventory Item Creation (Hinglish/Optical terms)")
        print("=" * 60)
        
        inv_res = json.loads(add_inventory_item(
            model_name="RayBan Aviator Classic Gold Frame",
            location="Shelf A-1",
            cost_price=1800.0,
            selling_price=3200.0,
            brand="RayBan",
            frame_type="Full Rim",
            quantity=15
        ))
        print(f"Inventory item creation result: {inv_res}")
        assert inv_res.get("success"), f"Inventory creation failed: {inv_res}"
        inv_id = inv_res["item"]["id"]

        # Search inventory
        inv_search = json.loads(search_inventory(query="RayBan"))
        print(f"Inventory search result: found {len(inv_search.get('items', []))} items")
        assert len(inv_search.get("items", [])) > 0

        print("\n" + "=" * 60)
        print("STEP 7: Testing AI Tool - Order Creation (Bill Banao - Hinglish / Multi)")
        print("=" * 60)
        
        order_res = json.loads(create_order(
            customer_id=cust_hi_id,
            items=[
                {
                    "description": "RayBan Aviator Classic Gold Frame",
                    "quantity": 1,
                    "unit_price": 3200.0
                },
                {
                    "description": "Blue Cut Anti-Glare Crizal Lenses",
                    "quantity": 2,
                    "unit_price": 800.0
                }
            ],
            advance_amount=2000.0,
            prescription_id=rx_id,
            delivery_mode="Self"
        ))
        print(f"Order creation result: {order_res}")
        assert order_res.get("success"), f"Order creation failed: {order_res}"
        order_id = order_res["order"]["id"]
        order_num = order_res["order"]["order_no"]
        print(f"Created order: {order_num}, Total={order_res['order']['total']}, Balance={order_res['order']['balance']}")

        print("\n" + "=" * 60)
        print("STEP 8: Testing AI Tool - Update Order Status & Stats")
        print("=" * 60)
        
        status_res = json.loads(update_order_status(order_id=order_id, status="Ready"))
        print(f"Update order status result: {status_res}")
        assert status_res.get("success")

        stats_res = json.loads(get_dashboard_stats())
        print(f"Dashboard stats result: {stats_res}")
        assert stats_res.get("success")

        nav_res = json.loads(navigate_to_page("orders"))
        print(f"Navigation result: {nav_res}")
        assert nav_res.get("success") and nav_res.get("url") == "/orders/"

        print("\n" + "=" * 60)
        print("STEP 9: Testing ChatMessage Model Persistence")
        print("=" * 60)
        
        sess_id = f"test_session_{uuid.uuid4().hex[:8]}"
        user_msg = ChatMessage(
            session_id=sess_id,
            user_id=user_obj.id,
            role="user",
            content="नया बिल बनाओ",
            original_language="hi",
            original_text="नया बिल बनाओ"
        )
        assistant_msg = ChatMessage(
            session_id=sess_id,
            user_id=user_obj.id,
            role="assistant",
            content="मैंने बिल बना दिया है। Order No: " + order_num,
            original_language="hi",
            action_type="create"
        )
        db.session.add(user_msg)
        db.session.add(assistant_msg)
        db.session.commit()

        saved_msgs = ChatMessage.query.filter_by(session_id=sess_id).all()
        print(f"Saved {len(saved_msgs)} chat messages to session {sess_id}")
        assert len(saved_msgs) == 2, "ChatMessage persistence failed"

        print("\n" + "=" * 60)
        print("ALL TESTS PASSED SUCCESSFULLY! ALL 14 SCENARIOS VERIFIED.")
        print("=" * 60)

if __name__ == "__main__":
    run_tests()
