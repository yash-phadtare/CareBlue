"""Private S3 object storage with short-lived, authenticated image URLs."""
import os
from io import BytesIO
from flask import current_app

def s3_client():
    import boto3
    return boto3.client("s3", endpoint_url=current_app.config["S3_ENDPOINT_URL"],
                        region_name=current_app.config["S3_REGION"],
                        aws_access_key_id=current_app.config["S3_ACCESS_KEY_ID"],
                        aws_secret_access_key=current_app.config["S3_SECRET_ACCESS_KEY"])

def save_image(image, filename, hospital_id=None, kind='doctors'):
    from flask import session
    hospital_id = hospital_id or session.get('hospital_id')
    if not hospital_id:
        raise ValueError('A hospital is required for uploads.')
    key = f'tenants/{int(hospital_id)}/{kind}/{filename}'
    bucket = current_app.config["S3_BUCKET"]
    if bucket:
        output = BytesIO()
        image.save(output, "JPEG", quality=85)
        s3_client().put_object(Bucket=bucket, Key=key, Body=output.getvalue(), ContentType="image/jpeg",
                              ServerSideEncryption="AES256")
        return "s3:" + key
    folder = os.path.join(current_app.config["UPLOAD_FOLDER"],f'tenants/{int(hospital_id)}/{kind}')
    os.makedirs(folder, exist_ok=True)
    image.save(os.path.join(folder, filename), "JPEG", quality=85)
    return 'private:' + key

def image_url(path):
    from flask import url_for
    return url_for('tenant.asset',path=path) if path else ''


import secrets


def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in current_app.config['ALLOWED_EXTENSIONS']


def save_doctor_photo(file, hospital_id=None, kind='doctors'):
    """Validate, normalize and store an uploaded doctor photo.

    Verifies image integrity with Pillow, resizes to a bounded JPEG with a
    random filename (prevents collisions and path games). Returns the
    private storage reference, or None when no usable file was provided.
    """
    from PIL import Image, UnidentifiedImageError
    if not file or not getattr(file, 'filename', None):
        return None
    if not allowed_file(file.filename):
        return None
    try:
        probe = Image.open(file.stream)
        if probe.width * probe.height > 16000000:
            return None
        probe.verify()
        file.stream.seek(0)
        img = Image.open(file.stream).convert('RGB')
    except (UnidentifiedImageError, OSError, ValueError, Image.DecompressionBombError):
        return None
    img.thumbnail((512, 512))
    filename = secrets.token_hex(12) + '.jpg'
    return save_image(img, filename, hospital_id, kind)


def save_hospital_logo(file,hospital_id):
    return save_doctor_photo(file,hospital_id,'logos')
