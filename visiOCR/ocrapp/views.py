import cv2
import numpy as np
import pytesseract
from datetime import datetime, timedelta
from django.shortcuts import render
from django.http import HttpResponse
from django.views.decorators.csrf import csrf_exempt
import re
from django.template.loader import get_template
from xhtml2pdf import pisa
import qrcode
import base64
from io import BytesIO
import mysql.connector
from mysql.connector import Error
from django.template.loader import render_to_string
import logging
import json

pytesseract.pytesseract.tesseract_cmd = 'C:\\Program Files\\Tesseract-OCR\\tesseract.exe'

logging.basicConfig(level=logging.DEBUG)

def home(request):
    return render(request, 'ocr_app/home.html')

def preprocess_image(image):
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    processed_image = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY | cv2.THRESH_OTSU)[1]
    return processed_image

def extract_info(image):
    processed_image = preprocess_image(image)
    text = pytesseract.image_to_string(processed_image)
    name, birth_date, pan_number, aadhaar_number, gender = parse_text(text)
    return name, birth_date, pan_number, aadhaar_number, gender

def parse_text(text):
    name = ""
    birth_date = ""
    pan_number = ""
    aadhaar_number = ""
    gender = ""

    all_text_list = re.split(r'[\n]', text)
    text_list = [i for i in all_text_list if i.strip() != ""]

    pan_pattern = r'[A-Z]{5}[0-9]{4}[A-Z]{1}'
    pan_match = re.search(pan_pattern, text)
    if pan_match:
        pan_number = pan_match.group(0).strip()

    aadhar_pattern = r'\d{4}\s\d{4}\s\d{4}'
    aadhar_match = re.search(aadhar_pattern, text)
    if aadhar_match:
        aadhaar_number = aadhar_match.group(0).strip()

    if any(word in text.lower() for word in ["male", "female"]):
        name, birth_date, gender = extract_aadhar_info(text_list)
    else:
        name, birth_date, gender = extract_pan_info(text)

    return name, birth_date, pan_number, aadhaar_number, gender

def extract_aadhar_info(text_list):
    user_dob = ""
    user_name = ""
    user_gender = ""
    aadhar_dob_pat = r'(YoB|YOB:|DOB:|DOB|AOB)'
    gender_pat = r'\b(?:male|female|transgender|other)\b'
    date_ele = ""
    index = None

    for idx, line in enumerate(text_list):
        if re.search(aadhar_dob_pat, line):
            index = re.search(aadhar_dob_pat, line).span()[1]
            date_ele = line
            dob_idx = idx
            break

    if index is not None:
        date_str = ''.join(char for char in date_ele[index:] if re.match(r'\d|/', char))
        user_dob = date_str

        user_name = text_list[dob_idx - 1]
        name_match = re.search(r'([A-Z][a-zA-Z\s]+)', user_name)
        if name_match:
            name = name_match.group(0).strip()
        else:
            name = ""

        for line in text_list:
            gender_match = re.search(gender_pat, line, re.IGNORECASE)
            if gender_match:
                user_gender = gender_match.group(0).capitalize()
                break

        return name, user_dob, user_gender
    else:
        return "", "", ""

def extract_pan_info(text):
    pancard_name = ""
    user_gender = ""
    name_patterns = [
        r'Name\s*\n([A-Z\s]+)', 
    ]
    gender_pat = r'\b(?:male|female|transgender|other)\b'

    for pattern in name_patterns:
        name_match = re.search(pattern, text)
        if name_match:
            matched_name = name_match.group(1).strip().replace('\n', ' ')
            pancard_name = matched_name
            break

    dob_match = re.search(r'(\d{2}/\d{2}/\d{4})', text, re.IGNORECASE)
    if dob_match:
        birth_date = dob_match.group(0).strip()
    else:
        birth_date = ""

    gender_match = re.search(gender_pat, text, re.IGNORECASE)
    if gender_match:
        user_gender = gender_match.group(0).capitalize()

    return pancard_name, birth_date, user_gender

def create_connection():
    try:
        connection = mysql.connector.connect(
            host='localhost',
            database='VISIOCR',
            user='root',
            password='altamash'
        )
        return connection
    except Error as e:
        logging.error("Error while connecting to MySQL: %s", e)
        return None

