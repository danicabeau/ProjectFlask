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
    return render_template('upload.html')

@upload_bp.route('/upload', methods=['POST'])
def upload_file():
    """จัดการการอัปโหลดไฟล์และ OCR (ยังไม่บันทึกลง DB)"""
    try:
        # ตรวจสอบว่ามีไฟล์ในคำขอหรือไม่
        if 'file' not in request.files:
            return jsonify({
                'success': False,
                'message': 'ไม่พบไฟล์ในคำขอ'
            }), 400
        
        file = request.files['file']
        
        # ตรวจสอบว่าผู้ใช้เลือกไฟล์หรือไม่
        if file.filename == '':
            return jsonify({
                'success': False,
                'message': 'ไม่ได้เลือกไฟล์'
            }), 400
        
        # ตรวจสอบประเภทไฟล์
        if not allowed_file(file.filename):
            return jsonify({
                'success': False,
                'message': 'ประเภทไฟล์ไม่ถูกต้อง (อนุญาตเฉพาะ PDF, PNG, JPG, JPEG)'
            }), 400
        
        # สร้างชื่อไฟล์ที่ปลอดภัย
        filename = secure_filename(file.filename)
        
        # เพิ่ม timestamp เพื่อป้องกันชื่อไฟล์ซ้ำ
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        name, ext = os.path.splitext(filename)
        filename = f"{name}_{timestamp}{ext}"
        
        # สร้างโฟลเดอร์ตามวันที่
        upload_date = datetime.now().strftime('%Y/%m/%d')
        upload_path = os.path.join(Config.UPLOAD_FOLDER, upload_date)
        os.makedirs(upload_path, exist_ok=True)
        
        # บันทึกไฟล์
        file_path = os.path.join(upload_path, filename)
        file.save(file_path)
        
        # กำหนดประเภทไฟล์
        file_type = 'pdf' if ext.lower() == '.pdf' else 'image'
        
        # ⭐ เรียกใช้ OCR แต่ยังไม่บันทึกลง DB ⭐
        print(f"🔍 Processing OCR for: {file_path}")
        ocr_result = process_document_ocr(file_path, file_type)
        
        # ตรวจสอบผลลัพธ์ OCR
        if ocr_result['success']:
            ocr_data = ocr_result['data']
            ocr_confidence = ocr_result.get('confidence', 0.0)
            print(f"✅ OCR Success! Confidence: {ocr_confidence}")
        else:
            print(f"❌ OCR Failed: {ocr_result.get('error', 'Unknown error')}")
            ocr_data = {
                'student': {},
                'internship_place': {},
                'mentor': {},
                'internship_period': {}
            }
            ocr_confidence = 0.0
        
        # ส่งข้อมูลกลับไปให้ user ตรวจสอบ (ยังไม่บันทึกลง DB)
        return jsonify({
            'success': True,
            'message': 'อ่านข้อมูลจากเอกสารสำเร็จ กรุณาตรวจสอบความถูกต้อง',
            'data': {
                'filename': filename,
                'file_path': file_path,
                'file_type': file_type,
                'ocr_data': ocr_data,
                'ocr_confidence': ocr_confidence
            }
        }), 200
        
    except Exception as e:
        print(f"❌ Upload Error: {str(e)}")
        return jsonify({
            'success': False,
            'message': f'เกิดข้อผิดพลาด: {str(e)}'
        }), 500

@upload_bp.route('/save-document', methods=['POST'])
def save_document():
    """บันทึกข้อมูลลง database หลังจาก user ตรวจสอบแล้ว"""
    try:
        data = request.get_json()
        
        # ดึงข้อมูลจาก request
        filename = data.get('filename')
        file_path = data.get('file_path')
        file_type = data.get('file_type')
        ocr_data = data.get('ocr_data')
        ocr_confidence = data.get('ocr_confidence', 0.0)
        
        # แปลง date strings เป็น datetime objects
        internship_period = ocr_data.get('internship_period', {})
        if internship_period.get('start_date'):
            try:
                start_date_str = internship_period['start_date']
                if start_date_str:
                    internship_period['start_date'] = datetime.strptime(start_date_str, '%Y-%m-%d')
                else:
                    internship_period['start_date'] = None
            except:
                internship_period['start_date'] = None
        
        if internship_period.get('end_date'):
            try:
                end_date_str = internship_period['end_date']
                if end_date_str:
                    internship_period['end_date'] = datetime.strptime(end_date_str, '%Y-%m-%d')
                else:
                    internship_period['end_date'] = None
            except:
                internship_period['end_date'] = None
        
        # บันทึกลง MongoDB
        internship_documents = get_collection('internship_documents')
        
        document_data = {
            'student_id': ObjectId(),  # TODO: ใช้ student_id จาก session จริง
            'document_info': {
                'file_name': filename,
                'file_path': file_path,
                'file_type': file_type,
                'upload_date': datetime.now()
            },
            'ocr_extracted_data': ocr_data,
            'ocr_confidence': ocr_confidence,
            'is_verified': True,  # เปลี่ยนเป็น True เพราะ user ตรวจสอบแล้ว
            'status': 'pending',
            'created_at': datetime.now(),
            'updated_at': datetime.now()
        }
        
        result = internship_documents.insert_one(document_data)
        
        # อัปเดตหรือเพิ่มข้อมูลบริษัท
        company_name = ocr_data.get('internship_place', {}).get('company_name')
        if company_name and company_name.strip():
            companies = get_collection('companies')
            existing_company = companies.find_one({'company_name': company_name})
            
            if existing_company:
                # อัปเดตจำนวนนักศึกษา
                companies.update_one(
                    {'_id': existing_company['_id']},
                    {
                        '$inc': {'internship_count': 1},
                        '$set': {'updated_at': datetime.now()}
                    }
                )
            else:
                # สร้างบริษัทใหม่
                companies.insert_one({
                    'company_name': company_name,
                    'address': ocr_data.get('internship_place', {}).get('address', ''),
                    'province': ocr_data.get('internship_place', {}).get('province', ''),
                    'country': ocr_data.get('internship_place', {}).get('country', 'ประเทศไทย'),
                    'internship_count': 1,
                    'created_at': datetime.now(),
                    'updated_at': datetime.now()
                })
        
        return jsonify({
            'success': True,
            'message': 'บันทึกข้อมูลลงฐานข้อมูลสำเร็จ',
            'document_id': str(result.inserted_id)
        }), 200
        
    except Exception as e:
        print(f"❌ Save Error: {str(e)}")
        return jsonify({
            'success': False,
            'message': f'เกิดข้อผิดพลาดในการบันทึก: {str(e)}'
        }), 500

@upload_bp.route('/documents', methods=['GET'])
def list_documents():
    """แสดงรายการเอกสารทั้งหมด"""
    try:
        internship_documents = get_collection('internship_documents')
        
        # ดึงข้อมูลเอกสารทั้งหมด
        documents = list(internship_documents.find().sort('created_at', -1))
        
        # แปลง ObjectId เป็น string
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
        
        # แปลง ObjectId เป็น string
        document['_id'] = str(document['_id'])
        document['student_id'] = str(document['student_id'])
        
        return render_template('document_detail.html', document=document)
        
    except Exception as e:
        return f"Error: {str(e)}", 500