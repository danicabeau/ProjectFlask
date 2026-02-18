# routes/upload.py
from flask import Blueprint, render_template, request, jsonify, session, redirect, url_for
from werkzeug.utils import secure_filename
import os
from datetime import datetime
from bson import ObjectId

from utils.db import get_collection
from utils.ocr_typhoon import process_document_ocr
from config import Config

upload_bp = Blueprint('upload', __name__)

def allowed_file(filename):
    """ตรวจสอบว่าไฟล์ที่อัปโหลดเป็นประเภทที่อนุญาตหรือไม่"""
    return '.' in filename and \
           filename.rsplit('.', 1)[1].lower() in Config.ALLOWED_EXTENSIONS

@upload_bp.route('/upload', methods=['GET'])
def upload_page():
    """แสดงหน้าอัปโหลดไฟล์"""
    # ตรวจสอบ Login ก่อนเข้าหน้าอัปโหลด
    if 'user_id' not in session:
        return redirect(url_for('auth.login_page'))
    return render_template('upload.html')

@upload_bp.route('/upload', methods=['POST'])
def upload_file():
    """จัดการการอัปโหลดไฟล์และ OCR (ยังไม่บันทึกลง DB)"""
    try:
        if 'file' not in request.files:
            return jsonify({'success': False, 'message': 'ไม่พบไฟล์ในคำขอ'}), 400
        
        file = request.files['file']
        if file.filename == '':
            return jsonify({'success': False, 'message': 'ไม่ได้เลือกไฟล์'}), 400
        
        if not allowed_file(file.filename):
            return jsonify({'success': False, 'message': 'ประเภทไฟล์ไม่ถูกต้อง'}), 400
        
        filename = secure_filename(file.filename)
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        name, ext = os.path.splitext(filename)
        filename = f"{name}_{timestamp}{ext}"
        
        upload_date = datetime.now().strftime('%Y/%m/%d')
        upload_path = os.path.join(Config.UPLOAD_FOLDER, upload_date)
        os.makedirs(upload_path, exist_ok=True)
        
        file_path = os.path.join(upload_path, filename)
        file.save(file_path)
        
        file_type = 'pdf' if ext.lower() == '.pdf' else 'image'
        
        # เรียกใช้ OCR
        ocr_result = process_document_ocr(file_path, file_type)
        
        return jsonify({
            'success': True,
            'message': 'อ่านข้อมูลจากเอกสารสำเร็จ',
            'data': {
                'filename': filename,
                'file_path': file_path,
                'file_type': file_type,
                'ocr_data': ocr_result.get('data', {}),
                'ocr_confidence': ocr_result.get('confidence', 0.0)
            }
        }), 200
        
    except Exception as e:
        return jsonify({'success': False, 'message': f'เกิดข้อผิดพลาด: {str(e)}'}), 500

@upload_bp.route('/save-document', methods=['POST'])
def save_document():
    """บันทึกข้อมูลลง database หลังจาก user ตรวจสอบแล้ว"""
    try:
        # ตรวจสอบว่า Login หรือยัง
        if 'user_id' not in session:
            return jsonify({'success': False, 'message': 'Session expired'}), 401

        data = request.get_json()
        ocr_data = data.get('ocr_data')
        
        # แปลงวันที่จาก String เป็น Datetime Object
        internship_period = ocr_data.get('internship_period', {})
        for date_key in ['start_date', 'end_date']:
            if internship_period.get(date_key):
                try:
                    internship_period[date_key] = datetime.strptime(internship_period[date_key], '%Y-%m-%d')
                except:
                    internship_period[date_key] = None

        internship_documents = get_collection('internship_documents')
        
        document_data = {
            # ⭐ แก้ไข: ใช้ student_id จาก session ของคนที่ Login อยู่จริง
            'student_id': ObjectId(session.get('user_id')), 
            'document_info': {
                'file_name': data.get('filename'),
                'file_path': data.get('file_path'),
                'file_type': data.get('file_type'),
                'upload_date': datetime.now()
            },
            'ocr_extracted_data': ocr_data,
            'ocr_confidence': data.get('ocr_confidence', 0.0),
            'is_verified': True,
            'status': 'pending',
            'created_at': datetime.now(),
            'updated_at': datetime.now()
        }
        
        result = internship_documents.insert_one(document_data)
        
        # ส่วนอัปเดตข้อมูลบริษัท
        company_name = ocr_data.get('internship_place', {}).get('company_name')
        if company_name:
            companies = get_collection('companies')
            companies.update_one(
                {'company_name': company_name},
                {
                    '$inc': {'internship_count': 1},
                    '$set': {'updated_at': datetime.now()}
                },
                upsert=True
            )
        
        return jsonify({'success': True, 'message': 'บันทึกข้อมูลสำเร็จ', 'document_id': str(result.inserted_id)}), 200
        
    except Exception as e:
        return jsonify({'success': False, 'message': f'Save Error: {str(e)}'}), 500

@upload_bp.route('/documents', methods=['GET'])
def list_documents():
    """แสดงรายการเอกสารเฉพาะของนักศึกษาที่ Login อยู่"""
    if 'user_id' not in session:
        return redirect(url_for('auth.login_page'))
    
    try:
        internship_documents = get_collection('internship_documents')
        
        # ⭐ จุดสำคัญ: กรองด้วย student_id ของตัวเองเท่านั้น
        user_id = session.get('user_id')
        query = {'student_id': ObjectId(user_id)}
        
        # ดึงข้อมูลมาเฉพาะของตัวเอง และเรียงลำดับจากล่าสุดขึ้นก่อน
        documents = list(internship_documents.find(query).sort('created_at', -1))
        
        for doc in documents:
            doc['_id'] = str(doc['_id'])
            doc['student_id'] = str(doc['student_id'])
        
        return render_template('documents.html', documents=documents)
        
    except Exception as e:
        return f"Error: {str(e)}", 500

@upload_bp.route('/document/<document_id>', methods=['GET'])
def view_document(document_id):
    """ดูรายละเอียดเอกสาร"""
    try:
        internship_documents = get_collection('internship_documents')
        document = internship_documents.find_one({'_id': ObjectId(document_id)})
        
        if not document:
            return "ไม่พบเอกสาร", 404
        
        # เช็คสิทธิ์: ถ้าไม่ใช่เจ้าของและไม่ใช่ HOD ห้ามดู (Optional Security)
        if str(document['student_id']) != session.get('user_id') and session.get('role') != 'hod':
            return "คุณไม่มีสิทธิ์เข้าถึงเอกสารนี้", 403
            
        document['_id'] = str(document['_id'])
        document['student_id'] = str(document['student_id'])
        
        return render_template('document_detail.html', document=document)
    except Exception as e:
        return f"Error: {str(e)}", 500