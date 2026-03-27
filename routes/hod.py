from flask import Blueprint, render_template, session, redirect, url_for, jsonify, request
from bson import ObjectId
from datetime import datetime
from utils.db import get_collection
from flask_mail import Message
from extenstions import mail

hod_bp = Blueprint('hod', __name__, url_prefix='/hod')

@hod_bp.route('/dashboard')
def dashboard():
    if 'user_id' not in session or session.get('role') != 'hod':
        return redirect(url_for('auth.login'))

    app_col = get_collection('application_forms')
    applications = list(app_col.find({'status': 'pending'}))

    users_col = get_collection('users')
    docs = []
    for app in applications:
        app['_id'] = str(app['_id'])
        ocr = app.get('ocr_data', {})
        docs.append({
            '_id':      app['_id'],
            'status':   app.get('status'),
            'file_info': app.get('file_info', {}),
            'created_at': app.get('created_at'),
            'student_info': ocr.get('student_info', {}),
        })

    return render_template('hod_dashboard.html', documents=docs)


@hod_bp.route('/approve/<id>', methods=['POST'])
def approve(id):
    if 'user_id' not in session or session.get('role') != 'hod':
        return jsonify({'success': False, 'message': 'Unauthorized'}), 401
    try:
        get_collection('application_forms').update_one(
            {'_id': ObjectId(id)},
            {'$set': {
                'status': 'approved',
                'approved_by': session.get('user_id'),
                'approved_at': datetime.now(),
                'updated_at': datetime.now()
            }}
        )
        return jsonify({'success': True, 'message': 'อนุมัติใบสมัครเรียบร้อยแล้ว'})
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)}), 500


@hod_bp.route('/reject/<id>', methods=['POST'])
def reject(id):
    if 'user_id' not in session or session.get('role') != 'hod':
        return jsonify({'success': False, 'message': 'Unauthorized'}), 401
    try:
        get_collection('application_forms').delete_one({'_id': ObjectId(id)})
        return jsonify({'success': True, 'message': 'ลบใบสมัครเรียบร้อยแล้ว'})
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)}), 500


@hod_bp.route('/appointments')
def appointments():
    if 'user_id' not in session or session.get('role') != 'hod':
        return redirect(url_for('auth.login'))

    db = get_collection('users').database
    assignments = list(db['advisor_assignments'].find({}, sort=[('assigned_date', -1)]))

    history = []
    for a in assignments:
        doc_id = a.get('internship_document_id')
        doc = db['internship_documents'].find_one({'_id': doc_id}) if doc_id else {}
        ocr = doc.get('ocr_extracted_data', {}) if doc else {}
        history.append({
            '_id':           str(a['_id']),
            'advisor_id':    str(a.get('advisor_id', '')),
            'status':        a.get('status', 'assigned'),
            'assigned_date': a.get('assigned_date'),
            'student':       a.get('student', {}),
            'mentor':        a.get('mentor', {}),
            'appointment':   a.get('appointment', {}),
            'company':       ocr.get('internship_place', {}),
        })

    return render_template('hod_appointments.html', history=history)


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
        return str(e), 500


@hod_bp.route('/api/toggle-supervisor', methods=['POST'])
def toggle_supervisor():
    if 'user_id' not in session or session.get('role') != 'hod':
        return jsonify({'success': False, 'message': 'Unauthorized'}), 401
    try:
        data = request.get_json()
        get_collection('users').update_one(
            {'_id': ObjectId(data['user_id'])},
            {'$set': {'can_supervise': data['can_supervise'], 'updated_at': datetime.now()}}
        )
        status = 'เปิดสิทธิ์' if data['can_supervise'] else 'ปิดสิทธิ์'
        return jsonify({'success': True, 'message': f'{status}การนิเทศเรียบร้อยแล้ว'})
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)}), 500


