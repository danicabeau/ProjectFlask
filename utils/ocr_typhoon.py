import os
import re
import json
import torch
import tempfile
import gc
import sys
from typing import Dict, Any, List, Union, Callable, Optional
from PIL import Image, ImageEnhance, ImageFilter
from peft import PeftModel
from transformers import AutoModelForImageTextToText, AutoProcessor
from qwen_vl_utils import process_vision_info

# ================================================================
# ⚙️ CONFIG — แม่นที่สุดภายใน 5 นาที
# ================================================================
FORCE_CPU = True
USE_BFLOAT16 = True        # bfloat16 บน Ryzen 6800HS: เร็ว + ถูกต้อง
MAX_LONG_SIDE = 1400           # 0 = ไม่ resize เลย ให้โมเดลเห็นภาพคมชัดสุด
PDF_DPI = 200               # DPI สูง → ลายมือคมชัด → อ่านแม่นขึ้น
JPEG_QUALITY = 95
MAX_TOKENS_PAGE1 = 900      # ← เพิ่มจาก 700 เพราะข้อมูลมารดาอยู่ท้ายหน้า 1 โดนตัด
MAX_TOKENS_PAGE2 = 400
MAX_TOKENS_ACCEPTANCE = 900
ENHANCE_IMAGE = True        # เพิ่ม contrast + sharpen ก่อนส่งเข้าโมเดล


def thai_date_to_iso(date_str):
    if not date_str: return ""
    months_full = {
        'มกราคม': '01', 'กุมภาพันธ์': '02', 'มีนาคม': '03', 'เมษายน': '04',
        'พฤษภาคม': '05', 'มิถุนายน': '06', 'กรกฎาคม': '07', 'สิงหาคม': '08',
        'กันยายน': '09', 'ตุลาคม': '10', 'พฤศจิกายน': '11', 'ธันวาคม': '12'
    }
    months_abbr = {
        'ม.ค.': '01', 'ก.พ.': '02', 'มี.ค.': '03', 'เม.ย.': '04',
        'พ.ค.': '05', 'มิ.ย.': '06', 'ก.ค.': '07', 'ส.ค.': '08',
        'ก.ย.': '09', 'ต.ค.': '10', 'พ.ย.': '11', 'ธ.ค.': '12'
    }
    try:
        s = date_str.strip()
        # ลอง format ย่อก่อน เช่น "5 พ.ค.69" หรือ "5 พ.ค. 69"
        m_abbr = re.match(r'(\d{1,2})\s*([ก-๙]+\.(?:[ก-๙]+\.)?)\s*(\d{2,4})', s)
        if m_abbr:
            day = m_abbr.group(1).zfill(2)
            month_str = m_abbr.group(2).strip()
            year_raw = int(m_abbr.group(3))
            month = months_abbr.get(month_str, '')
            if not month:
                # ลองเพิ่ม . ต่อท้ายถ้าไม่มี
                for k, v in months_abbr.items():
                    if month_str.replace('.', '') == k.replace('.', ''):
                        month = v; break
            if month:
                # ปี 2 หลัก: 69 → 2569 → CE 2026, ปี 4 หลัก: 2569 → CE 2026
                if year_raw < 100:
                    year_be = year_raw + 2500
                else:
                    year_be = year_raw
                year_ce = year_be - 543 if year_be > 2400 else year_be
                return f"{year_ce}-{month}-{day}"

        # Fallback: format เต็ม เช่น "5 พฤษภาคม 2569"
        parts = s.split()
        if len(parts) >= 3:
            day = parts[0].zfill(2)
            month = months_full.get(parts[1], '01')
            year = str(int(parts[2]) - 543)
            return f"{year}-{month}-{day}"
    except: pass
    return ""


def _resize_if_needed(image, max_long_side=MAX_LONG_SIDE):
    if max_long_side <= 0:
        return image
    w, h = image.size
    if max(w, h) > max_long_side:
        scale = max_long_side / max(w, h)
        new_w, new_h = int(w * scale), int(h * scale)
        image = image.resize((new_w, new_h), Image.LANCZOS)
        print(f"  ⚡ Resized: {w}x{h} → {new_w}x{new_h}")
    return image


