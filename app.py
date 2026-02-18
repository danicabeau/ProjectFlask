from flask import Flask, render_template, session, redirect, url_for, jsonify
from config import config
import os
import firebase_admin
from firebase_admin import credentials

# Import ยูทิลิตี้และเส้นทาง (Routes) ของเรา
from utils.db import get_db
from routes.upload import upload_bp
from routes.auth import auth_bp
from routes.hod import hod_bp # สำหรับระบบจัดการของหัวหน้าภาค

def create_app(config_name='development'):
    app = Flask(__name__)
    
    # 1. โหลดค่าคอนฟิกูเรชันจากไฟล์ config.py
    app.config.from_object(config[config_name])
    
    # 2. สร้างโฟลเดอร์สำหรับเก็บไฟล์อัปโหลด (ถ้ายังไม่มี)
    if not os.path.exists(app.config['UPLOAD_FOLDER']):
        os.makedirs(app.config['UPLOAD_FOLDER'])
        print(f"📁 Created upload folder at: {app.config['UPLOAD_FOLDER']}")

    # 3. ตั้งค่า Firebase Admin SDK (ใช้กุญแจ Master Key)
    # เราใช้เงื่อนไขเช็คเพื่อไม่ให้มัน Initialize ซ้ำเวลา Flask รีโหลด
    if not firebase_admin._apps:
        try:
            # ใช้ path ตามที่คุณระบุไว้ หรือปรับให้ตรงกับที่วางไฟล์จริงนะครับ
            cred_path = 'serviceAccountKey.json'
            cred = credentials.Certificate(cred_path)
            firebase_admin.initialize_app(cred)
            print("🔥 Firebase Admin initialized successfully.")
        except Exception as e:
            print(f"⚠️ Firebase initialization failed: {e}")

    # 4. เชื่อมต่อฐานข้อมูลและลงทะเบียน Blueprint
    with app.app_context():
        # ทดสอบการเชื่อมต่อ MongoDB
        database = get_db()
        print(f"✅ Flask app connected to database: {database.name}")
        
        # ลงทะเบียนระบบต่าง ๆ ของแอป
        app.register_blueprint(upload_bp)  # ระบบอัปโหลดและ OCR
        app.register_blueprint(auth_bp)    # ระบบ Login (Google/Firebase)
        app.register_blueprint(hod_bp)     # ระบบนัดนิเทศของหัวหน้าภาค

    # --- เส้นทางหลัก (Routes) ---
# --- เส้นทางหลัก (Routes) ---

    @app.route('/')
    def index():
        # แก้ไข Logic: ถ้า "ไม่มี" user_id ใน session (คือยังไม่ได้ Login)
        if 'user_id' not in session:
            # ใช้แค่ 'login.html' พอครับ เพราะ Flask จะหาในโฟลเดอร์ templates ให้อัตโนมัติ
            return render_template('login.html') 
        
        # ถ้า Login แล้ว (มี user_id) ให้เด้งไปหน้าอัปโหลดเอกสาร
        return redirect(url_for('upload.upload_page'))
    # --- ระบบจัดการข้อผิดพลาด (Error Handlers) ---

    @app.errorhandler(404)
    def not_found(error):
        return "<h1>404 - ไม่พบหน้านี้ครับ Krittiya</h1>", 404

    @app.errorhandler(500)
    def internal_error(error):
        return "<h1>500 - เซิร์ฟเวอร์มีปัญหา ลองเช็ค Log ดูนะ</h1>", 500

    return app

# ส่วนสำหรับรันแอปพลิเคชัน
if __name__ == '__main__':
    # รันบน host 0.0.0.0 เพื่อให้เข้าถึงได้จาก device อื่นในวงแลนเดียวกัน
    app = create_app('development')
    app.run(host='0.0.0.0', port=5000, debug=True, use_reloader=False)