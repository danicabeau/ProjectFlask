from flask import Blueprint, render_template, session, redirect, url_for, jsonify, request, flash
from bson import ObjectId
from datetime import datetime
from utils.db import get_collection

hod_bp = Blueprint('hod', __name__, url_prefix='/hod')

@hod_bp.route('/dashboard')
def dashboard():
    if 'user_id' not in session or session.get('role') != 'hod':
        return redirect(url_for('auth.login'))
    
    docs = list(get_collection('internship_documents').find({'status': 'pending'}))
    for d in docs: d['_id'] = str(d['_id'])
    return render_template('hod_dashboard.html', documents=docs)


@hod_bp.route('/appointments')
def appointments():
    """ประวัติการนัดหมายนิเทศทั้งหมดในระบบ (HOD เห็นทุกคน)"""
    if 'user_id' not in session or session.get('role') != 'hod':
        return redirect(url_for('auth.login'))

    db = get_collection('users').database

    # ดึงทุก assignment ในระบบ เรียงล่าสุดก่อน
    assignments = list(db['advisor_assignments'].find(
        {}, sort=[('assigned_date', -1)]
    ))

    history = []
    for a in assignments:
        doc_id = a.get('internship_document_id')
        doc = db['internship_documents'].find_one({'_id': doc_id}) if doc_id else {}
        ocr = doc.get('ocr_extracted_data', {}) if doc else {}

        history.append({
            '_id':         str(a['_id']),
            'status':      a.get('status', 'assigned'),
            'assigned_date': a.get('assigned_date'),
            'student':     a.get('student', {}),
            'mentor':      a.get('mentor', {}),
            'appointment': a.get('appointment', {}),
            'company':     ocr.get('internship_place', {}),
        })

    return render_template('hod_appointments.html', history=history)


@hod_bp.route('/approve/<id>', methods=['POST'])
def approve(id):
    get_collection('internship_documents').update_one(
        {'_id': ObjectId(id)},
        {'$set': {'status': 'approved', 'updated_at': datetime.now()}}
    )
    return jsonify({'success': True})


@hod_bp.route('/reject/<id>', methods=['POST'])
def reject(id):
    get_collection('internship_documents').delete_one({'_id': ObjectId(id)})
    return jsonify({'success': True})


@hod_bp.route('/manage-supervisors')
def manage_supervisors():
    if 'user_id' not in session or session.get('role') != 'hod':
        return redirect(url_for('auth.login'))

    try:
        users_col = get_collection('users')
        lecturers = list(users_col.find({'role': {'$ne': 'student'}}))
        for l in lecturers:
            l['_id'] = str(l['_id'])
        return render_template('hod_manage_supervisors.html', lecturers=lecturers)
    except Exception as e:
        print(f"Error: {e}")
        return str(e), 500