def _enhance_for_ocr(image):
    """ปรับภาพให้เหมาะกับ OCR ลายมือ — เพิ่ม contrast + sharpen"""
    if not ENHANCE_IMAGE:
        return image
    image = ImageEnhance.Contrast(image).enhance(1.3)
    image = ImageEnhance.Sharpness(image).enhance(1.5)
    image = ImageEnhance.Brightness(image).enhance(1.05)
    print("  🔧 Image enhanced (contrast+sharpen)")
    return image


class ProgressCallback:
    def __init__(self, callback=None, max_tokens=800):
        self._callback = callback
        self._max_tokens = max_tokens
        self._current_percent = 0

    def _emit(self, percent, message):
        percent = max(0, min(100, percent))
        if percent <= self._current_percent and percent < 100: return
        self._current_percent = percent
        if self._callback:
            self._callback(percent, message)
        else:
            bar_len = 30
            filled = int(bar_len * percent / 100)
            bar = '█' * filled + '░' * (bar_len - filled)
            sys.stdout.write(f'\r  [{bar}] {percent:3d}%  {message}')
            sys.stdout.flush()
            if percent >= 100: sys.stdout.write('\n')

    def on_preprocess(self): self._emit(5, "กำลังเตรียมรูปภาพ...")
    def on_page(self, n, total): self._emit(10 + int(70 * n / total), f"AI กำลังอ่านหน้า {n}/{total}...")
    def on_parse(self): self._emit(90, "กำลัง parse ข้อมูล...")
    def on_done(self): self._emit(100, "เสร็จสิ้น ✅")


