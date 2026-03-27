from flask import Blueprint, request, jsonify, session, redirect, url_for, render_template
from firebase_admin import auth
from utils.db import get_db
from bson import ObjectId
from datetime import datetime

auth_bp = Blueprint('auth', __name__)

@auth_bp.route('/api/login', methods=['POST'])
def login():
    id_token = request.json.get('idToken')
    try:
        decoded_token = auth.verify_id_token(id_token)
        email = decoded_token['email']
        full_name = decoded_token.get('name', '')

        name_parts = full_name.split(' ', 1)
        first_name = name_parts[0]
        last_name = name_parts[1] if len(name_parts) > 1 else ""

        db = get_db()
        user = db.users.find_one({"email": email})

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

        session['user_id'] = str(user['_id'])
        session['role'] = user['role']
        session['email'] = email
        session['user_name'] = user['name']

        print(f"✅ Login Success: {email} (Role: {user['role']})")
        return jsonify({"status": "success", "role": user['role']})

    except Exception as e:
        print(f"❌ Firebase Error: {str(e)}")
        return jsonify({"status": "error", "message": str(e)}), 401


@auth_bp.route('/logout')
def logout():
    session.clear()
    print("🔒 User logged out.")
    return redirect(url_for('index'))


# ✅ Profile — ดูและแก้ไขข้อมูลส่วนตัว (ทุก role)
@auth_bp.route('/profile', methods=['GET'])
def profile():
    if 'user_id' not in session:
        return redirect(url_for('index'))
    try:
        db = get_db()
        user = db.users.find_one({'_id': ObjectId(session['user_id'])})
        if not user:
            return redirect(url_for('index'))
        user['_id'] = str(user['_id'])

        app = None
        internship = None
        if session.get('role') == 'student':
            app = db.application_forms.find_one({'student_id': ObjectId(session['user_id'])})
            if app:
                app['_id'] = str(app['_id'])
                # ให้ ocr_data ไม่ None
                if not app.get('ocr_data'):
                    app['ocr_data'] = {'student_info': {}, 'family_info': {'father': {}, 'mother': {}}, 'emergency': {}}

            internship = db.internship_documents.find_one(
                {'student_id': ObjectId(session['user_id'])},
                sort=[('created_at', -1)]
            )
            if internship:
                internship['_id'] = str(internship['_id'])

        return render_template('profile.html', user=user, app=app, internship=internship)
    except Exception as e:
        return f"Error: {str(e)}", 500


@auth_bp.route('/profile', methods=['POST'])
def profile_update():
    if 'user_id' not in session:
        return jsonify({'success': False, 'message': 'Unauthorized'}), 401
    try:
        data = request.get_json()
        db = get_db()

        # อัปเดต users collection
        db.users.update_one(
            {'_id': ObjectId(session['user_id'])},
            {'$set': {
                'name':               data.get('name', ''),
                'profile.first_name': data.get('first_name', ''),
                'profile.last_name':  data.get('last_name', ''),
                'profile.phone':      data.get('phone', ''),
                'updated_at':         datetime.now()
            }}
        )
        session['user_name'] = data.get('name', session.get('user_name'))

        # อัปเดต application_forms (เฉพาะ student)
        if session.get('role') == 'student' and data.get('student_info'):
            db.application_forms.update_one(
                {'student_id': ObjectId(session['user_id'])},
                {'$set': {
                    'ocr_data.student_info': data['student_info'],
                    'ocr_data.family_info':  data.get('family_info', {}),
                    'ocr_data.emergency':    data.get('emergency', {}),
                    'updated_at':            datetime.now()
                }}
            )

        # อัปเดต internship_documents (เฉพาะ student ที่มีใบตอบรับ)
        if session.get('role') == 'student' and data.get('mentor'):
            db.internship_documents.update_one(
                {'student_id': ObjectId(session['user_id'])},
                {'$set': {
                    'ocr_extracted_data.mentor':           data['mentor'],
                    'ocr_extracted_data.internship_place': data.get('internship_place', {}),
                    'updated_at':                          datetime.now()
                }},
                sort=[('created_at', -1)]
            )

        return jsonify({'success': True, 'message': 'บันทึกข้อมูลเรียบร้อยแล้ว'})
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)}), 500