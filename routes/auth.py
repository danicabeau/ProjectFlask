from flask import Blueprint, request, jsonify, session, redirect, url_for # <--- เพิ่ม redirect, url_for
from firebase_admin import auth
from utils.db import get_db
from bson import ObjectId
from datetime import datetime

auth_bp = Blueprint('auth', __name__)

@auth_bp.route('/api/login', methods=['POST'])
def login():
    id_token = request.json.get('idToken')
    try:
        # 1. ตรวจสอบ Token กับ Firebase
        decoded_token = auth.verify_id_token(id_token)
        email = decoded_token['email']
        full_name = decoded_token.get('name', '')

        # แยกชื่อและนามสกุลจาก Google Name
        name_parts = full_name.split(' ', 1)
        first_name = name_parts[0]
        last_name = name_parts[1] if len(name_parts) > 1 else ""

        # 2. เชื่อมต่อ MongoDB
        db = get_db()
        user = db.users.find_one({"email": email})

        # 3. ถ้าเป็น user ใหม่ ให้บันทึกข้อมูลตามกฎ Schema
        if not user:
            new_user = {
                "email": email,
                "name": full_name,
                "role": "student",
                "password": "", 
                "profile": {
                    "first_name": first_name, 
                    "last_name": last_name,   
                    "phone": ""               
                },
                "is_active": True,
                "created_at": datetime.now()
            }
            result = db.users.insert_one(new_user)
            user = new_user
            user['_id'] = result.inserted_id

        # 4. เก็บข้อมูลเข้า Session
        session['user_id'] = str(user['_id'])
        session['role'] = user['role']
        session['email'] = email
        session['user_name'] = user['name'] # เพิ่มบรรทัดนี้เพื่อให้ base.html แสดงชื่อได้

        print(f"✅ Login Success: {email} (Role: {user['role']})")
        return jsonify({"status": "success", "role": user['role']})

    except Exception as e:
        print(f"❌ Firebase Error: {str(e)}")
        return jsonify({"status": "error", "message": str(e)}), 401

# ⭐ เพิ่มส่วนนี้: ฟังก์ชัน Logout
@auth_bp.route('/logout')
def logout():
    session.clear() # ล้าง Session ทั้งหมด
    print("🔒 User logged out.")
    return redirect(url_for('index')) # กลับไปหน้าแรก (ซึ่งจะเด้งไปหน้า Login)