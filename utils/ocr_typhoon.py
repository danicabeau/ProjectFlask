import os
import re
import json
import torch
import tempfile
import gc
from typing import Dict, Any, List, Union
from PIL import Image
from peft import PeftModel
from transformers import AutoModelForImageTextToText, AutoProcessor
from qwen_vl_utils import process_vision_info

def thai_date_to_iso(date_str):
    if not date_str: return ""
    months = {
        'มกราคม': '01', 'กุมภาพันธ์': '02', 'มีนาคม': '03', 'เมษายน': '04',
        'พฤษภาคม': '05', 'มิถุนายน': '06', 'กรกฎาคม': '07', 'สิงหาคม': '08',
        'กันยายน': '09', 'ตุลาคม': '10', 'พฤศจิกายน': '11', 'ธันวาคม': '12'
    }
    try:
        parts = date_str.strip().split()
        if len(parts) >= 3:
            day = parts[0].zfill(2)
            month = months.get(parts[1], '01')
            year = str(int(parts[2]) - 543)
            return f"{year}-{month}-{day}"
    except:
        pass
    return ""

class TyphoonOCR:
    def __init__(self, model_path: str = None):
        if model_path is None:
            model_path = os.path.abspath(r"D:\ProjectFlask\models\Typhoon-OCR-HighDetail-Model")
        
        base_model_id = "typhoon-ai/typhoon-ocr1.5-2b"
        print("🔄 Loading Typhoon OCR Model...")
        base_model = AutoModelForImageTextToText.from_pretrained(
            base_model_id,
            dtype=torch.float16 if torch.cuda.is_available() else torch.float32,
            device_map="auto",
            trust_remote_code=True
        )
        self.model = PeftModel.from_pretrained(base_model, model_path).merge_and_unload()
        self.processor = AutoProcessor.from_pretrained(base_model_id, trust_remote_code=True)
        print("✅ Typhoon OCR Online (Dual-Document Mode)")

    def _get_empty_structure(self):
        return {
            'internship_place': {'company_name': '', 'phone': '', 'address': '', 'email': ''},
            'mentor': {'first_name': '', 'last_name': '', 'phone': ''},
            'internship_period': {'start_date': '', 'end_date': ''}
        }

    def _get_application_structure(self):
        return {
            'student_info': {
                'name_th': '', 'name_en': '', 'student_id': '', 'year': '',
                'gpax': '', 'dob': '', 'address_reg': '', 'phone': '', 'military_status': ''
            },
            'family_info': {
                'father': {'name': '', 'age': '', 'job': '', 'phone': ''},
                'mother': {'name': '', 'age': '', 'job': '', 'phone': ''}
            },
            'emergency': {'name': '', 'phone': '', 'address': ''}
        }

    def _clear_memory(self):
        if torch.cuda.is_available(): torch.cuda.empty_cache()
        gc.collect()

    def _stitch_images(self, image_paths: List[str]) -> str:
        """✅ ต่อรูปหลายไฟล์เป็นภาพเดียวในแนวตั้ง แล้วคืน temp path"""
        images = [Image.open(p).convert("RGB") for p in image_paths]
        total_height = sum(img.height for img in images)
        max_width = max(img.width for img in images)
        stitched = Image.new('RGB', (max_width, total_height), (255, 255, 255))
        y = 0
        for img in images:
            stitched.paste(img, (0, y))
            y += img.height
        fd, temp_path = tempfile.mkstemp(suffix='.jpg')
        os.close(fd)
        stitched.save(temp_path, 'JPEG', quality=95)
        return temp_path

    def extract_structured_data(self, image_path: str, doc_type: str = 'application_form') -> Dict[str, Any]:
        try:
            if doc_type == 'application_form':
                prompt = "อ่านข้อมูลจากใบสมัครนี้ สกัดข้อมูลนักศึกษาไทยและอังกฤษ, ข้อมูลครอบครัว, และบุคคลติดต่อได้ในกรณีฉุกเฉินจนถึงข้อ 3.2"
            else:
                prompt = "อ่านข้อความทั้งหมดในเอกสารนี้ตามลำดับบรรทัด และรักษาโครงสร้างตารางไว้"

            with Image.open(image_path) as img:
                image = img.convert("RGB")
                messages = [{"role": "user", "content": [{"type": "image", "image": image}, {"type": "text", "text": prompt}]}]
                text = self.processor.apply_chat_template(messages, tokenize=False, add_generation_prompt=True)
                image_inputs, _ = process_vision_info(messages)
                inputs = self.processor(text=[text], images=image_inputs, padding=True, return_tensors="pt").to(self.model.device)
                
                with torch.inference_mode():
                    generated_ids = self.model.generate(**inputs, max_new_tokens=1500, do_sample=False)
                
                output_text = self.processor.batch_decode(generated_ids[:, inputs.input_ids.shape[1]:], skip_special_tokens=True)[0]
                del inputs, generated_ids, image_inputs

            print(f"DEBUG: AI Raw Response ({doc_type}) ->\n{output_text}")
            return self.parse_application_data(output_text) if doc_type == 'application_form' else self.parse_internship_data(output_text)

        except Exception as e:
            print(f"ERROR in extract_structured_data: {e}")
            return self._get_application_structure() if doc_type == 'application_form' else self._get_empty_structure()
        finally:
            self._clear_memory()

    def parse_application_data(self, text: str) -> Dict[str, Any]:
        data = self._get_application_structure()
        text = text.replace('&amp;', '&')
        
        # Zone Splitting
        fam_split = re.split(r'\*\*ข้อมูลครอบครัว.*?\*\*', text)
        stu_zone = fam_split[0]
        rest_1 = fam_split[1] if len(fam_split) > 1 else ""
        emg_split = re.split(r'\*\*บุคคล(?:ที่)?ติดต่อได้ในกรณีฉุกเฉิน.*?\*\*', rest_1)
        fam_zone = emg_split[0]
        emg_zone = emg_split[1] if len(emg_split) > 1 else ""

        # Fallback: plain text split
        if not fam_zone and not emg_zone:
            fam_split2 = re.split(r'ข้อมูลครอบครัว', text)
            stu_zone = fam_split2[0]
            rest_1 = fam_split2[1] if len(fam_split2) > 1 else ""
            emg_split2 = re.split(r'บุคคล(?:ที่)?ติดต่อได้ในกรณีฉุกเฉิน', rest_1)
            fam_zone = emg_split2[0]
            emg_zone = emg_split2[1] if len(emg_split2) > 1 else ""

        print(f"DEBUG stu_zone: {stu_zone[:200]}")
        print(f"DEBUG fam_zone: {fam_zone[:200]}")
        print(f"DEBUG emg_zone: {emg_zone[:200]}")

        # --- โซนนักศึกษา ---
        m_th = re.search(r'ชื่อ-สกุล\s*[:\s]*(?:นาย/นาง/นางสาว\s*)?(?:ไทย\s*)?([ก-๙][ก-๙\s]+)', stu_zone)
        if m_th: data['student_info']['name_th'] = m_th.group(1).strip()

        m_en = re.search(r'(?:Name.*?Surname|English)\s*[:\s]*(?:Mr\./Mrs\./Miss\.\s*)?([A-Za-z][A-Za-z\s]+)', stu_zone, re.IGNORECASE)
        if m_en: data['student_info']['name_en'] = m_en.group(1).strip()

        m_id = re.search(r'รหัสนักศึกษา[^0-9]*(\d{10,13})', stu_zone)
        if m_id: data['student_info']['student_id'] = m_id.group(1)

        m_year = re.search(r'ชั้นปี(?:ที่)?\s*[:\s]*(\d)', stu_zone)
        if m_year: data['student_info']['year'] = m_year.group(1)

        m_gpax = re.search(r'(?:GPAX|เกรดเฉลี่ยรวม)[^0-9]*(\d+\.\d+)', stu_zone)
        if m_gpax: data['student_info']['gpax'] = m_gpax.group(1)

        m_dob = re.search(r'(?:วันเดือนปีเกิด|Date of birth)[^0-9]*(\d{1,2}/\d{2}/\d{4})', stu_zone)
        if m_dob:
            parts = m_dob.group(1).split('/')
            if len(parts) == 3:
                be_year = int(parts[2])
                ce_year = be_year - 543 if be_year > 2400 else be_year
                data['student_info']['dob'] = f"{ce_year}-{parts[1]}-{parts[0].zfill(2)}"

        m_addr = re.search(r'ที่อยู่ตามทะเบียน[^ก-๙0-9]*([0-9ก-๙].*?)(?=โทรศัพท์|\n\n)', stu_zone, re.DOTALL)
        if m_addr: data['student_info']['address_reg'] = re.sub(r'\s+', ' ', m_addr.group(1)).strip()

        m_phone = re.search(r'โทรศัพท์[^0-9]*(0\d{2}[-\s]?\d{3,4}[-\s]?\d{3,4})', stu_zone)
        if m_phone: data['student_info']['phone'] = m_phone.group(1).strip()

        if 'ยังไม่ได้รับการเกณฑ์' in stu_zone:
            data['student_info']['military_status'] = 'ยังไม่ได้รับการเกณฑ์'
        elif 'ปลดเป็นทหารกองหนุน' in stu_zone:
            data['student_info']['military_status'] = 'ปลดเป็นทหารกองหนุน'
        elif 'ได้รับการยกเว้น' in stu_zone:
            data['student_info']['military_status'] = 'ได้รับการยกเว้น'

        # --- โซนครอบครัว ---
        f_match = re.search(r'บิดา\s*ชื่อ-สกุล\s*[:\s]*([ก-๙][ก-๙\s]+?)(?:\s*อายุ|\s*\n|$)', fam_zone)
        if f_match: data['family_info']['father']['name'] = f_match.group(1).strip()

        f_age = re.search(r'(?:บิดา.*?)อายุ\s*[:\s]*(\d+)\s*ปี', fam_zone, re.DOTALL)
        if f_age: data['family_info']['father']['age'] = f_age.group(1)

        f_job = re.search(r'(?:บิดา.*?)อาชีพ\s*[:\s]*([ก-๙][ก-๙\s]+?)(?:\n|โทรศัพท์)', fam_zone, re.DOTALL)
        if f_job: data['family_info']['father']['job'] = f_job.group(1).strip()

        f_phone = re.search(r'(?:บิดา.*?)(0\d{2}[-\s]?\d{3,4}[-\s]?\d{3,4})', fam_zone, re.DOTALL)
        if f_phone: data['family_info']['father']['phone'] = f_phone.group(1)

        m_match = re.search(r'มารดา\s*ชื่อ-สกุล\s*[:\s]*([ก-๙][ก-๙\s]+?)(?:\s*อายุ|\s*\n|$)', fam_zone)
        if m_match: data['family_info']['mother']['name'] = m_match.group(1).strip()

        m_age = re.search(r'(?:มารดา.*?)อายุ\s*[:\s]*(\d+)\s*ปี', fam_zone, re.DOTALL)
        if m_age: data['family_info']['mother']['age'] = m_age.group(1)

        m_job = re.search(r'(?:มารดา.*?)อาชีพ\s*[:\s]*([ก-๙][ก-๙\s]+?)(?:\n|โทรศัพท์|-)', fam_zone, re.DOTALL)
        if m_job: data['family_info']['mother']['job'] = m_job.group(1).strip()

        m_phone_match = re.search(r'(?:มารดา.*?โทรศัพท์[^0-9]*)(0\d{2}[-\s]?\d{3,4}[-\s]?\d{3,4})', fam_zone, re.DOTALL)
        if m_phone_match: data['family_info']['mother']['phone'] = m_phone_match.group(1)

        # --- โซนติดต่อฉุกเฉิน ---
        e_name = re.search(r'(?:3\.1\s+)?ชื่อ-สกุล\s*(?:\(นาย/นาง/นางสาว\))?\s*[:\s]*([ก-๙][ก-๙\s]+?)(?:\s*ความเกี่ยวข้อง|\n|$)', emg_zone)
        if e_name: data['emergency']['name'] = e_name.group(1).strip()

        e_phone = re.search(r'โทรศัพท์[^0-9]*(0\d{2}[-\s]?\d{3,4}[-\s]?\d{3,4})', emg_zone)
        if e_phone: data['emergency']['phone'] = e_phone.group(1)

        e_addr = re.search(r'ที่อยู่[^ก-๙0-9]*([0-9ก-๙].*?)(?=โทรศัพท์|\n\n|$)', emg_zone, re.DOTALL)
        if e_addr: data['emergency']['address'] = re.sub(r'\s+', ' ', e_addr.group(1)).strip()

        print(f"DEBUG Parsed: {json.dumps(data, ensure_ascii=False, indent=2)}")
        return data

    def parse_internship_data(self, text: str) -> Dict[str, Any]:
        data = self._get_empty_structure()
        def clean_value(val):
            if not val: return ""
            val = val.replace('*', '')
            labels = [r'ชื่อสถานประกอบการ', r'Employer\s*Name', r'ที่อยู่', r'Address', r'โทรศัพท์', r'Telephone', r'ชื่อ-นามสกุล', r'Name']
            for label in labels: val = re.sub(label, '', val, flags=re.IGNORECASE)
            return re.sub(r'^[\s,:/.-]+|[\s,:/.-]+$', '', val).strip()

        split_match = re.search(r'(ผู้ประสานงาน|Coordinator)', text)
        company_text = text[:split_match.start()] if split_match else text
        mentor_text = text[split_match.start():] if split_match else ""

        # ชื่อบริษัท — รองรับทั้ง "," และ space/newline
        m_comp = re.search(r'(?:ชื่อสถานประกอบการ|Employer Name)[,\s]+([^\n,]+)', company_text, re.IGNORECASE)
        if m_comp: data['internship_place']['company_name'] = clean_value(m_comp.group(1))

        # ที่อยู่บริษัท — รองรับทั้ง "," และ newline ก่อน "โทรสาร"
        m_addr = re.search(r'(?:ที่อยู่เลขที่|address)[,\s]+(.+?)(?=\n.*โทรสาร|\nโทรสาร|โทรสาร|,\s*โทรสาร)', company_text, re.IGNORECASE | re.DOTALL)
        if m_addr: data['internship_place']['address'] = clean_value(m_addr.group(1))

        # เบอร์โทรบริษัท — รองรับทั้ง space และ ","
        m_phone = re.search(r'โทรศัพท์/Telephone[,\s]+(0\d[\d\s\-]+?)(?:\s+E-mail|,\s*E-mail|\s*$)', company_text, re.IGNORECASE)
        if not m_phone:
            m_phone = re.search(r'(?:Telephone|โทรศัพท์)[^,\n]*[,\s]+([\d\s-]{9,})', company_text, re.IGNORECASE)
        if m_phone: data['internship_place']['phone'] = clean_value(m_phone.group(1))

        # Email บริษัท — รองรับทั้ง space และ ","
        m_email = re.search(r'E-mail[,\s]+([a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+)', company_text, re.IGNORECASE)
        if m_email: data['internship_place']['email'] = m_email.group(1).strip()

        if mentor_text:
            # ชื่อพี่เลี้ยง — รองรับทั้ง "Name, xxx" และ "Name xxx\n"
            m_mname = re.search(r'ชื่อ-นามสกุล/Name[,\s]+([ก-๙a-zA-Z][ก-๙a-zA-Z\s]+?)(?:\n|,\s*ตำแหน่ง|ตำแหน่ง)', mentor_text)
            if not m_mname:
                m_mname = re.search(r'(?:Name|ชื่อ-นามสกุล)[^,\n]*[,\s]+([^\n,]+)', mentor_text, re.IGNORECASE)
            if m_mname:
                parts = clean_value(m_mname.group(1)).split()
                data['mentor']['first_name'] = parts[0] if parts else ""
                data['mentor']['last_name'] = " ".join(parts[1:]) if len(parts) > 1 else ""

            # เบอร์พี่เลี้ยง — รองรับทั้ง "Telephone No. 08xxx" และ "Telephone No, 08xxx"
            m_mphone = re.search(r'โทรศัพท์/Telephone No[.,\s]+(0\d[\d\s\-]+?)(?:\s+E-mail|,\s*E-mail|\s*$)', mentor_text, re.IGNORECASE)
            if not m_mphone:
                m_mphone = re.search(r'(?:Telephone|โทรศัพท์)[^,\n]*[,\s]+([\d\s-]{9,})', mentor_text, re.IGNORECASE)
            if m_mphone: data['mentor']['phone'] = clean_value(m_mphone.group(1))

        m_period = re.search(r'ระหว่างวันที่\s*(.*?)\s*ถึงวันที่\s*(.*?)\s*(?:จำนวน|$)', text)
        if m_period:
            data['internship_period']['start_date'] = thai_date_to_iso(clean_value(m_period.group(1)))
            data['internship_period']['end_date'] = thai_date_to_iso(clean_value(m_period.group(2)))

        return data

    def process_document(self, file_path_or_list: Union[str, List[str]], file_type: str, doc_type: str = 'application_form') -> Dict[str, Any]:
        temp_path = None
        try:
            if file_type.lower() == 'pdf':
                # ✅ PDF: อ่านทุกหน้า (ไม่จำกัด last_page)
                from pdf2image import convert_from_path
                images = convert_from_path(file_path_or_list, dpi=200)
                widths = [i.width for i in images]
                heights = [i.height for i in images]
                stitched = Image.new('RGB', (max(widths), sum(heights)), (255, 255, 255))
                y = 0
                for img in images:
                    stitched.paste(img, (0, y))
                    y += img.height
                fd, temp_path = tempfile.mkstemp(suffix='.jpg')
                os.close(fd)
                stitched.save(temp_path, 'JPEG', quality=95)
                data = self.extract_structured_data(temp_path, doc_type)

            elif isinstance(file_path_or_list, list) and len(file_path_or_list) > 1:
                # ✅ รูปภาพหลายไฟล์: ต่อรูปก่อน
                temp_path = self._stitch_images(file_path_or_list)
                data = self.extract_structured_data(temp_path, doc_type)

            else:
                # รูปภาพไฟล์เดียว
                path = file_path_or_list[0] if isinstance(file_path_or_list, list) else file_path_or_list
                data = self.extract_structured_data(path, doc_type)

            return {'success': True, 'data': data}

        except Exception as e:
            return {'success': False, 'error': str(e)}
        finally:
            if temp_path and os.path.exists(temp_path):
                os.remove(temp_path)


_ocr_instance = None
def process_document_ocr(file_path_or_list: Union[str, List[str]], file_type: str, doc_type: str = 'application_form') -> Dict[str, Any]:
    global _ocr_instance
    if _ocr_instance is None:
        _ocr_instance = TyphoonOCR()
    return _ocr_instance.process_document(file_path_or_list, file_type, doc_type)