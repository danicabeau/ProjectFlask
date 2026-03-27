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

    assignments = list(db['advisor_assignments'].find({
        'advisor_id': ObjectId(session['user_id'])
    }))

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

    assigned_ids = db['advisor_assignments'].distinct('internship_document_id')
    available_docs = list(db['internship_documents'].find({
        'status': 'approved',
        '_id': {'$nin': assigned_ids}
    }))

    # ✅ รวมข้อมูลจาก application_forms เพื่อดึงชื่อนักศึกษา
    available = []
    for doc in available_docs:
        doc['_id'] = str(doc['_id'])
        ocr = doc.get('ocr_extracted_data', {})

        # ดึงข้อมูลนักศึกษาจาก application_forms โดยใช้ student_id
        student_id = doc.get('student_id')
        app_form = None
        if student_id:
            app_form = db['application_forms'].find_one({'student_id': student_id})

        student_info = {}
        if app_form:
            si = app_form.get('ocr_data', {}).get('student_info', {})
            student_info = {
                'first_name': si.get('name_th', ''),
                'last_name': '',
                'major': si.get('year', ''),
                'student_id_number': si.get('student_id', ''),
            }
        
        # ดึงจาก mentor ใน ocr_extracted_data
        mentor = ocr.get('mentor', {})

        # สร้างโครงสร้างที่ template ต้องการ
        doc['student_info'] = student_info
        doc['internship_place'] = ocr.get('internship_place', {})
        doc['mentor_info'] = mentor
        available.append(doc)

    lecturer_email = user.get('email', '')
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
        mnt = ocr.get('mentor', {})

        # ✅ ดึงข้อมูลนักศึกษาจาก application_forms
        student_id = doc.get('student_id')
        s_name = ''
        s_email = ''
        s_phone = ''

        if student_id:
            app_form = db['application_forms'].find_one({'student_id': student_id})
            if app_form:
                si = app_form.get('ocr_data', {}).get('student_info', {})
                s_name = si.get('name_th', '')
                s_phone = si.get('phone', '')
                # อีเมลอาจอยู่ใน internship_place
                s_email = ocr.get('internship_place', {}).get('email', '')

        # ✅ mentor มี first_name (ชื่อเต็มรวมนามสกุล) และ phone
        m_name = mnt.get('first_name', '')
        m_phone = mnt.get('phone', '')

        return jsonify({
            'success': True,
            'data': {
                's_name':  s_name,
                's_email': s_email,
                's_phone': s_phone,
                'm_name':  m_name,
                'm_email': '',
                'm_phone': m_phone
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
            'student_id':    student_id,
            'advisor_id':    advisor_id,
            'assigned_by':   advisor_id,
            'assigned_date': datetime.utcnow(),
            'status':        'scheduled',
            'created_at':    datetime.utcnow(),

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


@lecturer_bp.route('/my-appointments')
def my_appointments():
    """ประวัติการนัดหมายของตัวเอง"""
    if 'user_id' not in session:
        return redirect(url_for('auth.login'))

    db = get_collection('users').database
    assignments = list(db['advisor_assignments'].find(
        {'advisor_id': ObjectId(session['user_id'])},
        sort=[('assigned_date', -1)]
    ))

    history = []
    for a in assignments:
        doc_id = a.get('internship_document_id')
        doc = db['internship_documents'].find_one({'_id': doc_id}) if doc_id else {}
        ocr = doc.get('ocr_extracted_data', {}) if doc else {}
        history.append({
            '_id':         str(a['_id']),
            'status':      a.get('status', 'assigned'),
            'student':     a.get('student', {}),
            'mentor':      a.get('mentor', {}),
            'appointment': a.get('appointment', {}),
            'company':     ocr.get('internship_place', {}),
        })

    return render_template('lecturer_appointments.html', history=history)


@lecturer_bp.route('/api/update-appointment', methods=['POST'])
def update_appointment():
    """แก้ไขการนัดหมายของตัวเอง"""
    if 'user_id' not in session:
        return jsonify({'success': False, 'message': 'Unauthorized'}), 401
    try:
        data = request.json
        db = get_collection('users').database
        assignment_id = ObjectId(data.get('assignment_id'))

        assignment = db['advisor_assignments'].find_one({'_id': assignment_id})
        if not assignment:
            return jsonify({'success': False, 'message': 'ไม่พบรายการนัดหมายนี้'})
        if str(assignment.get('advisor_id', '')) != session.get('user_id'):
            return jsonify({'success': False, 'message': 'คุณสามารถแก้ไขได้เฉพาะการนัดหมายของตัวเองเท่านั้น'}), 403

        db['advisor_assignments'].update_one({'_id': assignment_id}, {'$set': {
            'student': {'name': data.get('s_name', ''), 'email': data.get('s_email', ''), 'phone': data.get('s_phone', '')},
            'mentor': {'name': data.get('m_name', ''), 'email': data.get('m_email', ''), 'phone': data.get('m_phone', '')},
            'appointment': {'date': data.get('date', ''), 'time': data.get('time', ''), 'eval_link': data.get('eval_link', ''), 'lecturer_name': data.get('l_name', ''), 'lecturer_phone': data.get('l_phone', '')},
            'updated_at': datetime.utcnow(),
        }})

        # ส่งอีเมลแจ้ง
        recipients = [r for r in [data.get('s_email'), data.get('m_email'), session.get('email')] if r and r.strip()]
        if recipients:
            try:
                msg = Message(
                    subject=f"[แก้ไข] แจ้งการเปลี่ยนแปลงนัดหมายนิเทศ: {data.get('s_name', '')}",
                    recipients=recipients,
                    body=f"""เรียนทุกท่าน,

แจ้งการเปลี่ยนแปลงรายละเอียดการนัดหมายนิเทศ:

- นักศึกษา: {data.get('s_name', '-')}
- อาจารย์ผู้นิเทศ: {data.get('l_name', '-')} (โทร: {data.get('l_phone', '-')})
- พี่เลี้ยง: {data.get('m_name', '-')}
- วันเวลา: {data.get('date', '-')} เวลา {data.get('time', '-')} น.
- ลิงก์ประเมิน: {data.get('eval_link', '-')}

แก้ไขโดย: {session.get('user_name', '')}
ระบบจัดการการฝึกงาน (Internship System)
""")
                mail.send(msg)
            except:
                return jsonify({'success': True, 'message': 'บันทึกเรียบร้อย แต่ส่งอีเมลไม่สำเร็จ'})

        return jsonify({'success': True, 'message': 'บันทึกและส่งอีเมลแจ้งเรียบร้อยแล้ว'})
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)}), 500


