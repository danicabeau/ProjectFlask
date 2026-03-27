# app.py
from flask import Flask, render_template, session, redirect, url_for, jsonify
from config import config
import os
import firebase_admin
from firebase_admin import credentials
from extenstions import mail

# Import ยูทิลิตี้และเส้นทาง
from utils.db import get_db
from routes.upload import upload_bp
from routes.auth import auth_bp
from routes.hod import hod_bp
from routes.lecturer import lecturer_bp
from routes.company import company_bp


def create_app(config_name='development'):
    app = Flask(__name__)
    
    # 1. Config หลัก
    app.config.from_object(config[config_name])
    
    # 2. ตั้งค่า Mail
    app.config['MAIL_SERVER'] = 'smtp.gmail.com'
    app.config['MAIL_PORT'] = 587
    app.config['MAIL_USE_TLS'] = True
    app.config['MAIL_USERNAME'] = 'inturnshipweb@gmail.com'
    app.config['MAIL_PASSWORD'] = 'tthbhbogumvsiihf' 
    app.config['MAIL_DEFAULT_SENDER'] = 'inturnshipweb@gmail.com'
    
    # ⭐ ผูก Mail เข้ากับ App
    mail.init_app(app)
    
    # 3. สร้าง Upload Folder
    if not os.path.exists(app.config['UPLOAD_FOLDER']):
        os.makedirs(app.config['UPLOAD_FOLDER'])

    # 4. Firebase Init
    if not firebase_admin._apps:
        try:
            cred = credentials.Certificate('serviceAccountKey.json')
            firebase_admin.initialize_app(cred)
            print("🔥 Firebase initialized.")
        except Exception as e:
            print(f"⚠️ Firebase failed: {e}")

    # 5. Database & Blueprints
    with app.app_context():
        database = get_db()
        app.register_blueprint(upload_bp)
        app.register_blueprint(auth_bp)
        app.register_blueprint(hod_bp)
        app.register_blueprint(lecturer_bp)
        app.register_blueprint(company_bp)

    # 6. Routes หลัก
    @app.route('/')
    def index():
        if 'user_id' not in session:
            return render_template('login.html') 
        
        user_role = session.get('role')
        if user_role == 'hod':
            return redirect(url_for('hod.dashboard'))
        elif user_role == 'lecturer':
            return redirect(url_for('lecturer.dashboard'))
        return redirect(url_for('upload.upload_page'))

    # --- Error Handlers ---
    @app.errorhandler(404)
    def not_found(error): return "<h1>404 - ไม่พบหน้านี้ครับ</h1>", 404

    return app

if __name__ == '__main__':
    app = create_app('development')
    app.run(host='0.0.0.0', port=5000, debug=True, use_reloader=False)