@hod_bp.route('/api/update-appointment', methods=['POST'])
def update_appointment():
    """แก้ไขการนัดหมายและส่งอีเมลแจ้งเตือน"""
    if 'user_id' not in session or session.get('role') != 'hod':
        return jsonify({'success': False, 'message': 'Unauthorized'}), 401

    try:
        data = request.get_json()
        db = get_collection('users').database
        assignment_id = ObjectId(data.get('assignment_id'))

        # ตรวจสอบว่าเป็นเจ้าของการนัดหมายนี้หรือไม่
        assignment = db['advisor_assignments'].find_one({'_id': assignment_id})
        if not assignment:
            return jsonify({'success': False, 'message': 'ไม่พบรายการนัดหมายนี้'})

        if str(assignment.get('advisor_id', '')) != session.get('user_id'):
            return jsonify({'success': False, 'message': 'คุณสามารถแก้ไขได้เฉพาะการนัดหมายของตัวเองเท่านั้น'}), 403

        # อัปเดตข้อมูลใน advisor_assignments
        update_data = {
            'student': {
                'name':  data.get('s_name', ''),
                'email': data.get('s_email', ''),
                'phone': data.get('s_phone', ''),
            },
            'mentor': {
                'name':  data.get('m_name', ''),
                'email': data.get('m_email', ''),
                'phone': data.get('m_phone', ''),
            },
            'appointment': {
                'date':           data.get('date', ''),
                'time':           data.get('time', ''),
                'eval_link':      data.get('eval_link', ''),
                'lecturer_name':  data.get('l_name', ''),
                'lecturer_phone': data.get('l_phone', ''),
            },
            'updated_at': datetime.utcnow(),
        }

        result = db['advisor_assignments'].update_one(
            {'_id': assignment_id},
            {'$set': update_data}
        )

        if result.modified_count == 0 and result.matched_count == 0:
            return jsonify({'success': False, 'message': 'ไม่พบรายการนัดหมายนี้'})

        # ส่งอีเมลแจ้งเตือนการแก้ไข
        recipients = [data.get('s_email'), data.get('m_email')]
        # เพิ่มอีเมลของผู้แก้ไข (HOD)
        hod_email = session.get('email', '')
        if hod_email:
            recipients.append(hod_email)
        recipients = [r for r in recipients if r and r.strip()]

        if recipients:
            try:
                msg = Message(
                    subject=f"[แก้ไข] แจ้งการเปลี่ยนแปลงนัดหมายนิเทศ: {data.get('s_name', '')}",
                    recipients=recipients,
                    body=f"""เรียนทุกท่าน,

แจ้งการเปลี่ยนแปลงรายละเอียดการนัดหมายนิเทศงานนักศึกษาฝึกงานดังนี้:

───────────────────────────────
ข้อมูลที่อัปเดต:
───────────────────────────────
- นักศึกษา:           {data.get('s_name', '-')}
- อาจารย์ผู้นิเทศ:    {data.get('l_name', '-')} (โทร: {data.get('l_phone', '-')})
- พี่เลี้ยง (Mentor): {data.get('m_name', '-')} (โทร: {data.get('m_phone', '-')})

วันเวลาที่นัดหมาย:   {data.get('date', '-')} เวลา {data.get('time', '-')} น.
ลิงก์ประเมิน:         {data.get('eval_link', '-')}

───────────────────────────────
แก้ไขโดย: {session.get('user_name', 'HOD')}
วันที่แก้ไข: {datetime.now().strftime('%d/%m/%Y %H:%M')}
───────────────────────────────

จึงเรียนมาเพื่อโปรดทราบและเตรียมความพร้อม
ระบบจัดการการฝึกงาน (Internship System)
"""
                )
                mail.send(msg)
            except Exception as mail_err:
                # ถ้าส่งเมลไม่ได้ ก็ยังบันทึกข้อมูลสำเร็จ
                return jsonify({
                    'success': True,
                    'message': f'บันทึกเรียบร้อย แต่ส่งอีเมลไม่สำเร็จ: {str(mail_err)}'
                })

        return jsonify({'success': True, 'message': 'บันทึกและส่งอีเมลแจ้งเรียบร้อยแล้ว'})

    except Exception as e:
        return jsonify({'success': False, 'message': str(e)}), 500


@hod_bp.route('/api/cancel-appointment', methods=['POST'])
def cancel_appointment():
    """ยกเลิกการนัดหมาย — เฉพาะเจ้าของเท่านั้น พร้อมส่งอีเมลแจ้ง"""
    if 'user_id' not in session or session.get('role') != 'hod':
        return jsonify({'success': False, 'message': 'Unauthorized'}), 401

    try:
        data = request.get_json()
        db = get_collection('users').database
        assignment_id = ObjectId(data.get('assignment_id'))

        # ดึงข้อมูลเดิม
        assignment = db['advisor_assignments'].find_one({'_id': assignment_id})
        if not assignment:
            return jsonify({'success': False, 'message': 'ไม่พบรายการนัดหมายนี้'})

        # ตรวจสอบเจ้าของ
        if str(assignment.get('advisor_id', '')) != session.get('user_id'):
            return jsonify({'success': False, 'message': 'คุณสามารถยกเลิกได้เฉพาะการนัดหมายของตัวเองเท่านั้น'}), 403

        # อัปเดต status เป็น cancelled
        db['advisor_assignments'].update_one(
            {'_id': assignment_id},
            {'$set': {
                'status': 'cancelled',
                'cancelled_by': session.get('user_id'),
                'cancelled_at': datetime.utcnow(),
                'updated_at': datetime.utcnow(),
            }}
        )

        # ส่งอีเมลแจ้งยกเลิก
        student = assignment.get('student', {})
        mentor = assignment.get('mentor', {})
        appointment = assignment.get('appointment', {})

        recipients = [student.get('email'), mentor.get('email')]
        hod_email = session.get('email', '')
        if hod_email:
            recipients.append(hod_email)
        recipients = [r for r in recipients if r and r.strip()]

        if recipients:
            try:
                msg = Message(
                    subject=f"[ยกเลิก] แจ้งยกเลิกนัดหมายนิเทศ: {student.get('name', '')}",
                    recipients=recipients,
                    body=f"""เรียนทุกท่าน,

ขอแจ้งยกเลิกการนัดหมายนิเทศงานนักศึกษาฝึกงานดังนี้:

───────────────────────────────
รายละเอียดที่ถูกยกเลิก:
───────────────────────────────
- นักศึกษา:           {student.get('name', '-')}
- อาจารย์ผู้นิเทศ:    {appointment.get('lecturer_name', '-')} (โทร: {appointment.get('lecturer_phone', '-')})
- พี่เลี้ยง (Mentor): {mentor.get('name', '-')}

วันเวลาที่นัดเดิม:   {appointment.get('date', '-')} เวลา {appointment.get('time', '-')} น.

───────────────────────────────
ยกเลิกโดย: {session.get('user_name', 'HOD')}
วันที่ยกเลิก: {datetime.now().strftime('%d/%m/%Y %H:%M')}
───────────────────────────────

หากมีการนัดหมายใหม่ ระบบจะแจ้งให้ทราบอีกครั้ง
ระบบจัดการการฝึกงาน (Internship System)
"""
                )
                mail.send(msg)
            except Exception as mail_err:
                return jsonify({
                    'success': True,
                    'message': f'ยกเลิกเรียบร้อย แต่ส่งอีเมลไม่สำเร็จ: {str(mail_err)}'
                })

        return jsonify({'success': True, 'message': 'ยกเลิกนัดหมายและส่งอีเมลแจ้งเรียบร้อยแล้ว'})

    except Exception as e:
        return jsonify({'success': False, 'message': str(e)}), 500


