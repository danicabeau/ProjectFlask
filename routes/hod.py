from flask import Blueprint, render_template, session
from utils.db import get_db

hod_bp = Blueprint('hod', __name__, url_prefix='/hod')

@hod_bp.route('/dashboard')
def dashboard():
    # เดี๋ยวเราค่อยมาเขียนฟีเจอร์นัดนิเทศตรงนี้ครับ
    return "<h1>HOD Dashboard - ระบบหัวหน้าภาค</h1>"