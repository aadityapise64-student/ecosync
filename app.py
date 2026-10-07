import os
from flask import Flask, render_template, redirect, url_for
from flask_login import LoginManager, current_user
from config import Config
from models import db, User

login_manager = LoginManager()
login_manager.login_view = 'auth.login'
login_manager.login_message = 'Please log in to access this page.'
login_manager.login_message_category = 'info'

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

    # Ensure instance and upload directories exist
    os.makedirs(os.path.join(app.root_path, 'instance'), exist_ok=True)
    os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)

    # Initialize extensions
    db.init_app(app)
    login_manager.init_app(app)

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

    # Auto-create tables within application context
    with app.app_context():
        db.create_all()

    return app

if __name__ == '__main__':
    app = create_app()
    print("EcoSync Waste Management System starting on http://127.0.0.1:5000")
    app.run(host='0.0.0.0', port=5000, debug=True)
