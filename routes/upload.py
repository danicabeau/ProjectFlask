from flask import Blueprint, render_template, request, jsonify, session, redirect, url_for, send_file, abort
from werkzeug.utils import secure_filename
import os, img2pdf, tempfile, gridfs
from datetime import datetime
from bson import ObjectId
from io import BytesIO

from utils.db import get_collection
from utils.ocr_typhoon import process_document_ocr
from config import Config

upload_bp = Blueprint('upload', __name__)

# --- [1. Helper Functions] ---
def get_gridfs():
    db = get_collection('users').database
    return gridfs.GridFS(db)

def allowed_file(filename):
    return '.' in filename and \
           filename.rsplit('.', 1)[1].lower() in Config.ALLOWED_EXTENSIONS

def get_student_name(student_id):
    user = get_collection('users').find_one({'_id': ObjectId(student_id)})
    return user.get('name', 'ไม่ระบุชื่อ') if user else 'ไม่พบข้อมูล'

# --- [2. API & Page Routes] ---

@upload_bp.route('/api/check-status', methods=['GET'])
def check_status():
    if 'user_id' not in session:
        return jsonify({'success': False, 'message': 'Unauthorized'}), 401
    user_id = session.get('user_id')
    application = get_collection('application_forms').find_one({'student_id': ObjectId(user_id)})
    if not application:
        return jsonify({'has_application': False, 'application_approved': False})
    status = application.get('status', 'pending')
    return jsonify({'has_application': True, 'application_approved': (status == 'approved'), 'status': status})

@upload_bp.route('/upload', methods=['GET'])
def upload_page():
    if 'user_id' not in session:
        return redirect(url_for('auth.login_page'))
    
    user_id = ObjectId(session.get('user_id'))
    documents = []
    
    app = get_collection('application_forms').find_one({'student_id': user_id})
    if app:
        documents.append({
            '_id': str(app['_id']),
            'file_name': app.get('file_info', {}).get('file_name', 'N/A'),
            'doc_type': 'ใบสมัคร',
            'status': app.get('status', 'pending'),
            'created_at': app.get('created_at')
        })
    
    incs = list(get_collection('internship_documents').find({'student_id': user_id}))
    for i in incs:
        documents.append({
            '_id': str(i['_id']),
            'file_name': i.get('document_info', {}).get('file_name', 'N/A'),
            'doc_type': 'ใบตอบรับ',
            'status': i.get('status', 'N/A'),
            'created_at': i.get('created_at')
        })
    
    documents.sort(key=lambda x: x['created_at'] if x['created_at'] else datetime.min, reverse=True)
    return render_template('upload.html', documents=documents)

@upload_bp.route('/upload', methods=['POST'])
def upload_file():
    temp_files = []
    try:
        doc_type = request.form.get('document_type')
        user_id = session.get('user_id')

        if doc_type == 'acceptance_letter':
            app = get_collection('application_forms').find_one({'student_id': ObjectId(user_id)})
            if not app or app.get('status') != 'approved':
                return jsonify({'success': False, 'message': 'ใบสมัครยังไม่ได้รับการอนุมัติ'}), 403

        files = request.files.getlist('files[]')
        if not files or files[0].filename == '':
            single = request.files.get('file')
            if not single or single.filename == '':
                return jsonify({'success': False, 'message': 'ไม่พบไฟล์'}), 400
            files = [single]

        for f in files:
            if not allowed_file(f.filename):
                return jsonify({'success': False, 'message': f'ไฟล์ {f.filename} ไม่ถูกต้อง'}), 400

        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        first_ext = os.path.splitext(files[0].filename)[1].lower()

        if first_ext == '.pdf':
            fd, temp_pdf = tempfile.mkstemp(suffix='.pdf')
            os.close(fd)
            temp_files.append(temp_pdf)
            files[0].save(temp_pdf)
            saved_filename = secure_filename(files[0].filename)
            pdf_bytes = open(temp_pdf, 'rb').read()
        else:
            image_temps = []
            for f in files:
                fd, tmp = tempfile.mkstemp(suffix=os.path.splitext(f.filename)[1])
                os.close(fd); f.save(tmp)
                image_temps.append(tmp); temp_files.append(tmp)
            fd, temp_pdf = tempfile.mkstemp(suffix='.pdf')
            os.close(fd); temp_files.append(temp_pdf)
            with open(temp_pdf, 'wb') as f_pdf:
                f_pdf.write(img2pdf.convert(image_temps))
            saved_filename = f"scan_{timestamp}.pdf"
            pdf_bytes = open(temp_pdf, 'rb').read()

        ocr_result = process_document_ocr(temp_pdf, 'pdf', doc_type)
        fs = get_gridfs()
        gridfs_id = fs.put(pdf_bytes, filename=saved_filename, content_type='application/pdf',
                           uploaded_by=user_id, doc_type=doc_type, upload_date=datetime.now())

        return jsonify({'success': True, 'data': {
            'filename': saved_filename,
            'gridfs_id': str(gridfs_id),
            'ocr_data': ocr_result.get('data', {}),
            'ocr_confidence': ocr_result.get('confidence', 0.0)
        }}), 200
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)}), 500
    finally:
        for f in temp_files:
            try: os.remove(f)
            except: pass

