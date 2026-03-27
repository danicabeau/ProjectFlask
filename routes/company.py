from flask import Blueprint, render_template, session, redirect, url_for
from bson import ObjectId
from utils.db import get_collection

company_bp = Blueprint('company', __name__)


@company_bp.route('/companies', methods=['GET'])
def list_companies():
    """แสดงเฉพาะสถานประกอบการที่มีการนิเทศสำเร็จแล้ว (status: completed)"""
    if 'user_id' not in session:
        return redirect(url_for('auth.login_page'))
    try:
        db = get_collection('users').database

        # ดึงเฉพาะ advisor_assignments ที่สถานะ completed
        completed = list(db['advisor_assignments'].find({'status': 'completed'}))

        # รวบรวม internship_document_id ที่สำเร็จ
        completed_doc_ids = [a.get('internship_document_id') for a in completed if a.get('internship_document_id')]

        if not completed_doc_ids:
            return render_template('companies.html', companies=[])

        # ดึง internship_documents เฉพาะที่สำเร็จ
        docs = list(db['internship_documents'].find({'_id': {'$in': completed_doc_ids}}))

        # รวมข้อมูลจาก companies collection
        companies_col = {c['company_name']: c for c in db['companies'].find({})}

        # จัดกลุ่มตามบริษัท
        company_map = {}
        for doc in docs:
            place  = doc.get('ocr_extracted_data', {}).get('internship_place', {})
            period = doc.get('ocr_extracted_data', {}).get('internship_period', {})
            name   = place.get('company_name', '').strip()
            if not name:
                continue

            # ดึงชื่อ นศ.
            student = db['users'].find_one({'_id': doc.get('student_id')})
            student_name = student.get('name', 'ไม่ระบุ') if student else 'ไม่ระบุ'

            # format ช่วงเวลา
            start = period.get('start_date')
            end   = period.get('end_date')
            if hasattr(start, 'strftime'):
                period_str = f"{start.strftime('%d/%m/%Y')} – {end.strftime('%d/%m/%Y')}" if hasattr(end, 'strftime') else start.strftime('%d/%m/%Y')
            else:
                period_str = f"{start or ''} – {end or ''}".strip(' –') or ''

            if name not in company_map:
                col_data = companies_col.get(name, {})
                company_map[name] = {
                    'company_name':  name,
                    'address':       place.get('address', '') or col_data.get('address', ''),
                    'phone':         place.get('phone', '')   or col_data.get('phone', ''),
                    'email':         place.get('email', '')   or col_data.get('email', ''),
                    'business_type': col_data.get('business_type', ''),
                    'student_count': 0,
                    'students':      []
                }

            company_map[name]['student_count'] += 1
            company_map[name]['students'].append({
                'name':   student_name,
                'period': period_str
            })

        companies = sorted(company_map.values(), key=lambda x: x['student_count'], reverse=True)
        return render_template('companies.html', companies=companies)
    except Exception as e:
        return f"Error: {str(e)}", 500


@company_bp.route('/companies/<path:company_name>', methods=['GET'])
def company_detail(company_name):
    """รายละเอียดบริษัท — แสดงเฉพาะนศ.ที่นิเทศสำเร็จ"""
    if 'user_id' not in session:
        return redirect(url_for('auth.login_page'))
    try:
        db = get_collection('users').database

        # ข้อมูลบริษัทจาก companies collection
        company = db['companies'].find_one({'company_name': company_name}) or {}
        if not company:
            company = {'company_name': company_name}
        else:
            company['_id'] = str(company['_id'])

        # ดึง internship_document_id ที่สำเร็จ
        completed = list(db['advisor_assignments'].find({'status': 'completed'}))
        completed_doc_ids = [a.get('internship_document_id') for a in completed if a.get('internship_document_id')]

        # ดึง internship_documents ของบริษัทนี้ เฉพาะที่สำเร็จ
        query = {
            'ocr_extracted_data.internship_place.company_name': company_name
        }
        if completed_doc_ids:
            query['_id'] = {'$in': completed_doc_ids}
        else:
            # ไม่มีรายการสำเร็จเลย
            return render_template('company_detail.html', company=company, records=[])

        docs = list(db['internship_documents'].find(query))

        records = []
        for doc in docs:
            ocr    = doc.get('ocr_extracted_data', {})
            place  = ocr.get('internship_place', {})
            mentor = ocr.get('mentor', {})
            period = ocr.get('internship_period', {})

            # เติมข้อมูลบริษัทถ้ายังไม่มี
            if not company.get('address'):
                company['address'] = place.get('address', '')
            if not company.get('phone'):
                company['phone'] = place.get('phone', '')
            if not company.get('email'):
                company['email'] = place.get('email', '')

            student = db['users'].find_one({'_id': doc.get('student_id')})
            student_name = student.get('name', 'ไม่ระบุ') if student else 'ไม่ระบุ'

            start = period.get('start_date')
            end   = period.get('end_date')
            if hasattr(start, 'strftime'):
                period_str = f"{start.strftime('%d/%m/%Y')} – {end.strftime('%d/%m/%Y')}" if hasattr(end, 'strftime') else start.strftime('%d/%m/%Y')
            else:
                period_str = f"{start or ''} – {end or ''}".strip(' –') or ''

            mentor_name = f"{mentor.get('first_name', '')} {mentor.get('last_name', '')}".strip()

            records.append({
                'student_name': student_name,
                'period':       period_str,
                'mentor_name':  mentor_name or '-',
                'mentor_phone': mentor.get('phone', '-'),
            })

        return render_template('company_detail.html', company=company, records=records)
    except Exception as e:
        return f"Error: {str(e)}", 500
