import re
from functools import wraps
from flask import Blueprint, render_template, redirect, url_for, flash, request
from flask_login import login_user, logout_user, login_required, current_user
from models import db, User, Address, CollectionCenter, RecyclerOrg

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
                flash(f"Access denied: This section requires {', '.join(allowed_roles)} privileges.", "danger")
                return redirect(url_for('index'))
            return f(*args, **kwargs)
        return decorated_function
    return decorator

@auth_bp.route('/login', methods=['GET', 'POST'])
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
            next_page = request.args.get('next')
            if next_page and next_page.startswith('/') and not next_page.startswith('//'):
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
        role = request.form.get('role', 'household').strip().lower()

        # Validation
        if not name or not email or not password:
            flash("Please fill in all required fields.", "warning")
            return render_template('auth/register.html')

        if not EMAIL_REGEX.match(email):
            flash("Please enter a valid email address.", "warning")
            return render_template('auth/register.html')

        if len(password) < 6:
            flash("Password must be at least 6 characters long.", "warning")
            return render_template('auth/register.html')

        if password != confirm_password:
            flash("Passwords do not match.", "warning")
            return render_template('auth/register.html')

        if User.query.filter_by(email=email).first():
            flash("An account with this email address already exists. Please log in.", "warning")
            return redirect(url_for('auth.login'))

        if role not in ['household', 'worker', 'center', 'recycler', 'admin']:
            role = 'household'

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

            # If center role, create facility profile
            elif role == 'center':
                center_name = request.form.get('center_name', f"{name}'s Waste Segregation Center").strip()
                center_addr = request.form.get('center_address', 'Kolhapur Industrial Area').strip()
                center = CollectionCenter(
                    user_id=new_user.id,
                    center_name=center_name or f"{name}'s Waste Segregation Center",
                    address=center_addr or 'Kolhapur Industrial Area',
                    contact_number=phone
                )
                db.session.add(center)

            # If recycler role, create recycler organization profile
            elif role == 'recycler':
                org_name = request.form.get('org_name', f"{name} Materials Recovery").strip()
                org_addr = request.form.get('org_address', 'Kolhapur Recovery Park').strip()
                recycler = RecyclerOrg(
                    user_id=new_user.id,
                    org_name=org_name or f"{name} Materials Recovery",
                    address=org_addr or 'Kolhapur Recovery Park'
                )
                db.session.add(recycler)

            db.session.commit()
            flash("Account successfully created! Please log in with your credentials.", "success")
            return redirect(url_for('auth.login'))

        except Exception as e:
            db.session.rollback()
            flash("An unexpected error occurred during account creation. Please try again.", "danger")
            return render_template('auth/register.html')

    return render_template('auth/register.html')

@auth_bp.route('/logout')
@login_required
def logout():
    logout_user()
    flash("You have been securely logged out.", "info")
    return redirect(url_for('index'))
