import os
import re
import json
import torch
import tempfile
from typing import Dict, Any
from PIL import Image
from peft import PeftModel
from transformers import AutoModelForImageTextToText, AutoProcessor
from qwen_vl_utils import process_vision_info

class TyphoonOCR:
    def __init__(self, model_path: str = None):
        if model_path is None:
            raw_path = r"D:\ProjectFlask\models\Typhoon-OCR-HighDetail-Model"
            model_path = os.path.abspath(raw_path)
        
        self.model_path = model_path
        base_model_id = "typhoon-ai/typhoon-ocr1.5-2b"
        
        try:
            base_model = AutoModelForImageTextToText.from_pretrained(
                base_model_id,
                dtype=torch.float16 if torch.cuda.is_available() else torch.float32,
                device_map="auto",
                trust_remote_code=True
            )
            self.model = PeftModel.from_pretrained(base_model, model_path)
            self.model = self.model.merge_and_unload()
            self.processor = AutoProcessor.from_pretrained(base_model_id, trust_remote_code=True)
            print("✅ Typhoon OCR System Online (Raw Parser Mode)")
        except Exception as e:
            print(f"❌ Load Error: {e}")
            raise

    def _get_empty_structure(self):
        return {
            'student': {'first_name': '', 'last_name': '', 'major': ''},
            'internship_place': {
                'company_name': '', 'address': '', 'province': '', 'country': 'ประเทศไทย',
                'phone': '', 'fax': '', 'email': '', 'business_type': ''
            },
            'mentor': {
                'first_name': '', 'last_name': '', 'position': '',
                'department': '', 'phone': '', 'email': ''
            },
            'internship_period': {'start_date': '', 'end_date': ''},
            'student_count': 1,
            'contact_location': ''
        }

    def extract_structured_data(self, image_path: str) -> Dict[str, Any]:
        try:
            # เปลี่ยน Prompt: ปล่อยให้โมเดลอ่านตามธรรมชาติ (เพราะมันทำได้ดีที่สุดแล้ว)
            prompt = "อ่านข้อความทั้งหมดในเอกสารนี้ตามลำดับบรรทัด และรักษาโครงสร้างตารางไว้"

            image = Image.open(image_path).convert("RGB")
            messages = [{"role": "user", "content": [{"type": "image", "image": image}, {"type": "text", "text": prompt}]}]
            
            text = self.processor.apply_chat_template(messages, tokenize=False, add_generation_prompt=True)
            image_inputs, _ = process_vision_info(messages)
            
            inputs = self.processor(text=[text], images=image_inputs, padding=True, return_tensors="pt").to(self.model.device)
            
            with torch.inference_mode():
                generated_ids = self.model.generate(**inputs, max_new_tokens=1024, do_sample=False)
            
            output_text = self.processor.batch_decode(generated_ids[:, inputs.input_ids.shape[1]:], skip_special_tokens=True)[0]
            print(f"DEBUG: AI Raw Response ->\n{output_text}")
            
            return self.parse_internship_data(output_text)
        except Exception as e:
            print(f"❌ Extraction Error: {e}")
            return self._get_empty_structure()

    def parse_internship_data(self, text: str) -> Dict[str, Any]:
        """ดึงข้อมูลจากข้อความดิบด้วย Regex ที่แก้ปัญหา Markdown และข้อความติดกัน"""
        data = self._get_empty_structure()
        
        # 1. ทำความสะอาดข้อความเบื้องต้น (ลบเครื่องหมาย ** ที่ AI ชอบเติมมา)
        text = text.replace('*', '')
        
        def clean(val):
            if not val: return ""
            # ลบ tag html หรือเครื่องหมายแปลกๆ ท้ายประโยค
            val = re.sub(r'[<>]+$', '', val).strip()
            return val

        # 2. ส่วนสถานประกอบการ (ยึดคำภาษาอังกฤษเป็นหลัก เพื่อเลี่ยงปัญหาเครื่องหมาย /)
        m_comp = re.search(r'Employer Name[:\s]*(.*?)(?:\n|$)', text, re.IGNORECASE)
        if m_comp: data['internship_place']['company_name'] = clean(m_comp.group(1))

        m_addr = re.search(r'Address[:\s]*(.*?)(?:\n|$)', text, re.IGNORECASE)
        if m_addr: data['internship_place']['address'] = clean(m_addr.group(1))

        # หา Fax ก่อน แล้วค่อยหา Phone เพราะในกระดาษ Fax อยู่หน้า Phone
        m_fax = re.search(r'Fax No\.?[:\s]*(.*?)(?:\s+โทรศัพท์|\s+Telephone|\n|$)', text, re.IGNORECASE)
        if m_fax: data['internship_place']['fax'] = clean(m_fax.group(1))

        m_phone = re.search(r'Telephone[:\s]*(.*?)(?:\s+E-mail|\n|$)', text, re.IGNORECASE)
        if m_phone: data['internship_place']['phone'] = clean(m_phone.group(1))

        m_email = re.search(r'E-mail[:\s]*(.*?)(?:\s+ประเภทธุรกิจ|\s+Business|\n|$)', text, re.IGNORECASE)
        if m_email: data['internship_place']['email'] = clean(m_email.group(1))

        m_bus = re.search(r'Business Type[:\s]*(.*?)(?:\n|$)', text, re.IGNORECASE)
        if m_bus: data['internship_place']['business_type'] = clean(m_bus.group(1))

        # 3. ข้อมูลนักศึกษา (จากตาราง HTML)
        m_student = re.search(r'<td>1</td>\s*<td>(.*?)</td>\s*<td>(.*?)</td>', text, re.IGNORECASE)
        if m_student:
            data['student']['first_name'] = clean(m_student.group(1))
            data['student']['major'] = clean(m_student.group(2))

        # 4. ส่วนพี่เลี้ยง (Mentor) - ตัดเอาเฉพาะครึ่งล่างของหน้ากระดาษ
        if "ผู้ประสานงาน" in text:
            mentor_section = text.split("ผู้ประสานงาน")[-1]
            
            m_mname = re.search(r'Name[:\s]*(.*?)(?:\n|$)', mentor_section, re.IGNORECASE)
            if m_mname: 
                full = clean(m_mname.group(1))
                parts = full.split()
                if len(parts) >= 2:
                    data['mentor']['first_name'] = parts[0]
                    data['mentor']['last_name'] = " ".join(parts[1:])
                else:
                    data['mentor']['first_name'] = full

            m_mpos = re.search(r'Position[:\s]*(.*?)(?:\s+แผนก|\s+Department|\n|$)', mentor_section, re.IGNORECASE)
            if m_mpos: data['mentor']['position'] = clean(m_mpos.group(1))

            m_mdep = re.search(r'Department[:\s]*(.*?)(?:\n|$)', mentor_section, re.IGNORECASE)
            if m_mdep: data['mentor']['department'] = clean(m_mdep.group(1))

            m_mphone = re.search(r'Telephone No\.?[:\s]*(.*?)(?:\s+E-mail|\n|$)', mentor_section, re.IGNORECASE)
            if m_mphone: data['mentor']['phone'] = clean(m_mphone.group(1))

            m_memail = re.search(r'E-mail[:\s]*(.*?)(?:\s+โดย|\n|$)', mentor_section, re.IGNORECASE)
            if m_memail: data['mentor']['email'] = clean(m_memail.group(1))

        return data

    def process_document(self, file_path: str, file_type: str) -> Dict[str, Any]:
        try:
            if file_type.lower() == 'pdf':
                from pdf2image import convert_from_path
                # ใช้ DPI 200 เพื่อความเร็วและลด Noise
                images = convert_from_path(file_path, first_page=1, last_page=1, dpi=200)
                with tempfile.NamedTemporaryFile(suffix='.jpg', delete=False) as tmp:
                    images[0].save(tmp.name, 'JPEG', quality=95)
                    temp_path = tmp.name
                data = self.extract_structured_data(temp_path)
                if os.path.exists(temp_path): os.unlink(temp_path)
            else:
                data = self.extract_structured_data(file_path)
            
            # คำนวณ Confidence
            flat_values = []
            for v in data.values():
                if isinstance(v, dict): flat_values.extend(v.values())
                else: flat_values.append(v)
            
            filled = sum(1 for x in flat_values if x and str(x).strip())
            total = len(flat_values)
            
            # ถ้าอ่านได้มากกว่า 2 ค่า ถือว่า OK (เพราะเอกสารจริงอาจกรอกไม่ครบ)
            return {'success': True, 'data': data, 'confidence': (filled/total) if total > 0 else 0}
            
        except Exception as e:
            return {'success': False, 'error': str(e), 'data': self._get_empty_structure(), 'confidence': 0.0}

_ocr_instance = None
def process_document_ocr(file_path: str, file_type: str) -> Dict[str, Any]:
    global _ocr_instance
    if _ocr_instance is None: _ocr_instance = TyphoonOCR()
    return _ocr_instance.process_document(file_path, file_type)