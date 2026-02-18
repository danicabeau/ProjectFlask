import os
import re
import json
import torch
import tempfile
import gc
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
            print("🔄 Loading Typhoon OCR Model...")
            base_model = AutoModelForImageTextToText.from_pretrained(
                base_model_id,
                dtype=torch.float16 if torch.cuda.is_available() else torch.float32,
                device_map="auto",
                trust_remote_code=True
            )
            self.model = PeftModel.from_pretrained(base_model, model_path)
            self.model = self.model.merge_and_unload()
            self.processor = AutoProcessor.from_pretrained(base_model_id, trust_remote_code=True)
            print("✅ Typhoon OCR System Online (Hybrid Parser Mode)")
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
            prompt = "อ่านข้อความทั้งหมดในเอกสารนี้ตามลำดับบรรทัด และรักษาโครงสร้างตารางไว้"
            with Image.open(image_path) as img:
                image = img.convert("RGB")
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
        """Hybrid Parser: แบ่งโซน + จับหลังคอมมา (แม่นยำที่สุดสำหรับเคสนี้)"""
        data = self._get_empty_structure()
        
        def clean_value(val):
            if not val: return ""
            val = val.replace('*', '') 
            # ลบหัวข้อที่อาจติดมา
            labels = [
                r'ชื่อสถานประกอบการ', r'Employer\s*Name', r'ที่อยู่', r'Address',
                r'โทรศัพท์', r'Telephone', r'โทรสาร', r'Fax', r'E-mail', r'อีเมล',
                r'ประเภทธุรกิจ', r'Business\s*Type', r'ชื่อ-นามสกุล', r'Name',
                r'ตำแหน่ง', r'Position', r'แผนก/ฝ่าย', r'Department', 
                r'เลขที่', r'No\.', r'โดยให้นักศึกษา.*?ได้ที่', r'จ\.', r'จังหวัด'
            ]
            for label in labels:
                val = re.sub(label, '', val, flags=re.IGNORECASE)
            
            val = re.sub(r'^[\s,:/.-]+|[\s,:/.-]+$', '', val).strip()
            return val

        # ⭐ 1. แบ่งโซน (Zone Splitting)
        # ใช้คำว่า "ผู้ประสานงาน" เป็นจุดตัด
        split_match = re.search(r'(ผู้ประสานงาน|Coordinator)', text)
        if split_match:
            split_idx = split_match.start()
            company_text = text[:split_idx]
            mentor_text = text[split_idx:]
        else:
            company_text = text
            mentor_text = ""

        # ==========================================
        # 2. โซนบน: ข้อมูลสถานประกอบการ (จับหลังคอมมา)
        # ==========================================
        
        # ชื่อบริษัท: จับหลัง "Employer Name," หรือ "ชื่อสถานประกอบการ,"
        m_comp = re.search(r'(?:Employer Name|ชื่อสถานประกอบการ)[^,]*,\s*([^,]+)', company_text, re.IGNORECASE)
        if m_comp: data['internship_place']['company_name'] = clean_value(m_comp.group(1))

        # ที่อยู่: จับหลัง "Address,"
        m_addr = re.search(r'(?:Address|ที่อยู่)[^,]*,\s*(.*?)(?:,?\s*(?:โทร|Fax|Tel)|$)', company_text, re.IGNORECASE)
        if m_addr: 
            raw_addr = clean_value(m_addr.group(1))
            data['internship_place']['address'] = raw_addr
            
            # จังหวัด: หา "จ." หรือ "จังหวัด" ในที่อยู่ หรือใน text โซนบน
            m_prov = re.search(r'(?:จ\.|จังหวัด)\s*([ก-๙]+)', raw_addr) 
            if not m_prov:
                 m_prov = re.search(r'(?:จ\.|จังหวัด)\s*([ก-๙]+)', company_text)
            if m_prov: data['internship_place']['province'] = clean_value(m_prov.group(1))

        # เบอร์โทรบริษัท: จับหลัง "Telephone,"
        m_phone = re.search(r'(?:Telephone|โทรศัพท์)[^,]*,\s*([\d\s-]{9,})', company_text, re.IGNORECASE)
        if m_phone: data['internship_place']['phone'] = clean_value(m_phone.group(1))

        # อีเมลบริษัท: จับ Pattern Email
        m_email = re.search(r'([a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+)', company_text)
        if m_email: data['internship_place']['email'] = clean_value(m_email.group(1))

        # ประเภทธุรกิจ: จับหลัง "Business Type,"
        m_bus = re.search(r'(?:Business Type|ประเภทธุรกิจ)[^,]*,\s*([^,]+)', company_text, re.IGNORECASE)
        if m_bus: data['internship_place']['business_type'] = clean_value(m_bus.group(1))

        # ==========================================
        # 3. โซนบน: ข้อมูลนักศึกษา
        # ==========================================
        # จับ pattern: "1, ชื่อ, สาขา"
        m_student = re.search(r'1\s*,\s*([^,]+)\s*,\s*([^,]+)', company_text)
        if m_student:
            full_name = clean_value(m_student.group(1))
            data['student']['major'] = clean_value(m_student.group(2))
            parts = full_name.split()
            data['student']['first_name'] = parts[0] if parts else full_name
            data['student']['last_name'] = " ".join(parts[1:]) if len(parts) > 1 else ""

        # ระยะเวลา
        m_period = re.search(r'ระหว่างวันที่\s*(.*?)\s*ถึงวันที่\s*(.*?)\s*จำนวน', company_text)
        if m_period:
            data['internship_period']['start_date'] = clean_value(m_period.group(1))
            data['internship_period']['end_date'] = clean_value(m_period.group(2))
            
        m_count = re.search(r'จำนวน\s*(\d+)\s*คน', company_text)
        if m_count: data['student_count'] = int(m_count.group(1))

        # ==========================================
        # 4. โซนล่าง: ข้อมูลพี่เลี้ยง (จับหลังคอมมา)
        # ==========================================
        if mentor_text:
            # ชื่อพี่เลี้ยง: จับหลัง "Name,"
            m_mname = re.search(r'(?:Name|ชื่อ-นามสกุล)[^,]*,\s*([^,]+)', mentor_text, re.IGNORECASE)
            if m_mname:
                m_parts = clean_value(m_mname.group(1)).split()
                data['mentor']['first_name'] = m_parts[0] if m_parts else ""
                data['mentor']['last_name'] = " ".join(m_parts[1:]) if len(m_parts) > 1 else ""
            
            # ตำแหน่ง: จับหลัง "Position,"
            m_mpos = re.search(r'(?:Position|ตำแหน่ง)[^,]*,\s*([^,]+)', mentor_text, re.IGNORECASE)
            if m_mpos: data['mentor']['position'] = clean_value(m_mpos.group(1))

            # แผนก: จับหลัง "Department,"
            m_mdep = re.search(r'(?:Department|แผนก)[^,]*,\s*([^,]+)', mentor_text, re.IGNORECASE)
            if m_mdep: data['mentor']['department'] = clean_value(m_mdep.group(1))

            # เบอร์โทรพี่เลี้ยง: จับหลัง "Telephone No,"
            m_mphone = re.search(r'(?:Telephone|โทรศัพท์)[^,]*,\s*([\d\s-]{9,})', mentor_text, re.IGNORECASE)
            if m_mphone: data['mentor']['phone'] = clean_value(m_mphone.group(1))
            
            # อีเมลพี่เลี้ยง: จับ Pattern Email ในโซนล่าง
            m_memail = re.search(r'([a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+)', mentor_text)
            if m_memail: data['mentor']['email'] = clean_value(m_memail.group(1))

        return data

    def process_document(self, file_path: str, file_type: str) -> Dict[str, Any]:
        temp_path = None
        try:
            if file_type.lower() == 'pdf':
                from pdf2image import convert_from_path
                images = convert_from_path(file_path, first_page=1, last_page=1, dpi=200)
                fd, temp_path = tempfile.mkstemp(suffix='.jpg')
                os.close(fd)
                images[0].save(temp_path, 'JPEG', quality=95)
                data = self.extract_structured_data(temp_path)
            else:
                data = self.extract_structured_data(file_path)
            
            flat_values = []
            for k, v in data.items():
                if isinstance(v, dict): flat_values.extend(v.values())
                else: flat_values.append(v)
            
            filled = sum(1 for x in flat_values if x and str(x).strip())
            total = len(flat_values)
            
            # คะแนนความมั่นใจ
            base_score = filled / total if total > 0 else 0
            has_company = bool(data['internship_place']['company_name'])
            has_student = bool(data['student']['first_name'])
            has_mentor = bool(data['mentor']['first_name'])
            
            confidence = 0.98 if (has_company and has_student and has_mentor) else base_score

            if torch.cuda.is_available():
                torch.cuda.empty_cache()
            gc.collect()

            return {'success': True, 'data': data, 'confidence': confidence}
        except Exception as e:
            return {'success': False, 'error': str(e), 'data': self._get_empty_structure()}
        finally:
            if temp_path and os.path.exists(temp_path):
                try: os.remove(temp_path)
                except: pass

_ocr_instance = None
def process_document_ocr(file_path: str, file_type: str) -> Dict[str, Any]:
    global _ocr_instance
    if _ocr_instance is None: _ocr_instance = TyphoonOCR()
    return _ocr_instance.process_document(file_path, file_type)