class TyphoonOCR:
    def __init__(self, model_path=None):
        if model_path is None:
            model_path = os.path.abspath(r"D:\ProjectFlask\models\Typhoon-OCR-HighDetail-Model")

        base_model_id = "typhoon-ai/typhoon-ocr1.5-2b"
        dtype = torch.bfloat16 if USE_BFLOAT16 else torch.float32
        device = "cpu" if FORCE_CPU else "auto"

        print(f"🔄 Loading Typhoon OCR (CPU + LoRA)...")
        print(f"   CPU: Ryzen 7 6800HS | Dtype: {dtype}")

        base_model = AutoModelForImageTextToText.from_pretrained(
            base_model_id, dtype=dtype, device_map=device, trust_remote_code=True)
        self.model = PeftModel.from_pretrained(base_model, model_path).merge_and_unload()
        self.processor = AutoProcessor.from_pretrained(base_model_id, trust_remote_code=True)
        self.device = torch.device("cpu") if FORCE_CPU else self.model.device
        print(f"✅ Typhoon OCR Online (CPU + LoRA + {dtype})")

    def _get_empty_structure(self):
        return {
            'internship_place': {'company_name': '', 'phone': '', 'address': '', 'email': ''},
            'mentor': {'first_name': '', 'last_name': '', 'phone': ''},
            'internship_period': {'start_date': '', 'end_date': ''}}

    def _get_application_structure(self):
        return {
            'student_info': {'name_th': '', 'name_en': '', 'student_id': '', 'year': '',
                             'gpax': '', 'dob': '', 'address_reg': '', 'phone': '', 'military_status': ''},
            'family_info': {'father': {'name': '', 'age': '', 'job': '', 'phone': ''},
                            'mother': {'name': '', 'age': '', 'job': '', 'phone': ''}},
            'emergency': {'name': '', 'phone': '', 'address': ''}}

    def _clear_memory(self):
        gc.collect()

    def _ocr_single_image(self, image, prompt, max_new_tokens=800):
        image = _resize_if_needed(image)
        image = _enhance_for_ocr(image)
        messages = [{"role": "user", "content": [
            {"type": "image", "image": image}, {"type": "text", "text": prompt}]}]
        text = self.processor.apply_chat_template(messages, tokenize=False, add_generation_prompt=True)
        image_inputs, _ = process_vision_info(messages)
        inputs = self.processor(text=[text], images=image_inputs, padding=True, return_tensors="pt")
        if FORCE_CPU:
            inputs = {k: v.to("cpu") if hasattr(v, 'to') else v for k, v in inputs.items()}
        with torch.inference_mode():
            output_ids = self.model.generate(**inputs, max_new_tokens=max_new_tokens, do_sample=False)
        generated_ids = output_ids[:, inputs['input_ids'].shape[1]:]
        result = self.processor.batch_decode(generated_ids, skip_special_tokens=True)[0]
        del inputs, image_inputs, output_ids, generated_ids
        self._clear_memory()
        return result

    PROMPT_PAGE1 = "อ่านข้อมูลที่เขียนในแบบฟอร์มนี้ทุกช่อง ตามที่เห็นจริง ห้ามแต่งเพิ่ม"
    PROMPT_PAGE2 = "อ่านข้อมูลที่เขียนในแบบฟอร์มนี้ทุกช่อง ตามที่เห็นจริง ห้ามแต่งเพิ่ม"
    PROMPT_ACCEPTANCE = "อ่านข้อมูลที่เขียนในเอกสารนี้ทุกช่อง ตามที่เห็นจริง ห้ามแต่งเพิ่ม"

    def process_document(self, file_path_or_list, file_type, doc_type='application_form', progress_callback=None):
        progress = ProgressCallback(callback=progress_callback)
        try:
            if file_type.lower() == 'pdf':
                from pdf2image import convert_from_path
                if progress: progress.on_preprocess()
                images = convert_from_path(file_path_or_list, dpi=PDF_DPI)
                total_pages = min(len(images), 2 if doc_type == 'application_form' else 1)
                all_text = ""
                for i in range(total_pages):
                    if progress: progress.on_page(i + 1, total_pages)
                    prompt = self.PROMPT_PAGE1 if (doc_type == 'application_form' and i == 0) else (self.PROMPT_PAGE2 if doc_type == 'application_form' else self.PROMPT_ACCEPTANCE)
                    max_tok = MAX_TOKENS_PAGE1 if (doc_type == 'application_form' and i == 0) else (MAX_TOKENS_PAGE2 if doc_type == 'application_form' else MAX_TOKENS_ACCEPTANCE)
                    print(f"  📖 Processing page {i+1}/{total_pages}...")
                    page_text = self._ocr_single_image(images[i].convert("RGB"), prompt, max_tok)
                    print(f"  DEBUG Page {i+1} Response:\n{page_text}\n{'='*50}")
                    all_text += page_text + "\n"
                if progress: progress.on_parse()
                data = self.parse_application_data(all_text) if doc_type == 'application_form' else self.parse_internship_data(all_text)
                if progress: progress.on_done()
                return {'success': True, 'data': data}
            else:
                if isinstance(file_path_or_list, list) and len(file_path_or_list) > 1:
                    imgs = [Image.open(p).convert("RGB") for p in file_path_or_list]
                    total_h = sum(im.height for im in imgs)
                    max_w = max(im.width for im in imgs)
                    stitched = Image.new('RGB', (max_w, total_h), (255, 255, 255))
                    y = 0
                    for im in imgs: stitched.paste(im, (0, y)); y += im.height
                    image = stitched
                else:
                    path = file_path_or_list[0] if isinstance(file_path_or_list, list) else file_path_or_list
                    image = Image.open(path).convert("RGB")
                if progress: progress.on_preprocess()
                prompt = self.PROMPT_PAGE1 if doc_type == 'application_form' else self.PROMPT_ACCEPTANCE
                max_tok = MAX_TOKENS_PAGE1 if doc_type == 'application_form' else MAX_TOKENS_ACCEPTANCE
                if progress: progress.on_page(1, 1)
                output_text = self._ocr_single_image(image, prompt, max_tok)
                print(f"  DEBUG Response:\n{output_text}")
                if progress: progress.on_parse()
                data = self.parse_application_data(output_text) if doc_type == 'application_form' else self.parse_internship_data(output_text)
                if progress: progress.on_done()
                return {'success': True, 'data': data}
        except Exception as e:
            print(f"ERROR: {e}")
            import traceback; traceback.print_exc()
            return {'success': False, 'error': str(e)}

    # ================================================================
    # PARSER: ใบสมัคร — แก้ regex ให้จับ output จริงจากโมเดล
    # ================================================================
    def parse_application_data(self, text):
        data = self._get_application_structure()
        text = text.replace('&amp;', '&')

        # ลบ ** (bold) และ * (bullet) ออก
        clean = re.sub(r'\*\*', '', text)
        clean = re.sub(r'^\s*\*\s+', '', clean, flags=re.MULTILINE)

        # --- แยกโซน ---
        stu_zone, fam_zone, emg_zone = clean, "", ""
        for pat in [r'ข้อมูลครอบครัว\s*\(?Family', r'Family details', r'2\.\s*ข้อมูลครอบครัว']:
            parts = re.split(pat, clean, maxsplit=1, flags=re.I)
            if len(parts) > 1:
                stu_zone, rest = parts[0], parts[1]
                for epat in [r'บุคคล(?:ที่)?ติดต่อ', r'Emergency', r'3\.\s*บุคคล']:
                    ep = re.split(epat, rest, maxsplit=1, flags=re.I)
                    if len(ep) > 1: fam_zone, emg_zone = ep[0], ep[1]; break
                if not emg_zone: fam_zone = rest
                break

        # --- นักศึกษา ---
        m_th = re.search(r'ไทย[:\s]+([ก-๙][ก-๙\s]+?)(?:\n|Name|$)', stu_zone)
        if not m_th:
            m_th = re.search(r'(?:นาย|นาง|นางสาว)\s+([ก-๙][ก-๙\s]+?)(?:\n|Name|English|$)', stu_zone)
        if m_th: data['student_info']['name_th'] = m_th.group(1).strip()

        m_en = re.search(r'English[:\s]+([A-Za-z][A-Za-z\s\.]+?)(?:\n|รหัส|Student|$)', stu_zone, re.I)
        if not m_en:
            m_en = re.search(r'(?:Mr\.|Mrs\.|Ms\.|Miss\.?)\s+([A-Za-z][A-Za-z\s\.]+?)(?:\n|$)', stu_zone, re.I)
        if m_en: data['student_info']['name_en'] = m_en.group(1).strip()

        m_id = re.search(r'(?:รหัสนักศึกษา|Student\s*identification\s*No\.?)[^0-9]*(\d{10,13})', stu_zone)
        if m_id: data['student_info']['student_id'] = m_id.group(1)

        m_year = re.search(r'(?:ชั้นปี(?:ที่)?|Year)[^0-9]*(\d)\b', stu_zone)
        if m_year: data['student_info']['year'] = m_year.group(1)

        m_gpax = re.search(r'(?:GPAX|เกรดเฉลี่ยรวม)[^0-9]*(\d+\.\d+)', stu_zone)
        if m_gpax: data['student_info']['gpax'] = m_gpax.group(1)

        m_dob = re.search(r'(?:วันเดือนปีเกิด|Date of birth|วันเกิด)[^0-9]*(\d{1,2}\s*/\s*\d{1,2}\s*/\s*\d{4})', stu_zone)
        if m_dob:
            dc = m_dob.group(1).replace(' ', '')
            p = dc.split('/')
            if len(p) == 3:
                try:
                    be = int(p[2]); ce = be - 543 if be > 2400 else be
                    data['student_info']['dob'] = f"{ce}-{p[1].zfill(2)}-{p[0].zfill(2)}"
                except: pass

        # ที่อยู่ — หยุดที่ \n แล้วตัด "โทรศัพท์..." ที่ติดท้าย
        m_addr = re.search(r'(?:ที่อยู่ตามทะเบียน|Registered Address)[^0-9ก-๙]*([0-9ก-๙][^\n]*)', stu_zone)
        if m_addr:
            addr = re.split(r'\s*โทรศัพท์', m_addr.group(1))[0].strip()
            data['student_info']['address_reg'] = addr

        m_phone = re.search(r'(?:โทรศัพท์|Telephone|Mobile)[^0-9]*(0\d{2}[-\s]?\d{3,4}[-\s]?\d{3,4})', stu_zone)
        if m_phone: data['student_info']['phone'] = m_phone.group(1).strip()

        if 'ยังไม่ได้รับการ' in stu_zone or 'Not yet served' in stu_zone:
            data['student_info']['military_status'] = 'ยังไม่ได้รับการเกณฑ์'
        elif 'ปลดเป็นทหารกอง' in stu_zone:
            data['student_info']['military_status'] = 'ปลดเป็นทหารกองหนุน'
        elif 'ได้รับการยกเว้น' in stu_zone or 'Exempted' in stu_zone:
            data['student_info']['military_status'] = 'ได้รับการยกเว้น'

        # --- ครอบครัว ---
        fam = fam_zone if fam_zone else clean

        # บิดา — รองรับทั้ง "บิดา ชื่อ-สกุล จักทำ ทำดี อายุ" และ "บิดา ชื่อ-สกุล: นายธนัฐ"
        f_name = re.search(r'บิดา\s*ชื่อ[-\s]?สกุล[:\s]*(?:นาย|นาง|นางสาว)?\s*([ก-๙][ก-๙\s]+?)(?:\s*อายุ|\s*\n|$)', fam)
        if f_name: data['family_info']['father']['name'] = f_name.group(1).strip()

        f_age = re.search(r'(?:บิดา|Father).*?(?:อายุ|Age)[:\s]*(\d{2,3})\s*ปี', fam, re.DOTALL)
        if f_age: data['family_info']['father']['age'] = f_age.group(1)

        f_job = re.search(r'(?:บิดา|Father).*?(?:อาชีพ|Occupation)[:\s]*([ก-๙A-Za-z][^\n(]{2,}?)(?:\n|\(|$)', fam, re.DOTALL)
        if f_job: data['family_info']['father']['job'] = f_job.group(1).strip()

        f_phone = re.search(r'(?:บิดา|Father).*?(0\d{2}[-\s]?\d{3,4}[-\s]?\d{3,4})', fam, re.DOTALL)
        if f_phone: data['family_info']['father']['phone'] = f_phone.group(1)

        # มารดา — ค้นเฉพาะหลัง "มารดา/Mother"
        mt_match = re.search(r'(มารดา|Mother)(.*)', fam, re.DOTALL | re.I)
        mt = mt_match.group(2) if mt_match else ""

        m_name = re.search(r'ชื่อ[-\s]?สกุล[:\s]*(?:นาย|นาง|นางสาว)?\s*([ก-๙][ก-๙\s]+?)(?:\s*อายุ|\s*\n|$)', mt)
        if m_name: data['family_info']['mother']['name'] = m_name.group(1).strip()

        m_age = re.search(r'(?:อายุ|Age)[:\s]*(\d{2,3})\s*ปี', mt)
        if m_age: data['family_info']['mother']['age'] = m_age.group(1)

        m_job = re.search(r'(?:อาชีพ|Occupation)[:\s]*([ก-๙A-Za-z][^\n(]{2,}?)(?:\n|\(|$)', mt)
        if m_job:
            val = m_job.group(1).strip()
            if val and val != '-' and 'Mother' not in val and 'name' not in val and 'ที่อยู่' not in val:
                data['family_info']['mother']['job'] = val

        m_phone = re.search(r'(0\d{2}[-\s]?\d{3,4}[-\s]?\d{3,4})', mt)
        if m_phone: data['family_info']['mother']['phone'] = m_phone.group(1)

        # --- ฉุกเฉิน ---
        if emg_zone:
            emg = emg_zone
        else:
            emg_m = re.search(r'((?:3\.\s*บุคคล|ฉุกเฉิน|Emergency).*)', clean, re.DOTALL | re.I)
            emg = emg_m.group(1) if emg_m else ""

        if emg:
            e_name = re.search(r'(?:3\.1\s+)?ชื่อ[-\s]?สกุล\s*(?:\(นาย/นาง/นางสาว\))?\s*[:\s]*([ก-๙][ก-๙\s]+?)(?:\s*ความเกี่ยวข้อง|\n|$)', emg)
            if e_name: data['emergency']['name'] = e_name.group(1).strip()

            e_phone = re.search(r'(?:Mobile phone|โทรศัพท์มือถือ)[^0-9]*(0\d{2}[-\s]?\d{3,4}[-\s]?\d{3,4})', emg)
            if not e_phone:
                e_phone = re.search(r'(?:โทรศัพท์|Telephone)[^0-9]*(0\d{2}[-\s]?\d{3,4}[-\s]?\d{3,4})', emg)
            if e_phone: data['emergency']['phone'] = e_phone.group(1)

            e_addr = re.search(r'(?:ที่อยู่|Address)\s*(?:\(Address\))?\s*[^ก-๙0-9]*([0-9ก-๙].+?)(?=\s*โทรศัพท์|\s*Telephone|\s*3\.2\s|\s*อาจารย์|\n|$)', emg)
            if e_addr:
                addr_val = re.sub(r'\s+', ' ', e_addr.group(1)).strip()
                # ตัดรหัสไปรษณีย์ที่อาจมีขยะติดท้าย: เก็บถึง 5 หลักสุดท้าย
                m_zip = re.search(r'^(.*?\d{5})\b', addr_val)
                if m_zip:
                    addr_val = m_zip.group(1).strip()
                data['emergency']['address'] = addr_val

        print(f"DEBUG Parsed: {json.dumps(data, ensure_ascii=False, indent=2)}")
        return data

    # ================================================================
    # PARSER: ใบตอบรับ (ไม่เปลี่ยน)
    # ================================================================
    def parse_internship_data(self, text):
        data = self._get_empty_structure()

        # ลบ ** (bold) และ * (bullet) ออก — เหมือน parse_application_data
        text = re.sub(r'\*\*', '', text)
        text = re.sub(r'^\s*\*\s+', '', text, flags=re.MULTILINE)

        def clean_value(val):
            if not val: return ""
            val = val.replace('*', '')
            for lab in [r'ชื่อสถานประกอบการ', r'Employer\s*Name', r'ที่อยู่', r'Address',
                        r'โทรศัพท์', r'Telephone', r'ชื่อ-นามสกุล', r'Name']:
                val = re.sub(lab, '', val, flags=re.IGNORECASE)
            return re.sub(r'^[\s,:/.-]+|[\s,:/.-]+$', '', val).strip()

        m_comp = re.search(r'(?:ชื่อสถานประกอบการ|ชื่อบริษัท|Employer Name)[/ชื่อบริษัท]*\s*[,:\s]+([^\n]+)', text, re.I)
        if m_comp: data['internship_place']['company_name'] = clean_value(m_comp.group(1))
        m_addr = re.search(r'(?:ที่อยู่เลขที่|ที่อยู่|[Aa]ddress)[/a-zA-Z]*\s*[,:\s]+(.+?)(?=\n.*โทรสาร|\nโทรสาร|โทรสาร|,\s*โทรสาร|\n\s*โทรศัพท์|\nโทรศัพท์)', text, re.I | re.DOTALL)
        if m_addr: data['internship_place']['address'] = clean_value(m_addr.group(1))
        m_phone = re.search(r'โทรศัพท์/Telephone[,\s]+(0\d[\d\s\-]+?)(?:\s+E-mail|,\s*E-mail|\s*$)', text, re.I)
        if not m_phone:
            m_phone = re.search(r'(?:Telephone|โทรศัพท์)\s*[^,\n]*[,:\s]+([\d\s-]{9,})', text, re.I)
        if m_phone: data['internship_place']['phone'] = clean_value(m_phone.group(1))
        m_email = re.search(r'E-mail[,:\s]+([a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+)', text, re.I)
        if m_email: data['internship_place']['email'] = m_email.group(1).strip()

        split = re.search(r'(ผู้ประสานงาน|Coordinator)', text)
        if split:
            mt = text[split.start():]
            m_mn = re.search(r'ชื่อ-นามสกุล/Name[,:\s]+([ก-๙a-zA-Z][ก-๙a-zA-Z\s]+?)(?:\n|ตำแหน่ง)', mt)
            if not m_mn:
                m_mn = re.search(r'(?:Name|ชื่อ|ผู้ประสานงานชื่อ)\s*[^,\n]*[,:\s]+([^\n,]+)', mt, re.I)
            if m_mn:
                parts = clean_value(m_mn.group(1)).split()
                data['mentor']['first_name'] = parts[0] if parts else ""
                data['mentor']['last_name'] = " ".join(parts[1:]) if len(parts) > 1 else ""
            m_mp = re.search(r'(?:Telephone|โทรศัพท์|ผู้ประสานงานโทร)\s*[^,\n]*[,:\s]+([\d\s-]{9,})', mt, re.I)
            if m_mp: data['mentor']['phone'] = clean_value(m_mp.group(1))

        m_per = re.search(r'ระหว่างวันที่[:\s]*(.*?)\s*ถึงวันที่\s*(.*?)\s*(?:จำนวน|$)', text)
        if m_per:
            data['internship_period']['start_date'] = thai_date_to_iso(clean_value(m_per.group(1)))
            data['internship_period']['end_date'] = thai_date_to_iso(clean_value(m_per.group(2)))
        return data


_ocr_instance = None
def process_document_ocr(file_path_or_list, file_type, doc_type='application_form', progress_callback=None):
    """
    ฟังก์ชันหลักสำหรับเรียกใช้ OCR

    result = process_document_ocr("doc.pdf", "pdf")
    result = process_document_ocr("doc.pdf", "pdf", progress_callback=my_progress)
    """
    global _ocr_instance
    if _ocr_instance is None:
        _ocr_instance = TyphoonOCR()
    return _ocr_instance.process_document(file_path_or_list, file_type, doc_type, progress_callback)