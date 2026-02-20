from flask import Blueprint, render_template, session, redirect, url_for, flash, jsonify, request
from bson import ObjectId
from datetime import datetime
from utils.db import get_collection
from flask_mail import Message
from extenstions import mail

lecturer_bp = Blueprint('lecturer', __name__, url_prefix='/lecturer')

@lecturer_bp.route('/dashboard')
def dashboard():
    """ดูรายชื่อนักศึกษาที่ดูแลอยู่ (เฉพาะของตัวเอง)"""
    if 'user_id' not in session:
        return redirect(url_for('auth.login'))

    db = get_collection('users').database

    # ✅ แก้จาก lecturer_id → advisor_id ให้ตรง schema
    assignments = list(db['advisor_assignments'].find({
        'advisor_id': ObjectId(session['user_id'])
    }))

    # ✅ รวมข้อมูลจาก assignment + internship_document เข้าด้วยกัน
    students = []
    for a in assignments:
        doc_id = a.get('internship_document_id')
        doc = db['internship_documents'].find_one({'_id': doc_id}) if doc_id else {}

        students.append({
            '_id':        str(a['_id']),
            'doc_id':     str(doc_id) if doc_id else '',
            'status':     a.get('status', 'assigned'),
            'student':    a.get('student', {}),
            'mentor':     a.get('mentor', {}),
            'appointment':a.get('appointment', {}),
            'ocr':        doc.get('ocr_extracted_data', {}) if doc else {},
            'company':    doc.get('ocr_extracted_data', {}).get('internship_place', {}) if doc else {},
        })

    return render_template('lecturer_dashboard.html', students=students)


@lecturer_bp.route('/claim-students')
def claim_page():
    """เลือกนักศึกษาใหม่: เช็คสิทธิ์ can_supervise"""
    if 'user_id' not in session:
        return redirect(url_for('auth.login'))

    user = get_collection('users').find_one({'_id': ObjectId(session['user_id'])})
    if not user or not user.get('can_supervise'):
        flash("คุณไม่มีสิทธิ์นิเทศนักศึกษา กรุณาติดต่อ HOD", "warning")
        return redirect(url_for('lecturer.dashboard'))

    db = get_collection('users').database
    

    # ✅ แก้จาก document_id → internship_document_id ให้ตรง schema
    assigned_ids = db['advisor_assignments'].distinct('internship_document_id')
    available = list(db['internship_documents'].find({
        'status': 'approved',
        '_id': {'$nin': assigned_ids}
    }))
    for s in available:
        s['_id'] = str(s['_id'])
    lecturer_email = user.get('email', '')  # ดึงจาก user object ที่ query ไว้แล้ว
    return render_template('lecturer_claim.html', students=available, lecturer_email=lecturer_email)



@lecturer_bp.route('/api/get-student-info/<doc_id>')
def get_student_info(doc_id):
    """ดึงข้อมูลจาก OCR ตามโครงสร้างใน MongoDB"""
    try:
        db = get_collection('users').database
        doc = db['internship_documents'].find_one({'_id': ObjectId(doc_id)})

        if not doc:
            return jsonify({'success': False, 'message': 'ไม่พบเอกสาร'})

        ocr = doc.get('ocr_extracted_data', {})
        std = ocr.get('student', {})
        mnt = ocr.get('mentor', {})

        return jsonify({
            'success': True,
            'data': {
                's_name':  f"{std.get('first_name', '')} {std.get('last_name', '')}".strip(),
                's_email': std.get('email', ''),
                's_phone': std.get('phone', ''),
                'm_name':  f"{mnt.get('first_name', '')} {mnt.get('last_name', '')}".strip(),
                'm_email': mnt.get('email', ''),
                'm_phone': mnt.get('phone', '')
            }
        })
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)})


@lecturer_bp.route('/api/confirm-appointment', methods=['POST'])
def confirm_appointment():
    if 'user_id' not in session:
        return jsonify({'success': False}), 401

    try:
        data = request.json
        db = get_collection('users').database

        doc_id    = ObjectId(data.get('doc_id'))
        advisor_id = ObjectId(session['user_id'])

        doc        = db['internship_documents'].find_one({'_id': doc_id})
        student_id = doc.get('student_id') if doc else doc_id

        appointment_data = {
            # ✅ Required fields ตาม schema
            'student_id':    student_id,
            'advisor_id':    advisor_id,
            'assigned_by':   advisor_id,
            'assigned_date': datetime.utcnow(),
            'status':        'scheduled',  # ✅ ใช้ scheduled (ต้องเพิ่มใน schema enum ด้วย)
            'created_at':    datetime.utcnow(),

            # Optional fields
            'internship_document_id': doc_id,
            'updated_at':    datetime.utcnow(),
            'notes':         '',

            'appointment': {
                'date':           data.get('date'),
                'time':           data.get('time'),
                'eval_link':      data.get('eval_link'),
                'lecturer_name':  data.get('l_name'),
                'lecturer_phone': data.get('l_phone'),
            },
            'mentor': {
                'name':  data.get('m_name'),
                'email': data.get('m_email'),
                'phone': data.get('m_phone'),
            },
            'student': {
                'name':  data.get('s_name'),
                'email': data.get('s_email'),
                'phone': data.get('s_phone'),
            },
        }
        db['advisor_assignments'].insert_one(appointment_data)

        # ส่ง Email แจ้งเตือนทั้ง 3 ฝ่าย
        recipients = [data.get('s_email'), data.get('m_email'), session.get('email')]
        recipients = [r for r in recipients if r]

        msg = Message(
            subject=f"แจ้งการนัดหมายนิเทศนักศึกษา: {data.get('s_name')}",
            recipients=recipients,
            body=f"""
เรียนทุกท่าน,

แจ้งรายละเอียดการนัดหมายนิเทศงานนักศึกษาฝึกงานดังนี้:

- นักศึกษา:           {data.get('s_name')}
- อาจารย์ผู้นิเทศ:    {data.get('l_name')} (โทร: {data.get('l_phone')})
- พี่เลี้ยง (Mentor): {data.get('m_name')}

วันเวลาที่นัดหมาย: {data.get('date')} เวลา {data.get('time')} น.
ลิงก์สำหรับทำแบบประเมิน: {data.get('eval_link')}

จึงเรียนมาเพื่อโปรดเตรียมความพร้อม
ระบบจัดการการฝึกงาน (Internship System)
            """
        )
        mail.send(msg)

        return jsonify({'success': True})
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)}), 500