import os

BASE_DIR = os.path.abspath(os.path.dirname(__file__))

class Config:
    SECRET_KEY = os.environ.get('SECRET_KEY')
    SQLALCHEMY_DATABASE_URI = os.environ.get(
        'DATABASE_URL', 
        f"sqlite:///{os.path.join(BASE_DIR, 'instance', 'waste_management.db')}"
    )
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    
    # Database connection pool stability
    SQLALCHEMY_ENGINE_OPTIONS = {
        "pool_pre_ping": True
    }
    
    # Upload configurations for waste photos
    UPLOAD_FOLDER = os.path.join(BASE_DIR, 'static', 'uploads')
    MAX_CONTENT_LENGTH = 16 * 1024 * 1024  # 16 MB max upload size
    ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg', 'webp', 'gif'}
    
    # Session cookie security settings
    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = 'Lax'