@lecturer_bp.route('/api/complete-appointment', methods=['POST'])
def complete_appointment():
    """เปลี่ยนสถานะเป็นเสร็จสิ้น — เฉพาะของตัวเอง"""
    if 'user_id' not in session:
        return jsonify({'success': False, 'message': 'Unauthorized'}), 401
    try:
        data = request.json
        db = get_collection('users').database
        assignment_id = ObjectId(data.get('assignment_id'))

        assignment = db['advisor_assignments'].find_one({'_id': assignment_id})
        if not assignment:
            return jsonify({'success': False, 'message': 'ไม่พบรายการนี้'})
        if str(assignment.get('advisor_id', '')) != session.get('user_id'):
            return jsonify({'success': False, 'message': 'ทำได้เฉพาะการนัดของตัวเองเท่านั้น'}), 403

        db['advisor_assignments'].update_one({'_id': assignment_id}, {'$set': {
            'status': 'completed', 'completed_by': session.get('user_id'),
            'completed_at': datetime.utcnow(), 'updated_at': datetime.utcnow(),
        }})

        student = assignment.get('student', {})
        mentor = assignment.get('mentor', {})
        appointment = assignment.get('appointment', {})
        recipients = [r for r in [student.get('email'), mentor.get('email'), session.get('email')] if r and r.strip()]
        if recipients:
            try:
                msg = Message(
                    subject=f"[สำเร็จ] การนิเทศเสร็จสิ้น: {student.get('name', '')}",
                    recipients=recipients,
                    body=f"""เรียนทุกท่าน,

การนิเทศงานนักศึกษาฝึกงานเสร็จสิ้นแล้ว:
- นักศึกษา: {student.get('name', '-')}
- อาจารย์: {appointment.get('lecturer_name', '-')}
- วันที่นัด: {appointment.get('date', '-')} เวลา {appointment.get('time', '-')} น.

บันทึกโดย: {session.get('user_name', '')}
ระบบจัดการการฝึกงาน (Internship System)
""")
                mail.send(msg)
            except:
                return jsonify({'success': True, 'message': 'บันทึกสำเร็จ แต่ส่งอีเมลไม่สำเร็จ'})

        return jsonify({'success': True, 'message': 'บันทึกสถานะเสร็จสิ้นเรียบร้อยแล้ว'})
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)}), 500


@lecturer_bp.route('/api/cancel-appointment', methods=['POST'])
def cancel_appointment():
    """ยกเลิกนัดหมาย — เฉพาะของตัวเอง"""
    if 'user_id' not in session:
        return jsonify({'success': False, 'message': 'Unauthorized'}), 401
    try:
        data = request.json
        db = get_collection('users').database
        assignment_id = ObjectId(data.get('assignment_id'))

        assignment = db['advisor_assignments'].find_one({'_id': assignment_id})
        if not assignment:
            return jsonify({'success': False, 'message': 'ไม่พบรายการนี้'})
        if str(assignment.get('advisor_id', '')) != session.get('user_id'):
            return jsonify({'success': False, 'message': 'ทำได้เฉพาะการนัดของตัวเองเท่านั้น'}), 403

        db['advisor_assignments'].update_one({'_id': assignment_id}, {'$set': {
            'status': 'cancelled', 'cancelled_by': session.get('user_id'),
            'cancelled_at': datetime.utcnow(), 'updated_at': datetime.utcnow(),
        }})

        student = assignment.get('student', {})
        mentor = assignment.get('mentor', {})
        appointment = assignment.get('appointment', {})
        recipients = [r for r in [student.get('email'), mentor.get('email'), session.get('email')] if r and r.strip()]
        if recipients:
            try:
                msg = Message(
                    subject=f"[ยกเลิก] แจ้งยกเลิกนัดหมายนิเทศ: {student.get('name', '')}",
                    recipients=recipients,
                    body=f"""เรียนทุกท่าน,

ขอแจ้งยกเลิกการนัดหมายนิเทศ:
- นักศึกษา: {student.get('name', '-')}
- อาจารย์: {appointment.get('lecturer_name', '-')}
- วันที่นัดเดิม: {appointment.get('date', '-')} เวลา {appointment.get('time', '-')} น.

ยกเลิกโดย: {session.get('user_name', '')}
ระบบจัดการการฝึกงาน (Internship System)
""")
                mail.send(msg)
            except:
                return jsonify({'success': True, 'message': 'ยกเลิกเรียบร้อย แต่ส่งอีเมลไม่สำเร็จ'})

        return jsonify({'success': True, 'message': 'ยกเลิกนัดหมายและส่งอีเมลแจ้งเรียบร้อยแล้ว'})
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)}), 500