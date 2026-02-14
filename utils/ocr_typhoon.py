from peft import PeftModel
import os
import re
import json
from typing import Dict, Any
from datetime import datetime
from PIL import Image
import torch
from transformers import AutoModelForImageTextToText, AutoProcessor
from qwen_vl_utils import process_vision_info

class TyphoonOCR:
    def __init__(self, model_path: str = None):
        if model_path is None:
            # ใช้ Path ที่ถูกต้องของคุณ
            raw_path = r"D:\ProjectFlask\models\Typhoon-OCR-HighDetail-Model"
            model_path = os.path.abspath(raw_path)
        
        self.model_path = model_path
        base_model_id = "typhoon-ai/typhoon-ocr1.5-2b"
        
        print(f"🔄 Loading Base Model: {base_model_id}")
        print(f"💉 Adapter Path: {model_path}")
        
        try:
            base_model = AutoModelForImageTextToText.from_pretrained(
                base_model_id,
                torch_dtype=torch.float16 if torch.cuda.is_available() else torch.float32,
                device_map="auto",
                trust_remote_code=True
            )
            
            self.model = PeftModel.from_pretrained(base_model, model_path)
            self.model = self.model.merge_and_unload()
            
            self.processor = AutoProcessor.from_pretrained(
                base_model_id,
                trust_remote_code=True
            )
            
            self.processor.image_processor.min_pixels = 256 * 256
            self.processor.image_processor.max_pixels = 1024 * 1024
            
            print("✅ Typhoon OCR Loaded Successfully!")
            
        except Exception as e:
            print(f"❌ Failed to load model: {e}")
            raise

    def _resize_image(self, image, max_size=1024):
        width, height = image.size
        if max(width, height) > max_size:
            scale = max_size / max(width, height)
            new_width = int(width * scale)
            new_height = int(height * scale)
            return image.resize((new_width, new_height), Image.Resampling.LANCZOS)
        return image

    def _get_empty_structure(self):
        """โครงสร้างข้อมูลเริ่มต้น"""
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
            'internship_period': {'start_date': None, 'end_date': None},
            'student_count': 1,
            'contact_location': ''
        }

    def extract_structured_data(self, image_path: str) -> Dict[str, Any]:
        try:
            prompt = """ภารกิจ: อ่านข้อความที่เขียน/พิมพ์เติมลงในช่องว่างของแบบฟอร์ม "แบบยืนยันการตอบรับนักศึกษาสหกิจศึกษา"
คำสั่ง:
- อ่านข้อความ "ตามที่ปรากฏจริง" (เช่น asdasd, ฟหกฟหก) ห้ามแก้ไข ห้ามเดา
- ส่งออกเป็น JSON เท่านั้น (Strict JSON)

Mapping Rules (จับคู่คำในภาพ กับ ตัวแปร JSON):
1. [internship_place][company_name] -> อ่านข้อความหลัง "ชื่อสถานประกอบการ/Employer Name"
2. [internship_place][address] -> อ่านข้อความหลัง "ที่อยู่เลขที่/Address"
3. [internship_place][phone] -> อ่านข้อความหลัง "โทรศัพท์/Telephone" (บรรทัดบน)
4. [internship_place][fax] -> อ่านข้อความหลัง "โทรสาร/Fax No."
5. [internship_place][email] -> อ่านข้อความหลัง "E-mail" (บรรทัดบน)
6. [internship_place][business_type] -> อ่านข้อความหลัง "ประเภทธุรกิจ/Business Type"
7. [internship_period] -> อ่านวันที่จาก "ระหว่างวันที่" ถึง "ถึงวันที่"
8. [student_count] -> อ่านตัวเลขจาก "จำนวน" ... "คน"

9. [student] (ตาราง) -> ดูตารางด้านล่าง อ่านแถวที่มีข้อมูล
   - [first_name] [last_name] -> อ่านจากคอลัมน์ "ชื่อ - นามสกุล (นักศึกษา)"
   - [major] -> อ่านจากคอลัมน์ "สาขาวิชา"

10. [mentor] (ส่วนล่างสุด) -> ดูใต้หัวข้อ "ผู้ประสานงานของสถานประกอบการ"
    - [first_name] [last_name] -> อ่านหลัง "ชื่อ – นามสกุล/Name"
    - [position] -> อ่านหลัง "ตำแหน่ง/Position"
    - [department] -> อ่านหลัง "แผนก/ฝ่าย/Department"
    - [phone] -> อ่านหลัง "โทรศัพท์/Telephone No." (บรรทัดล่าง)
    - [email] -> อ่านหลัง "E-mail" (บรรทัดล่าง)
    - [contact_location] -> อ่านหลัง "โดยให้นักศึกษาติดต่อ/รายงานตัวได้ที่/Student contact"

Output JSON Structure:
{
  "student": { "first_name": "", "last_name": "", "major": "" },
  "internship_place": {
    "company_name": "", "address": "", "phone": "", "fax": "", "email": "", "business_type": "", "province": ""
  },
  "mentor": {
    "first_name": "", "last_name": "", "position": "", "department": "", "phone": "", "email": ""
  },
  "internship_period": { "start_date": "", "end_date": "" },
  "student_count": 1,
  "contact_location": ""
}"""

            image = Image.open(image_path).convert("RGB")
            image = self._resize_image(image)

            messages = [{"role": "user", "content": [{"type": "image", "image": image}, {"type": "text", "text": prompt}]}]
            
            text = self.processor.apply_chat_template(messages, tokenize=False, add_generation_prompt=True)
            image_inputs, video_inputs = process_vision_info(messages)
            
            inputs = self.processor(
                text=[text], images=image_inputs, videos=video_inputs, padding=True, return_tensors="pt"
            ).to(self.model.device)
            
            with torch.no_grad():
                generated_ids = self.model.generate(
                    **inputs, max_new_tokens=1024, do_sample=False, temperature=0.0
                )
            
            generated_ids_trimmed = [out_ids[len(in_ids):] for in_ids, out_ids in zip(inputs.input_ids, generated_ids)]
            output_text = self.processor.batch_decode(generated_ids_trimmed, skip_special_tokens=True, clean_up_tokenization_spaces=False)[0]
            
            print(f"\n📝 Raw Output:\n{output_text}\n")
            return self.parse_internship_data(output_text)
            
        except Exception as e:
            print(f"❌ OCR Error: {e}")
            return self._get_empty_structure()

    def parse_internship_data(self, text: str) -> Dict[str, Any]:
        """ตัวแปลงข้อมูลแบบ Robust (ไม่ง้อ JSON): ใช้ Regex เจาะหาค่าทีละตัว"""
        print(f"DEBUG Raw Text from AI: {text}") 
        
        data = self._get_empty_structure()
        
        def extract_val(keys, default=''):
            for key in keys:
                pattern = f'"{key}"\s*:?\s*"([^"]+)"'
                match = re.search(pattern, text)
                if match:
                    return match.group(1).strip()
            return default

        # 1. ข้อมูลสถานประกอบการ
        data['internship_place']['company_name'] = extract_val(['company_name', 'ชื่อสถานประกอบการ'])
        data['internship_place']['address'] = extract_val(['address', 'ที่อยู่'])
        data['internship_place']['phone'] = extract_val(['phone', 'โทรศัพท์'])
        data['internship_place']['fax'] = extract_val(['fax', 'โทรสาร'])
        data['internship_place']['email'] = extract_val(['email', 'อีเมล'])
        data['internship_place']['business_type'] = extract_val(['business_type', 'ประเภทธุรกิจ'])
        data['internship_place']['province'] = extract_val(['province', 'จังหวัด'])

        # 2. ข้อมูลนักศึกษา
        data['student']['first_name'] = extract_val(['first_name', 'ชื่อจริง'])
        data['student']['last_name'] = extract_val(['last_name', 'นามสกุล'])
        data['student']['major'] = extract_val(['major', 'สาขาวิชา'])

        # 3. ข้อมูลผู้ประสานงาน
        mentor_section = text
        if "mentor" in text or "ผู้ประสานงาน" in text:
            parts = re.split(r'"mentor"|"ผู้ประสานงาน"', text)
            if len(parts) > 1:
                mentor_section = parts[-1]
        
        def extract_mentor(key):
            pattern = f'"{key}"\s*:?\s*"([^"]+)"'
            match = re.search(pattern, mentor_section)
            return match.group(1).strip() if match else ''

        data['mentor']['first_name'] = extract_mentor('first_name')
        data['mentor']['last_name'] = extract_mentor('last_name')
        data['mentor']['position'] = extract_mentor('position')
        data['mentor']['department'] = extract_mentor('department')
        data['mentor']['phone'] = extract_mentor('phone')
        data['mentor']['email'] = extract_mentor('email')

        # 4. อื่นๆ
        data['internship_period']['start_date'] = extract_val(['start_date', 'วันเริ่ม'])
        data['internship_period']['end_date'] = extract_val(['end_date', 'วันสิ้นสุด'])
        data['contact_location'] = extract_val(['contact_location', 'สถานที่รายงานตัว'])
        
        count_match = re.search(r'"student_count"\s*:?\s*"?(\d+)"?', text)
        if count_match:
            data['student_count'] = int(count_match.group(1))

        return data

    def process_document(self, file_path: str, file_type: str) -> Dict[str, Any]:
        try:
            if file_type == 'pdf':
                from pdf2image import convert_from_path
                import tempfile
                images = convert_from_path(file_path, first_page=1, last_page=1, dpi=300)
                with tempfile.NamedTemporaryFile(suffix='.jpg', delete=False) as tmp:
                    images[0].save(tmp.name, 'JPEG', quality=95)
                    temp_path = tmp.name
                data = self.extract_structured_data(temp_path)
                os.unlink(temp_path)
            else:
                data = self.extract_structured_data(file_path)
            
            # คำนวณ Confidence (นับจำนวนฟิลด์ที่อ่านได้)
            filled = 0
            total = 0
            def count(d):
                c = 0
                t = 0
                for k, v in d.items():
                    if isinstance(v, dict):
                        sub_c, sub_t = count(v)
                        c += sub_c
                        t += sub_t
                    else:
                        t += 1
                        if v and str(v).strip():
                            c += 1
                return c, t
            
            filled, total = count(data)
            confidence = filled / total if total > 0 else 0
            
            return {'success': True, 'data': data, 'confidence': confidence}
            
        except Exception as e:
            print(f"❌ Process Error: {e}")
            return {'success': False, 'error': str(e), 'data': None}

# Instance Management
_ocr_instance = None
def get_ocr_instance():
    global _ocr_instance
    if _ocr_instance is None:
        _ocr_instance = TyphoonOCR()
    return _ocr_instance


def process_document_ocr(file_path: str, file_type: str) -> Dict[str, Any]:
    """ฟังก์ชันหลักสำหรับประมวลผลเอกสาร"""
    try:
        ocr = get_ocr_instance()
        return ocr.process_document(file_path, file_type)
    except Exception as e:
        print(f"❌ OCR Error: {e}")
        return {
            'success': False,
            'error': str(e),
            'data': {
                'student': {'first_name': '', 'last_name': '', 'major': ''},
                'internship_place': {
                    'company_name': '', 'address': '', 'province': '', 'country': 'ประเทศไทย',
                    'phone': '', 'fax': '', 'email': '', 'business_type': ''
                },
                'mentor': {
                    'first_name': '', 'last_name': '', 'position': '',
                    'department': '', 'phone': '', 'email': ''
                },
                'internship_period': {'start_date': None, 'end_date': None},
                'student_count': 1,
                'contact_location': ''
            },
            'confidence': 0.0
        }