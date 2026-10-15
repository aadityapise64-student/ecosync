import re
from functools import wraps
from flask import Blueprint, render_template, redirect, url_for, flash, request, abort, current_app
from flask_login import login_user, logout_user, login_required, current_user
from models import db, User, Address
from extensions import limiter

auth_bp = Blueprint('auth', __name__)

EMAIL_REGEX = re.compile(r'^[\w\.-]+@[\w\.-]+\.\w+$')

def role_required(allowed_roles):
    """Decorator to enforce role-based access control"""
    def decorator(f):
        @wraps(f)
        def decorated_function(*args, **kwargs):
            if not current_user.is_authenticated:
                return redirect(url_for('auth.login', next=request.url))
            if current_user.role not in allowed_roles:
                abort(403)
            return f(*args, **kwargs)
        return decorated_function
    return decorator

@auth_bp.route('/login', methods=['GET', 'POST'])
@limiter.limit('10 per minute', methods=['POST'], error_message='Too many login attempts. Please wait a minute and try again.')
def login():
    if current_user.is_authenticated:
        return redirect(url_for('index'))

    if request.method == 'POST':
        email = request.form.get('email', '').strip().lower()
        password = request.form.get('password', '')
        remember = bool(request.form.get('remember'))

        if not email or not password:
            flash("Please provide both email address and password.", "warning")
            return render_template('auth/login.html')

        user = User.query.filter_by(email=email).first()
        if user and user.check_password(password):
            login_user(user, remember=remember)
            flash(f"Welcome back, {user.name}!", "success")
            
            # Next URL redirect safety: ensure local relative path only
            next_page = request.args.get('next') or request.form.get('next')
            if next_page and next_page.startswith('/') and not next_page.startswith('//') and '\\' not in next_page:
                return redirect(next_page)
            return redirect(url_for('index'))
        else:
            flash("Invalid email or password. Please try again.", "danger")

    return render_template('auth/login.html')

@auth_bp.route('/register', methods=['GET', 'POST'])
def register():
    if current_user.is_authenticated:
        return redirect(url_for('index'))

    if request.method == 'POST':
        name = request.form.get('name', '').strip()
        email = request.form.get('email', '').strip().lower()
        password = request.form.get('password', '')
        confirm_password = request.form.get('confirm_password', '')
        phone = request.form.get('phone', '').strip()
        # Public self-registration must never grant privileged roles. Staff and
        # admin accounts should be provisioned through a controlled process.
        role = 'household'

        # Validation
        if not name or not email or not password:
            flash("Please fill in all required fields.", "warning")
            return render_template('auth/register.html')

        if not EMAIL_REGEX.match(email):
            flash("Please enter a valid email address.", "warning")
            return render_template('auth/register.html')

        if len(password) < 10:
            flash("Password must be at least 10 characters long.", "warning")
            return render_template('auth/register.html')

        if password != confirm_password:
            flash("Passwords do not match.", "warning")
            return render_template('auth/register.html')

        if User.query.filter_by(email=email).first():
            flash("An account with this email address already exists. Please log in.", "warning")
            return redirect(url_for('auth.login'))

        try:
            # Create user
            new_user = User(
                name=name,
                email=email,
                phone=phone,
                role=role,
                reward_points=50 if role == 'household' else 0  # 50 welcome bonus points for new households
            )
            new_user.set_password(password)
            db.session.add(new_user)
            db.session.flush()  # Generate new_user.id

            # If household provided address details during registration
            if role == 'household':
                addr_line = request.form.get('address_line', '').strip()
                area = request.form.get('area', '').strip()
                city = request.form.get('city', 'Kolhapur').strip()
                pincode = request.form.get('pincode', '').strip()
                if addr_line and pincode:
                    address = Address(
                        user_id=new_user.id,
                        title="Home",
                        address_line=addr_line,
                        area=area,
                        city=city,
                        pincode=pincode
                    )
                    db.session.add(address)

            db.session.commit()
            flash("Account successfully created! Please log in with your credentials.", "success")
            return redirect(url_for('auth.login'))

        except Exception:
            db.session.rollback()
            current_app.logger.exception("Account creation failed.")
            flash("An unexpected error occurred during account creation. Please try again.", "danger")
            return render_template('auth/register.html')

    return render_template('auth/register.html')

@auth_bp.route('/logout', methods=['POST'])
@login_required
def logout():
    logout_user()
    flash("You have been securely logged out.", "info")
    return redirect(url_for('index'))