@upload_bp.route('/save-application', methods=['POST'])
def save_application():
    try:
        data = request.get_json()
        doc_data = {
            'student_id': ObjectId(session.get('user_id')),
            'document_type': 'application_form',
            'status': 'pending',
            'file_info': {
                'file_name': data.get('filename'),
                'gridfs_id': ObjectId(data['gridfs_id']),
                'upload_date': datetime.now()
            },
            'ocr_data': data.get('ocr_data', {}),
            'created_at': datetime.now(),
            'updated_at': datetime.now()
        }
        get_collection('application_forms').update_one(
            {'student_id': ObjectId(session.get('user_id'))},
            {'$set': doc_data},
            upsert=True
        )
        return jsonify({'success': True, 'message': 'บันทึกสำเร็จ'}), 200
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)}), 500

@upload_bp.route('/save-document', methods=['POST'])
def save_document():
    try:
        data = request.get_json()
        ocr_data = data.get('ocr_data', {})

        # แปลงวันที่
        internship_period = ocr_data.get('internship_period', {})
        for date_key in ['start_date', 'end_date']:
            if internship_period.get(date_key):
                try:
                    internship_period[date_key] = datetime.strptime(internship_period[date_key], '%Y-%m-%d')
                except: pass

        document_data = {
            'student_id': ObjectId(session.get('user_id')),
            'document_info': {
                'file_name':   data.get('filename'),
                'gridfs_id':   ObjectId(data['gridfs_id']),
                'file_type':   'pdf',
                'upload_date': datetime.now()
            },
            'ocr_extracted_data': ocr_data,
            'is_verified': True,
            'created_at': datetime.now(),
            'updated_at': datetime.now()
        }
        get_collection('internship_documents').insert_one(document_data)

        # อัปเดต companies
        company_name = ocr_data.get('internship_place', {}).get('company_name')
        if company_name:
            get_collection('companies').update_one(
                {'company_name': company_name},
                {
                    '$inc': {'internship_count': 1},
                    '$set': {'updated_at': datetime.now()},
                    '$setOnInsert': {'created_at': datetime.now()}
                },
                upsert=True
            )

        return jsonify({'success': True, 'message': 'บันทึกสำเร็จ'}), 200
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)}), 500

# --- [3. View & Management Routes] ---
# Companies routes ย้ายไปอยู่ใน company.py แล้ว

@upload_bp.route('/documents', methods=['GET'])
def list_documents():
    if 'user_id' not in session: return redirect(url_for('auth.login_page'))
    role = session.get('role')
    user_id = ObjectId(session.get('user_id'))
    query = {} if role in ['hod', 'lecturer'] else {'student_id': user_id}
    
    all_docs = []
    apps = list(get_collection('application_forms').find(query))
    incs = list(get_collection('internship_documents').find(query))
    
    for a in apps:
        all_docs.append({'_id': str(a['_id']), 'student_name': get_student_name(a['student_id']),
                         'file_name': a['file_info']['file_name'], 'doc_type': 'ใบสมัคร',
                         'status': a.get('status', 'pending'), 'created_at': a['created_at']})
    for i in incs:
        all_docs.append({'_id': str(i['_id']), 'student_name': get_student_name(i['student_id']),
                         'file_name': i['document_info']['file_name'], 'doc_type': 'ใบตอบรับ',
                         'status': i.get('status', 'N/A'), 'created_at': i['created_at']})
    
    all_docs.sort(key=lambda x: x['created_at'] if x['created_at'] else datetime.min, reverse=True)
    return render_template('documents.html', documents=all_docs, role=role)