def create_table(connection):
    try:
        if connection.is_connected():
            cursor = connection.cursor()
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS extracted_data (
                    id INT AUTO_INCREMENT PRIMARY KEY, 
                    name VARCHAR(255), 
                    birth_date DATE, 
                    pan_number VARCHAR(10), 
                    aadhaar_number VARCHAR(70), 
                    age INT, 
                    gender VARCHAR(10), 
                    qr_code_image LONGBLOB
                )
            """)
            connection.commit()
            logging.debug("Table 'extracted_data' created successfully")
            cursor.close()
    except Error as e:
        logging.error("Error while creating table: %s", e)

def insert_data(connection, name, birth_date, pan_number, aadhaar_number, gender, qr_code_image_data, age):
    try:
        if connection.is_connected():
            cursor = connection.cursor()
            sanitized_name = name.replace("'", "''")
            if birth_date:
                birth_date = datetime.strptime(birth_date, "%d/%m/%Y").strftime("%Y-%m-%d")
            else:
                birth_date = None

            query = """
                INSERT INTO extracted_data (name, birth_date, pan_number, aadhaar_number, gender, qr_code_image, age)
                VALUES (%s, %s, %s, %s, %s, %s, %s)
            """
            values = (
                sanitized_name,
                birth_date,
                pan_number if pan_number else None,
                aadhaar_number if aadhaar_number else None,
                gender if gender else None,
                qr_code_image_data if qr_code_image_data else None,
                age
            )
            cursor.execute(query, values)
            connection.commit()
            logging.debug("Record inserted successfully: Name=%s, Birth Date=%s, PAN Number=%s, Aadhaar Number=%s, Gender=%s", sanitized_name, birth_date, pan_number, aadhaar_number, gender)
            cursor.close()
    except Error as e:
        logging.error("Error while inserting data into table: %s", e)

def process_image(image):
    try:
        name, birth_date, pan_number, aadhaar_number, gender = extract_info(image)
        logging.debug("Extracted Info: Name=%s, Birth Date=%s, PAN Number=%s, Aadhaar Number=%s, Gender=%s", name, birth_date, pan_number, aadhaar_number, gender)
        
        if not name or not birth_date:
            logging.error("Failed to extract valid name or birth date from the image.")
            return "", "", "", "", "", "", None

        connection = create_connection()
        if not connection:
            logging.error("Failed to establish a database connection.")
            return name, birth_date, "", pan_number, aadhaar_number, gender, None

        try:
            create_table(connection)
            data = {
                "name": name,
                "birth_date": birth_date,
                "pan_number": pan_number,
                "aadhaar_number": aadhaar_number,
                "gender": gender
            }
            qr_code_image_data, expiration_time = create_qr_code(data)
            birth_date_obj = datetime.strptime(birth_date, "%d/%m/%Y")
            age = (datetime.now() - birth_date_obj).days // 365
            insert_data(connection, name, birth_date, pan_number, aadhaar_number, gender, qr_code_image_data, age)
        except Exception as e:
            logging.error("Error processing image: %s", e)
            return name, birth_date, "", pan_number, aadhaar_number, gender, None
        finally:
            if connection and connection.is_connected():
                connection.close()
                logging.debug("MySQL connection is closed")

        return name, birth_date, qr_code_image_data, pan_number, aadhaar_number, gender, expiration_time
    except Exception as e:
        logging.error("An unexpected error occurred: %s", e)
        return "", "", "", "", "", "", None
    
def create_qr_code(data, expiration_hours=2):
        try:
            expiration_time = datetime.now() + timedelta(hours=expiration_hours)
            data['expiration_time'] = expiration_time.strftime('%Y-%m-%d %H:%M:%S')

            qr = qrcode.QRCode(
                version=1,
                error_correction=qrcode.constants.ERROR_CORRECT_L,
                box_size=10,
                border=4,
            )
            qr.add_data(json.dumps(data))
            qr.make(fit=True)
            img = qr.make_image(fill='black', back_color='white')
            buffered = BytesIO()
            img.save(buffered, format="PNG")
            qr_code_image_data = base64.b64encode(buffered.getvalue()).decode('utf-8')
            return qr_code_image_data, expiration_time
        except Exception as e:
            logging.error("Failed to create QR code: %s", e)
            return "", None

@csrf_exempt
def upload_image(request):
    if request.method == 'POST' and request.FILES.get('image'):
        image_file = request.FILES['image']
        image_data = np.frombuffer(image_file.read(), np.uint8)
        image = cv2.imdecode(image_data, cv2.IMREAD_COLOR)

        name, birth_date, qr_code_image_data, pan_number, aadhaar_number, gender, expiration_time = process_image(image)

        visit_date = request.POST.get('visit_date')
        duration = request.POST.get('duration')

        # Calculate age from birth_date
        if birth_date:
            birth_date_obj = datetime.strptime(birth_date, "%d/%m/%Y")
            age = (datetime.now() - birth_date_obj).days // 365
        else:
            age = ""

        context = {
            'name': name,
            'birth_date': birth_date,
            'qr_code_image_data': qr_code_image_data,
            'pan_number': pan_number,
            'aadhaar_number': aadhaar_number,
            'gender': gender,
            'visit_date': visit_date,
            'duration': duration,
            'age': age,
            'expiration_time': expiration_time
        }
        return render(request, 'ocr_app/result.html', context)
    return render(request, 'ocr_app/home.html')

@csrf_exempt
def download_pdf(request):
    template_path = 'ocr_app/pdf_template.html'
    context = {
        'name': request.POST.get('name'),
        'birth_date': request.POST.get('birth_date'),
        'age': request.POST.get('age'),
        'pan_number': request.POST.get('pan_number'),
        'aadhaar_number': request.POST.get('aadhaar_number'),
        'gender': request.POST.get('gender'),
        'visit_date': request.POST.get('visit_date'),
        'duration': request.POST.get('duration'),
    }

    # Render the template as a string
    html = render_to_string(template_path, context)

    # Create a PDF
    response = HttpResponse(content_type='application/pdf')
    response['Content-Disposition'] = 'attachment; filename="visitor_pass.pdf"'

    pisa_status = pisa.CreatePDF(
        html, dest=response
    )

    # If PDF creation fails, return an error message
    if pisa_status.err:
        return HttpResponse('We had some errors <pre>' + html + '</pre>')
    return response