# routes/hod.py
from flask import Blueprint, render_template, session, redirect, url_for, jsonify, request
from bson import ObjectId
from datetime import datetime
from utils.db import get_collection

hod_bp = Blueprint('hod', __name__, url_prefix='/hod')

@hod_bp.route('/dashboard')
def dashboard():
    """หน้า Dashboard สำหรับ HOD"""
    # ตรวจสอบว่า login และเป็น HOD หรือไม่
    if 'user_id' not in session or session.get('role') != 'hod':
        return redirect(url_for('auth.login'))
    
    try:
        # ดึงเอกสารที่รออนุมัติ (status = 'pending')
        internship_documents = get_collection('internship_documents')
        documents = list(internship_documents.find({'status': 'pending'}).sort('created_at', -1))
        
        # แปลง ObjectId เป็น string
        for doc in documents:
            doc['_id'] = str(doc['_id'])
            if 'student_id' in doc:
                doc['student_id'] = str(doc['student_id'])
        
        return render_template('hod_dashboard.html', documents=documents)
        
    except Exception as e:
        print(f"❌ Error loading HOD dashboard: {e}")
        return f"Error: {str(e)}", 500

@hod_bp.route('/approve/<document_id>', methods=['POST'])
def approve(document_id):
    """อนุมัติเอกสาร"""
    if 'user_id' not in session or session.get('role') != 'hod':
        return jsonify({'success': False, 'message': 'Unauthorized'}), 403
    
    try:
        internship_documents = get_collection('internship_documents')
        
        result = internship_documents.update_one(
            {'_id': ObjectId(document_id)},
            {
                '$set': {
                    'status': 'approved',
                    'approved_by': session.get('user_id'),
                    'approved_at': datetime.now(),
                    'updated_at': datetime.now()
                }
            }
        )
        
        if result.modified_count > 0:
            return jsonify({'success': True, 'message': 'อนุมัติเอกสารเรียบร้อย'})
        else:
            return jsonify({'success': False, 'message': 'ไม่พบเอกสาร'}), 404
            
    except Exception as e:
        print(f"❌ Error approving document: {e}")
        return jsonify({'success': False, 'message': str(e)}), 500

@hod_bp.route('/reject/<document_id>', methods=['POST'])
def reject(document_id):
    """ส่งคืนเอกสาร"""
    if 'user_id' not in session or session.get('role') != 'hod':
        return jsonify({'success': False, 'message': 'Unauthorized'}), 403
    
    try:
        data = request.get_json()
        reason = data.get('reason', 'ไม่ระบุเหตุผล')
        
        internship_documents = get_collection('internship_documents')
        
        result = internship_documents.update_one(
            {'_id': ObjectId(document_id)},
            {
                '$set': {
                    'status': 'rejected',
                    'rejected_by': session.get('user_id'),
                    'rejected_at': datetime.now(),
                    'reject_reason': reason,
                    'updated_at': datetime.now()
                }
            }
        )
        
        if result.modified_count > 0:
            return jsonify({'success': True, 'message': 'ส่งคืนเอกสารเรียบร้อย'})
        else:
            return jsonify({'success': False, 'message': 'ไม่พบเอกสาร'}), 404
            
    except Exception as e:
        print(f"❌ Error rejecting document: {e}")
        return jsonify({'success': False, 'message': str(e)}), 500

@hod_bp.route('/assign-advisor')
def assign_advisor():
    """หน้ามอบหมายอาจารย์ที่ปรึกษา (ถ้ามี)"""
    if 'user_id' not in session or session.get('role') != 'hod ':
        return redirect(url_for('auth.login'))
    
    # TODO: เพิ่มฟังก์ชันมอบหมายอาจารย์ที่ปรึกษา
    return "<h1>หน้ามอบหมายอาจารย์ที่ปรึกษา (Coming Soon)</h1>"