@upload_bp.route('/document/<id>', methods=['GET'])
def view_document(id):
    if 'user_id' not in session: return redirect(url_for('auth.login_page'))
    try:
        doc = get_collection('application_forms').find_one({'_id': ObjectId(id)})
        is_app_form = True
        if not doc:
            doc = get_collection('internship_documents').find_one({'_id': ObjectId(id)})
            is_app_form = False
        if not doc: return "<h1>404 - ไม่พบเอกสาร</h1>", 404

        ocr = doc.get('ocr_data') or doc.get('ocr_extracted_data') or {}
        info, fam = ocr.get('student_info', {}), ocr.get('family_info', {})
        emg = ocr.get('emergency') or ocr.get('internship_place') or {}

        if not is_app_form:
            std_app = get_collection('application_forms').find_one({'student_id': doc['student_id']})
            if std_app:
                student_ocr = std_app.get('ocr_data', {})
                info, fam = student_ocr.get('student_info', {}), student_ocr.get('family_info', {})

        doc['_id'] = str(doc['_id'])
        return render_template('document_view.html', doc=doc, info=info, fam=fam, emg=emg, is_app_form=is_app_form)
    except Exception as e: return f"Error: {str(e)}", 500

@upload_bp.route('/update-document-status', methods=['POST'])
def update_document_status():
    if session.get('role') not in ['hod', 'lecturer']: return jsonify({'success': False, 'message': 'Denied'}), 403
    try:
        data = request.get_json()
        doc_id, new_status = data.get('id'), data.get('status')
        res = get_collection('application_forms').update_one(
            {'_id': ObjectId(doc_id)},
            {'$set': {'status': new_status, 'updated_at': datetime.now()}}
        )
        if res.matched_count == 0:
            get_collection('internship_documents').update_one(
                {'_id': ObjectId(doc_id)},
                {'$set': {'status': new_status, 'updated_at': datetime.now()}}
            )
        return jsonify({'success': True})
    except Exception as e: return jsonify({'success': False, 'message': str(e)}), 500

# --- [4. File Handling Routes] ---

@upload_bp.route('/document/file/<id>')
def serve_document_file(id):
    return _serve_file_logic(id, as_attachment=False)

@upload_bp.route('/document/download/<id>')
def download_document_file(id):
    return _serve_file_logic(id, as_attachment=True)

def _serve_file_logic(id, as_attachment=False):
    if 'user_id' not in session: abort(401)
    try:
        doc = get_collection('application_forms').find_one({'_id': ObjectId(id)}) or \
              get_collection('internship_documents').find_one({'_id': ObjectId(id)})
        if not doc: abort(404)

        if session.get('role') not in ['hod', 'lecturer'] and str(doc['student_id']) != str(session.get('user_id')):
            abort(403)

        file_meta = doc.get('file_info') or doc.get('document_info') or {}
        gridfs_id = file_meta.get('gridfs_id')
        if not gridfs_id: abort(404)

        fs = get_gridfs()
        grid_file = fs.get(gridfs_id)
        return send_file(BytesIO(grid_file.read()), mimetype='application/pdf',
                         download_name=file_meta.get('file_name', 'document.pdf'), as_attachment=as_attachment)
    except Exception as e: return str(e), 500

# ✅ นศ. ลบเอกสารของตัวเอง (เฉพาะที่ยังไม่ได้รับการอนุมัติ)
@upload_bp.route('/document/delete/<id>', methods=['POST'])
def delete_document(id):
    if 'user_id' not in session:
        return jsonify({'success': False, 'message': 'Unauthorized'}), 401
    try:
        user_id = ObjectId(session.get('user_id'))
        fs = get_gridfs()

        # ลองหาจาก application_forms ก่อน
        doc = get_collection('application_forms').find_one({'_id': ObjectId(id), 'student_id': user_id})
        if doc:
            if doc.get('status') == 'approved':
                return jsonify({'success': False, 'message': 'ไม่สามารถลบใบสมัครที่อนุมัติแล้ว'}), 403
            gridfs_id = doc.get('file_info', {}).get('gridfs_id')
            if gridfs_id: fs.delete(gridfs_id)
            get_collection('application_forms').delete_one({'_id': ObjectId(id)})
            return jsonify({'success': True})

        # ลองหาจาก internship_documents
        doc = get_collection('internship_documents').find_one({'_id': ObjectId(id), 'student_id': user_id})
        if doc:
            gridfs_id = doc.get('document_info', {}).get('gridfs_id')
            if gridfs_id: fs.delete(gridfs_id)
            get_collection('internship_documents').delete_one({'_id': ObjectId(id)})
            return jsonify({'success': True})

        return jsonify({'success': False, 'message': 'ไม่พบเอกสาร'}), 404
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)}), 500