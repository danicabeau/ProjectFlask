# app.py
from flask import Flask, render_template, session, redirect, url_for
from config import config
import os

from utils.db import get_db
from routes.upload import upload_bp  # เพิ่มบรรทัดนี้

def create_app(config_name='development'):
    app = Flask(__name__)
    
    # Load configuration
    app.config.from_object(config[config_name])
    
    # สร้างโฟลเดอร์สำหรับ uploads
    if not os.path.exists(app.config['UPLOAD_FOLDER']):
        os.makedirs(app.config['UPLOAD_FOLDER'])
    
    # เชื่อมต่อ MongoDB
    with app.app_context():
        database = get_db()
        print(f"✅ Flask app connected to database: {database.name}")
    
    # Register blueprints
    app.register_blueprint(upload_bp)  # เพิ่มบรรทัดนี้
    
    # Home route
    @app.route('/')
    def index():
        return redirect(url_for('upload.upload_page'))
    
    # Error handlers
    @app.errorhandler(404)
    def not_found(error):
        return "<h1>404 - Page Not Found</h1>", 404
    
    @app.errorhandler(500)
    def internal_error(error):
        return "<h1>500 - Internal Server Error</h1>", 500
    
    return app

if __name__ == '__main__':
    app = create_app('development')
    app.run(host='0.0.0.0', port=5000, debug=True, use_reloader=False)