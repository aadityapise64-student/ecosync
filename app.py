import os
import io
import mimetypes
from flask import Flask, render_template, redirect, url_for, abort, send_file, send_from_directory
from flask_login import LoginManager, current_user
from flask_wtf.csrf import CSRFProtect
from flask_migrate import Migrate
from config import Config
from extensions import limiter
from models import db, User, PickupRequest, AIScan
from services.media_storage import MediaStorageError, read_file, supabase_storage_enabled

login_manager = LoginManager()
login_manager.login_view = 'auth.login'
login_manager.login_message = 'Please log in to access this page.'
login_manager.login_message_category = 'info'
csrf = CSRFProtect()
migrate = Migrate()

@login_manager.user_loader
def load_user(user_id):
    try:
        return db.session.get(User, int(user_id))
    except (ValueError, TypeError):
        return None

def create_app(config_class=Config):
    app = Flask(__name__)
    app.config.from_object(config_class)
    if not app.config.get('SECRET_KEY'):
        raise RuntimeError('Set the SECRET_KEY environment variable before starting the application.')
    has_storage_url = bool(app.config.get('SUPABASE_URL', '').strip())
    has_storage_key = bool(app.config.get('SUPABASE_SECRET_KEY', '').strip())
    if has_storage_url != has_storage_key:
        raise RuntimeError('Configure both SUPABASE_URL and SUPABASE_SECRET_KEY, or leave both unset for local development.')
    if app.config.get('REQUIRE_DATABASE_URL') and not app.config.get('DATABASE_URL_CONFIGURED'):
        raise RuntimeError('DATABASE_URL is required for this deployment.')
    if app.config.get('REQUIRE_CLOUD_STORAGE') and not (has_storage_url and has_storage_key):
        raise RuntimeError('Supabase private storage is required for this deployment.')

    # Ensure instance and upload directories exist
    os.makedirs(os.path.join(app.root_path, 'instance'), exist_ok=True)
    os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)

    # Initialize extensions
    db.init_app(app)
    migrate.init_app(app, db)
    login_manager.init_app(app)
    csrf.init_app(app)
    limiter.init_app(app)

    @app.context_processor
    def inject_csrf_token():
        from flask_wtf.csrf import generate_csrf
        return {'csrf_token': generate_csrf}

    # Register Blueprints
    from routes.auth import auth_bp
    from routes.household import household_bp
    from routes.worker import worker_bp
    from routes.center import center_bp
    from routes.recycler import recycler_bp
    from routes.admin import admin_bp

    app.register_blueprint(auth_bp, url_prefix='/auth')
    app.register_blueprint(household_bp, url_prefix='/household')
    app.register_blueprint(worker_bp, url_prefix='/worker')
    app.register_blueprint(center_bp, url_prefix='/center')
    app.register_blueprint(recycler_bp, url_prefix='/recycler')
    app.register_blueprint(admin_bp, url_prefix='/admin')

    # Security headers middleware
    @app.after_request
    def add_security_headers(response):
        response.headers['X-Content-Type-Options'] = 'nosniff'
        response.headers['X-Frame-Options'] = 'SAMEORIGIN'
        response.headers['X-XSS-Protection'] = '1; mode=block'
        return response

    # Root landing page route
    @app.route('/')
    def index():
        if current_user.is_authenticated:
            # Direct logged-in users straight to their role dashboard
            role_routes = {
                'household': 'household.dashboard',
                'worker': 'worker.dashboard',
                'center': 'center.dashboard',
                'recycler': 'recycler.dashboard',
                'admin': 'admin.dashboard'
            }
            target_endpoint = role_routes.get(current_user.role, 'household.dashboard')
            return redirect(url_for(target_endpoint))
        return render_template('index.html')

    @app.route('/health')
    def health():
        return {'status': 'ok'}, 200

    @app.route('/media/<path:filename>')
    def serve_user_media(filename):
        if not current_user.is_authenticated or os.path.basename(filename) != filename:
            abort(404)
        pickup = PickupRequest.query.filter(
            (PickupRequest.photo_filename == filename) |
            (PickupRequest.ai_annotated_photo == filename)
        ).first()
        scan = AIScan.query.filter(
            (AIScan.original_filename == filename) |
            (AIScan.annotated_filename == filename)
        ).first()
        allowed = current_user.role == 'admin'
        if pickup:
            allowed = allowed or pickup.household_id == current_user.id
            allowed = allowed or (current_user.role == 'worker' and pickup.assignment and pickup.assignment.worker_id == current_user.id)
            allowed = allowed or (current_user.role == 'center' and current_user.center_profile and pickup.assignment and pickup.assignment.center_id == current_user.center_profile.id)
        if scan:
            allowed = allowed or scan.user_id == current_user.id
        if not allowed:
            abort(404)

        if supabase_storage_enabled():
            try:
                media_bytes = read_file(filename)
            except MediaStorageError:
                app.logger.warning('Private media provider download failed.')
                abort(503)
            if media_bytes is not None:
                response = send_file(
                    io.BytesIO(media_bytes),
                    mimetype=mimetypes.guess_type(filename)[0] or 'application/octet-stream',
                    download_name=filename,
                    conditional=False,
                )
                response.headers['Cache-Control'] = 'private, no-store'
                response.headers['X-Content-Type-Options'] = 'nosniff'
                return response

        folder = app.config['UPLOAD_FOLDER']
        if not os.path.isfile(os.path.join(folder, filename)):
            # Allow already-existing local demo uploads to remain viewable.
            legacy_folder = os.path.join(app.root_path, 'static', 'uploads')
            folder = legacy_folder
        response = send_from_directory(folder, filename, conditional=True)
        response.headers['Cache-Control'] = 'private, no-store'
        response.headers['X-Content-Type-Options'] = 'nosniff'
        return response

    @app.route('/static/uploads/<path:filename>')
    def block_public_uploads(filename):
        # Keep legacy upload paths private after moving new uploads out of static/.
        abort(404)

    # Context processor for global template variables
    @app.context_processor
    def inject_global_vars():
        return {
            'app_name': 'EcoSync',
            'current_user': current_user
        }

    # Error pages
    @app.errorhandler(403)
    def forbidden(e):
        return render_template('404.html'), 403

    @app.errorhandler(404)
    def page_not_found(e):
        return render_template('404.html'), 404

    @app.errorhandler(500)
    def internal_server_error(e):
        db.session.rollback()
        return render_template('500.html'), 500

    return app

if __name__ == '__main__':
    app = create_app()
    print("EcoSync Waste Management System starting on http://127.0.0.1:5000")
    app.run(host='0.0.0.0', port=int(os.environ.get('PORT', '5000')), debug=False)
