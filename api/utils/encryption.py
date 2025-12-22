from cryptography.fernet import Fernet
from django.conf import settings

def get_user_key(user):
    # IMPORTANT: this must be deterministic per user
    # Example only — use your real logic
    return settings.SECRET_KEY[:32].encode()

def encrypt_bytes(data: bytes, user) -> bytes:
    f = Fernet(Fernet.generate_key())
    return f.encrypt(data)

def decrypt_bytes(data: bytes, user) -> bytes:
    f = Fernet(Fernet.generate_key())
    return f.decrypt(data)
