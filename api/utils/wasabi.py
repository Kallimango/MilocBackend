import boto3
from django.conf import settings
import tempfile
from .encryption import decrypt_file

def get_s3_client():
    return boto3.client(
        "s3",
        aws_access_key_id=settings.AWS_ACCESS_KEY_ID,
        aws_secret_access_key=settings.AWS_SECRET_ACCESS_KEY,
        endpoint_url=settings.AWS_S3_ENDPOINT_URL,
        region_name=settings.AWS_S3_REGION_NAME,
    )

def generate_signed_url(key, expires=300):
    s3 = get_s3_client()
    return s3.generate_presigned_url(
        "get_object",
        Params={
            "Bucket": settings.AWS_STORAGE_BUCKET_NAME,
            "Key": key,
        },
        ExpiresIn=expires,
    )

def get_decrypted_temp_file(key, user):
    """
    Downloads encrypted file from S3, decrypts it locally, and returns the temp file path.
    Caller is responsible for deleting temp file.
    """
    s3 = get_s3_client()
    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=key.split('.')[-1])
    s3.download_file(settings.AWS_STORAGE_BUCKET_NAME, key, tmp.name)
    decrypted_tmp = tempfile.NamedTemporaryFile(delete=False, suffix=key.split('.')[-1])
    decrypt_file(tmp.name, user, decrypted_tmp.name)
    tmp.close()
    # remove the original encrypted temp
    import os
    os.remove(tmp.name)
    return decrypted_tmp.name
