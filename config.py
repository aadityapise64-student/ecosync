import os
from dotenv import load_dotenv

BASE_DIR = os.path.abspath(os.path.dirname(__file__))
# Load environment variables from .env file if present
load_dotenv(os.path.join(BASE_DIR, '.env'))

class Config:
    SECRET_KEY = os.environ.get('SECRET_KEY')
    DATABASE_URL_CONFIGURED = bool(os.environ.get('DATABASE_URL', '').strip())
    REQUIRE_DATABASE_URL = os.environ.get('REQUIRE_DATABASE_URL', 'false').strip().lower() == 'true'
    _database_url = os.environ.get(
        'DATABASE_URL', 
        f"sqlite:///{os.path.join(BASE_DIR, 'instance', 'waste_management.db')}"
    )
    if _database_url.startswith('postgres://'):
        _database_url = _database_url.replace('postgres://', 'postgresql+psycopg://', 1)
    elif _database_url.startswith('postgresql://'):
        _database_url = _database_url.replace('postgresql://', 'postgresql+psycopg://', 1)
    SQLALCHEMY_DATABASE_URI = _database_url
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    
    # Database connection pool stability
    SQLALCHEMY_ENGINE_OPTIONS = {
        "pool_pre_ping": True
    }
    
    # Upload configurations for waste photos
    UPLOAD_FOLDER = os.path.join(BASE_DIR, 'instance', 'uploads')
    SUPABASE_URL = os.environ.get('SUPABASE_URL', '').rstrip('/')
    # Secret API key: backend only. Never expose this to templates or the browser.
    SUPABASE_SECRET_KEY = os.environ.get('SUPABASE_SECRET_KEY', '')
    SUPABASE_STORAGE_BUCKET = os.environ.get('SUPABASE_STORAGE_BUCKET', 'user-media')
    SUPABASE_STORAGE_TIMEOUT = 20
    REQUIRE_CLOUD_STORAGE = os.environ.get('REQUIRE_CLOUD_STORAGE', 'false').strip().lower() == 'true'
    MAX_CONTENT_LENGTH = 16 * 1024 * 1024  # 16 MB max upload size
    MAX_IMAGE_PIXELS = 20_000_000
    ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg', 'webp'}
    
    # Session cookie security settings
    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = 'Lax'
    SESSION_COOKIE_SECURE = os.environ.get('SESSION_COOKIE_SECURE', 'false').lower() == 'true'

    # Roboflow AI Trash Detection & Classification Model Configuration
    ROBOFLOW_API_KEY = os.environ.get('ROBOFLOW_API_KEY', '')
    ROBOFLOW_WORKSPACE = os.environ.get('ROBOFLOW_WORKSPACE', 'aaditya-pise')
    ROBOFLOW_WORKFLOW_ID = os.environ.get('ROBOFLOW_WORKFLOW_ID', 'general-segmentation-api-7')
    ROBOFLOW_API_URL = os.environ.get('ROBOFLOW_API_URL', 'https://serverless.roboflow.com')
    ROBOFLOW_CLASSES = os.environ.get('ROBOFLOW_CLASSES', 'PAPER, PLASTIC, GLASS, METAL, CARDBOARD')
    # Synthetic detections are opt-in for local UI development only.
    AI_DEMO_MODE = os.environ.get('AI_DEMO_MODE', 'false').strip().lower() == 'true'