@hod_bp.route('/api/complete-appointment', methods=['POST'])
def complete_appointment():
    """เปลี่ยนสถานะเป็นเสร็จสิ้น — เฉพาะเจ้าของเท่านั้น"""
    if 'user_id' not in session:
        return jsonify({'success': False, 'message': 'Unauthorized'}), 401

    try:
        data = request.get_json()
        db = get_collection('users').database
        assignment_id = ObjectId(data.get('assignment_id'))

        assignment = db['advisor_assignments'].find_one({'_id': assignment_id})
        if not assignment:
            return jsonify({'success': False, 'message': 'ไม่พบรายการนัดหมายนี้'})

        # ตรวจสอบเจ้าของ
        if str(assignment.get('advisor_id', '')) != session.get('user_id'):
            return jsonify({'success': False, 'message': 'คุณสามารถเปลี่ยนสถานะได้เฉพาะการนัดหมายของตัวเองเท่านั้น'}), 403

        # อัปเดต status เป็น completed
        db['advisor_assignments'].update_one(
            {'_id': assignment_id},
            {'$set': {
                'status': 'completed',
                'completed_by': session.get('user_id'),
                'completed_at': datetime.utcnow(),
                'updated_at': datetime.utcnow(),
            }}
        )

        # ส่งอีเมลแจ้งนิเทศสำเร็จ
        student = assignment.get('student', {})
        mentor = assignment.get('mentor', {})
        appointment = assignment.get('appointment', {})

        recipients = [student.get('email'), mentor.get('email')]
        user_email = session.get('email', '')
        if user_email:
            recipients.append(user_email)
        recipients = [r for r in recipients if r and r.strip()]

        if recipients:
            try:
                msg = Message(
                    subject=f"[สำเร็จ] การนิเทศนักศึกษาเสร็จสิ้น: {student.get('name', '')}",
                    recipients=recipients,
                    body=f"""เรียนทุกท่าน,

ขอแจ้งว่าการนิเทศงานนักศึกษาฝึกงานได้ดำเนินการเสร็จสิ้นแล้ว:

───────────────────────────────
รายละเอียด:
───────────────────────────────
- นักศึกษา:           {student.get('name', '-')}
- อาจารย์ผู้นิเทศ:    {appointment.get('lecturer_name', '-')} (โทร: {appointment.get('lecturer_phone', '-')})
- พี่เลี้ยง (Mentor): {mentor.get('name', '-')}

วันที่นัดหมาย:       {appointment.get('date', '-')} เวลา {appointment.get('time', '-')} น.
ลิงก์ประเมิน:         {appointment.get('eval_link', '-')}

───────────────────────────────
บันทึกโดย: {session.get('user_name', '')}
วันที่บันทึก: {datetime.now().strftime('%d/%m/%Y %H:%M')}
───────────────────────────────

ขอบคุณทุกท่านที่ให้ความร่วมมือ
ระบบจัดการการฝึกงาน (Internship System)
"""
                )
                mail.send(msg)
            except Exception as mail_err:
                return jsonify({
                    'success': True,
                    'message': f'บันทึกสำเร็จ แต่ส่งอีเมลไม่สำเร็จ: {str(mail_err)}'
                })

        return jsonify({'success': True, 'message': 'บันทึกสถานะเสร็จสิ้นและส่งอีเมลแจ้งเรียบร้อยแล้ว'})

    except Exception as e:
        return jsonify({'success': False, 'message': str(e)}), 500