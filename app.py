from flask import Flask, render_template, session, redirect, url_for, jsonify
from config import config
import os
import firebase_admin
from firebase_admin import credentials

# Import ยูทิลิตี้และเส้นทาง (Routes)
from utils.db import get_db
from routes.upload import upload_bp
from routes.auth import auth_bp
from routes.hod import hod_bp

def create_app(config_name='development'):
    app = Flask(__name__)
    
    # 1. Config
    app.config.from_object(config[config_name])
    
    # 2. Create Upload Folder
    if not os.path.exists(app.config['UPLOAD_FOLDER']):
        os.makedirs(app.config['UPLOAD_FOLDER'])
        print(f"📁 Created upload folder at: {app.config['UPLOAD_FOLDER']}")

    # 3. Firebase Init
    if not firebase_admin._apps:
        try:
            cred_path = 'serviceAccountKey.json'
            cred = credentials.Certificate(cred_path)
            firebase_admin.initialize_app(cred)
            print("🔥 Firebase Admin initialized successfully.")
        except Exception as e:
            print(f"⚠️ Firebase initialization failed: {e}")

    # 4. Database & Blueprints
    with app.app_context():
        database = get_db()
        print(f"✅ Flask app connected to database: {database.name}")
        
        app.register_blueprint(upload_bp)
        app.register_blueprint(auth_bp)
        app.register_blueprint(hod_bp)

    # --- เส้นทางหลัก (Routes) ---

    @app.route('/')
    def index():
        # 1. ถ้ายังไม่ Login -> ไปหน้า Login
        if 'user_id' not in session:
            return render_template('login.html') 
        
        # 2. ถ้า Login แล้ว -> เช็ค Role
        user_role = session.get('role')
        
        # ⭐ ถ้าเป็น HOD ให้ไปหน้า Dashboard อนุมัติ
        if user_role == 'hod':
            # ต้องเป็น hod.dashboard ไม่ใช่ hod.assign_advisor หรือชื่ออื่น
            return redirect(url_for('hod.dashboard'))
            
        # ⭐ ถ้าเป็นนักศึกษา (หรืออื่นๆ) ให้ไปหน้าอัปโหลดเอกสาร
        return redirect(url_for('upload.upload_page'))

    # --- Error Handlers ---

    @app.errorhandler(404)
    def not_found(error):
        return "<h1>404 - ไม่พบหน้านี้ครับ</h1>", 404

    @app.errorhandler(500)
    def internal_error(error):
        return "<h1>500 - เซิร์ฟเวอร์มีปัญหา</h1>", 500

    return app

if __name__ == '__main__':
    app = create_app('development')
    app.run(host='0.0.0.0', port=5000, debug=True, use_reloader=False)