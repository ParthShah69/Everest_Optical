"""
live_chat_scratchpad_test.py
============================
Tests end-to-end natural conversations through the live Ollama LLM (qwen3:8b)
with Hindi, Hinglish, Gujarati, and mixed inputs.
"""

import sys
import os
import uuid

sys.path.insert(0, os.path.abspath(os.path.dirname(__file__)))

if sys.stdout.encoding != 'utf-8':
    try:
        sys.stdout.reconfigure(encoding='utf-8')
        sys.stderr.reconfigure(encoding='utf-8')
    except Exception:
        pass

from app import create_app
from extensions import db
from models.user import User
from services.ai_service import AIAssistant

def run_live_tests():
    app = create_app()
    with app.app_context():
        assistant = AIAssistant()
        print(f"Ollama Live Status: Available={assistant.is_available()}, Model={assistant.model}")
        
        # Get or create a test user
        user = User.query.first()
        if not user:
            user = User(username="admin_test", role="admin")
            db.session.add(user)
            db.session.commit()
            
        session_id = f"live_test_{uuid.uuid4().hex[:6]}"
        
        test_prompts = [
            # 1. Hindi natural customer creation
            ("Hindi", "नया ग्राहक जोड़ें: नाम किशोर शर्मा, फोन नंबर 9898123456, केयर ऑफ रामलाल"),
            # 2. Hinglish inventory check
            ("Hinglish", "RayBan frame ka stock check karo kitna bacha hai"),
            # 3. Gujarati natural navigation / query
            ("Gujarati", "મને બધા ગ્રાહકો નું લિસ્ટ બતાવો"),
            # 4. Hinglish bill creation intent
            ("Hinglish", "Kishore Sharma ke liye naya order banao: 1 Rayban frame price 3000, 1000 advance diya hai")
        ]
        
        for lang_name, prompt in test_prompts:
            print("\n" + "=" * 60)
            print(f"TESTING [{lang_name}] INPUT: {prompt}")
            print("=" * 60)
            
            try:
                res = assistant.chat(prompt, session_id=session_id, user_id=user.id)
                print("ASSISTANT RESPONSE:")
                print(f"Text: {res.get('text')}")
                print(f"Detected Language: {res.get('language')}")
                print(f"Action: {res.get('action')}")
                print(f"Navigate To: {res.get('navigate_to')}")
                print(f"Tool Calls Made: {len(res.get('tool_calls', []))}")
                for tc in res.get('tool_calls', []):
                    print(f"  -> Tool: {tc.get('name')}")
            except Exception as e:
                print(f"Error during chat: {e}")

if __name__ == "__main__":
    run_live_tests()
