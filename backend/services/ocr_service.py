import re
import os

# OCR dependencies are optional — not installed on Render free tier
# (easyocr + opencv are too large for free tier memory limits)
OCR_AVAILABLE = False
reader = None

try:
    import easyocr
    import cv2
    import numpy as np
    reader = easyocr.Reader(['en'], gpu=False)
    OCR_AVAILABLE = True
except ImportError:
    pass


def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in {'png', 'jpg', 'jpeg'}

def process_prescription_image(image_path):
    """
    Reads image, preprocesses it, runs OCR, and extracts optical details.
    Returns an error dict if OCR libraries are not installed.
    """
    if not OCR_AVAILABLE:
        return {"error": "OCR not available on this server. Please enter prescription details manually."}

    try:
        # 1. Read Image
        img = cv2.imread(image_path)
        if img is None:
            return {"error": "Could not read image"}

        # 2. Preprocessing (Grayscale + Thresholding)
        gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
        _, thresh = cv2.threshold(gray, 150, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)

        # 3. Run EasyOCR
        results = reader.readtext(thresh, detail=0)

        # 4. Parse Text
        extracted_data = parse_ocr_text(results)

        return extracted_data
    except Exception as e:
        return {"error": str(e)}

def parse_ocr_text(text_list):
    """
    Extract common prescription values from EasyOCR text fragments.

    OCR cannot reliably recover the two-dimensional layout of every handwritten
    prescription, so this parser only fills a field when it finds an eye/field
    label or a conventional eye-value sequence. The caller must still ask the
    user to verify the auto-filled values.
    """
    full_text = " ".join(str(text) for text in text_list).upper()
    full_text = re.sub(r'\s+', ' ', full_text).strip()

    data = {
        "re_sph": None, "re_cyl": None, "re_axis": None,
        "le_sph": None, "le_cyl": None, "le_axis": None,
        "re_nv_sph": None, "re_nv_cyl": None, "re_nv_axis": None,
        "le_nv_sph": None, "le_nv_cyl": None, "le_nv_axis": None,
        "addition": None,
        "pd_right": None, "pd_left": None, "pd_total": None,
    }

    # Split at NV/reading headers when present so an OD/OS entry from the near
    # section never overwrites the distant-vision values.
    near_header = re.search(r'\b(?:NEAR\s+VISION|N\s*\.?V\.?|READING)\b', full_text)
    dv_text = full_text[:near_header.start()] if near_header else full_text
    nv_text = full_text[near_header.end():] if near_header else ''

    _populate_eye_values(data, dv_text, 're', 'le')
    if nv_text:
        _populate_eye_values(data, nv_text, 're_nv', 'le_nv')

    data['addition'] = _find_labeled_number(full_text, r'\b(?:ADD|ADDITION)\b', 0, 4)
    _populate_pd_values(data, full_text)
    return data


_NUMBER_PATTERN = r'[+-]?\d+(?:\.\d{1,2})?'
_RIGHT_EYE_PATTERN = r'\b(?:RIGHT|OD|RE)\b'
_LEFT_EYE_PATTERN = r'\b(?:LEFT|OS|LE)\b'


def _format_number(value):
    """Return a browser-friendly numeric string while preserving an OCR sign."""
    return value.strip() if value is not None else None


def _find_labeled_number(text, label_pattern, minimum=None, maximum=None):
    match = re.search(
        rf'{label_pattern}\s*(?:[:=]|IS)?\s*({_NUMBER_PATTERN})', text,
    )
    if not match:
        return None
    value = float(match.group(1))
    if (minimum is not None and value < minimum) or (maximum is not None and value > maximum):
        return None
    return _format_number(match.group(1))


def _eye_block(text, eye_pattern, other_eye_pattern):
    """Return text after an eye label, ending at the next opposite-eye label."""
    match = re.search(eye_pattern, text)
    if not match:
        return ''
    following = text[match.end():]
    next_eye = re.search(other_eye_pattern, following)
    return following[:next_eye.start()] if next_eye else following[:120]


def _extract_eye_values(block):
    if not block:
        return (None, None, None)

    sph = _find_labeled_number(block, r'\bSPH(?:ERE)?\b', -30, 30)
    cyl = _find_labeled_number(block, r'\bCYL(?:INDER)?\b', -20, 20)
    axis = _find_labeled_number(block, r'\bAX(?:IS)?\b', 0, 180)
    values = re.findall(_NUMBER_PATTERN, block)

    # Conventional unlabelled format: OD/OS <SPH> <CYL> <AXIS>.
    if sph is None and values:
        candidate = float(values[0])
        if -30 <= candidate <= 30:
            sph = _format_number(values[0])
    if cyl is None and len(values) > 1:
        candidate = float(values[1])
        if -20 <= candidate <= 20:
            cyl = _format_number(values[1])
    if axis is None and len(values) > 2:
        candidate = float(values[2])
        if 0 <= candidate <= 180:
            axis = _format_number(values[2])
    return sph, cyl, axis


def _populate_eye_values(data, text, right_prefix, left_prefix):
    right = _extract_eye_values(_eye_block(text, _RIGHT_EYE_PATTERN, _LEFT_EYE_PATTERN))
    left = _extract_eye_values(_eye_block(text, _LEFT_EYE_PATTERN, _RIGHT_EYE_PATTERN))
    for prefix, values in ((right_prefix, right), (left_prefix, left)):
        data[f'{prefix}_sph'], data[f'{prefix}_cyl'], data[f'{prefix}_axis'] = values


def _populate_pd_values(data, text):
    # PD may be printed as R/L labels, a total (binocular) value, or a pair
    # such as "PD 32 / 32". Restricting accepted ranges avoids treating an axis
    # or invoice number as a pupil distance.
    data['pd_right'] = _find_labeled_number(
        text, r'\b(?:PD\s*(?:RIGHT|OD|RE)|(?:RIGHT|OD|RE)\s*PD)\b', 20, 40,
    )
    data['pd_left'] = _find_labeled_number(
        text, r'\b(?:PD\s*(?:LEFT|OS|LE)|(?:LEFT|OS|LE)\s*PD)\b', 20, 40,
    )
    data['pd_total'] = _find_labeled_number(
        text, r'\b(?:PD\s*)?(?:TOTAL\s*PD|PD\s*TOTAL|BINOCULAR\s*PD|PD\s*OU)\b', 50, 80,
    )

    pair = re.search(rf'\bPD\b\s*[:=]?\s*({_NUMBER_PATTERN})\s*(?:/|\\|,|X)\s*({_NUMBER_PATTERN})', text)
    if pair:
        right, left = (float(pair.group(1)), float(pair.group(2)))
        if 20 <= right <= 40 and 20 <= left <= 40:
            data['pd_right'] = data['pd_right'] or _format_number(pair.group(1))
            data['pd_left'] = data['pd_left'] or _format_number(pair.group(2))
            data['pd_total'] = data['pd_total'] or _format_number(str(right + left))

    if data['pd_total'] is None:
        # A lone "PD 64" is conventionally a binocular PD.
        data['pd_total'] = _find_labeled_number(text, r'\bPD\b', 50, 80)
