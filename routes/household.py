import os
import uuid
from datetime import datetime, date
from flask import Blueprint, render_template, redirect, url_for, flash, request, current_app
from flask_login import login_required, current_user
from werkzeug.utils import secure_filename
from models import db, User, Address, PickupRequest, WorkerAssignment, SegregationRecord, Coupon, RewardTransaction
from routes.auth import role_required

household_bp = Blueprint('household', __name__)

def allowed_file(filename: str) -> bool:
    return '.' in filename and \
           filename.rsplit('.', 1)[1].lower() in current_app.config['ALLOWED_EXTENSIONS']

@household_bp.route('/dashboard')
@login_required
@role_required(['household', 'admin'])
def dashboard():
    addresses = Address.query.filter_by(user_id=current_user.id).all()
    requests = PickupRequest.query.filter_by(household_id=current_user.id)\
        .options(db.joinedload(PickupRequest.address))\
        .order_by(PickupRequest.created_at.desc()).all()
    
    # Calculate summary metrics
    total_requests = len(requests)
    active_requests = len([r for r in requests if r.status not in ['Segregated', 'Cancelled']])
    completed_requests = len([r for r in requests if r.status == 'Segregated'])
    
    # Available coupons count
    available_coupons = Coupon.query.filter(Coupon.is_active.is_(True), Coupon.valid_until >= date.today()).count()

    return render_template(
        'household/dashboard.html',
        addresses=addresses,
        requests=requests,
        total_requests=total_requests,
        active_requests=active_requests,
        completed_requests=completed_requests,
        available_coupons=available_coupons
    )

@household_bp.route('/request/new', methods=['GET', 'POST'])
@login_required
@role_required(['household', 'admin'])
def new_request():
    addresses = Address.query.filter_by(user_id=current_user.id).all()
    if not addresses:
        flash("Please add a collection address before scheduling your waste pickup.", "warning")
        return redirect(url_for('household.add_address'))

    if request.method == 'POST':
        address_id = request.form.get('address_id', type=int)
        waste_category = request.form.get('waste_category', 'Recyclable').strip()
        scheduled_date_str = request.form.get('scheduled_date')
        time_slot = request.form.get('time_slot', 'Morning (7-11 AM)').strip()
        notes = request.form.get('notes', '').strip()

        # Input validation
        if not address_id or not scheduled_date_str:
            flash("Please specify pickup address and preferred date.", "warning")
            return render_template('household/new_request.html', addresses=addresses, today=date.today().isoformat())

        # IDOR check: Verify address belongs to current user (unless admin)
        address = db.session.get(Address, address_id)
        if not address or (current_user.role != 'admin' and address.user_id != current_user.id):
            flash("Invalid address selected. Please choose one of your saved addresses.", "danger")
            return render_template('household/new_request.html', addresses=addresses, today=date.today().isoformat())

        valid_categories = ['Recyclable', 'Organic', 'Hazardous', 'Non-Recyclable']
        if waste_category not in valid_categories:
            waste_category = 'Recyclable'

        try:
            scheduled_date = datetime.strptime(scheduled_date_str, '%Y-%m-%d').date()
        except ValueError:
            flash("Invalid date format.", "warning")
            return render_template('household/new_request.html', addresses=addresses, today=date.today().isoformat())

        if scheduled_date < date.today():
            flash("Pickup date cannot be in the past.", "warning")
            return render_template('household/new_request.html', addresses=addresses, today=date.today().isoformat())

        # Photo upload handling (Optional waste photo for reference)
        photo_filename = None
        if 'photo' in request.files:
            file = request.files['photo']
            if file and file.filename and allowed_file(file.filename):
                safe_name = secure_filename(file.filename)
                ext = safe_name.rsplit('.', 1)[1].lower() if '.' in safe_name else 'jpg'
                unique_name = f"waste_{uuid.uuid4().hex[:12]}.{ext}"
                file_path = os.path.join(current_app.config['UPLOAD_FOLDER'], unique_name)
                file.save(file_path)
                photo_filename = unique_name

        try:
            pickup = PickupRequest(
                household_id=current_user.id,
                address_id=address_id,
                waste_category=waste_category,
                photo_filename=photo_filename,
                scheduled_date=scheduled_date,
                time_slot=time_slot,
                status='Requested',
                notes=notes
            )
            db.session.add(pickup)
            db.session.commit()

            flash(f"Pickup request #{pickup.id} for {waste_category} waste submitted successfully!", "success")
            return redirect(url_for('household.request_detail', id=pickup.id))
        except Exception:
            db.session.rollback()
            flash("Failed to submit pickup request. Please try again.", "danger")
            return render_template('household/new_request.html', addresses=addresses, today=date.today().isoformat())

    return render_template('household/new_request.html', addresses=addresses, today=date.today().isoformat())

@household_bp.route('/request/<int:id>')
@login_required
@role_required(['household', 'admin'])
def request_detail(id):
    pickup = PickupRequest.query.options(
        db.joinedload(PickupRequest.address),
        db.joinedload(PickupRequest.assignment).joinedload(WorkerAssignment.worker),
        db.joinedload(PickupRequest.assignment).joinedload(WorkerAssignment.destination_center),
        db.joinedload(PickupRequest.segregation_record).joinedload(SegregationRecord.center)
    ).filter_by(id=id).first_or_404()

    # Household can only view their own requests (unless admin)
    if current_user.role != 'admin' and pickup.household_id != current_user.id:
        flash("You are not authorized to view this request.", "danger")
        return redirect(url_for('household.dashboard'))

    # Determine progression step index (0 to 5) for visual progress bar
    status_steps = ['Requested', 'Assigned', 'Accepted', 'Collected', 'At Center', 'Segregated']
    try:
        current_step_index = status_steps.index(pickup.status)
    except ValueError:
        current_step_index = -1  # Cancelled or unknown

    return render_template(
        'household/request_detail.html',
        pickup=pickup,
        status_steps=status_steps,
        current_step_index=current_step_index
    )

@household_bp.route('/request/<int:id>/cancel', methods=['POST'])
@login_required
@role_required(['household', 'admin'])
def cancel_request(id):
    pickup = db.session.get(PickupRequest, id)
    if not pickup:
        flash("Request not found.", "danger")
        return redirect(url_for('household.dashboard'))

    if current_user.role != 'admin' and pickup.household_id != current_user.id:
        flash("Unauthorized.", "danger")
        return redirect(url_for('household.dashboard'))

    if pickup.status in ['Requested', 'Assigned']:
        pickup.status = 'Cancelled'
        if pickup.assignment:
            pickup.assignment.status = 'Cancelled'
        db.session.commit()
        flash(f"Pickup request #{id} has been cancelled.", "info")
    else:
        flash(f"Cannot cancel request in '{pickup.status}' stage.", "warning")

    return redirect(url_for('household.dashboard'))

@household_bp.route('/address/add', methods=['GET', 'POST'])
@login_required
@role_required(['household', 'admin'])
def add_address():
    if request.method == 'POST':
        title = request.form.get('title', 'Home').strip()
        address_line = request.form.get('address_line', '').strip()
        area = request.form.get('area', '').strip()
        city = request.form.get('city', 'Kolhapur').strip()
        pincode = request.form.get('pincode', '').strip()

        if not address_line or not pincode:
            flash("Address line and pincode are required.", "warning")
            return render_template('household/add_address.html')

        try:
            new_addr = Address(
                user_id=current_user.id,
                title=title or 'Home',
                address_line=address_line,
                area=area,
                city=city or 'Kolhapur',
                pincode=pincode
            )
            db.session.add(new_addr)
            db.session.commit()
            flash("New collection address saved successfully!", "success")
            return redirect(url_for('household.dashboard'))
        except Exception:
            db.session.rollback()
            flash("Failed to save address. Please try again.", "danger")
            return render_template('household/add_address.html')

    return render_template('household/add_address.html')

@household_bp.route('/rewards')
@login_required
@role_required(['household', 'admin'])
def rewards():
    coupons = Coupon.query.filter_by(is_active=True).order_by(Coupon.points_cost.asc()).all()
    transactions = RewardTransaction.query.filter_by(user_id=current_user.id)\
        .options(db.joinedload(RewardTransaction.coupon))\
        .order_by(RewardTransaction.created_at.desc()).all()

    return render_template(
        'household/rewards.html',
        coupons=coupons,
        transactions=transactions
    )

@household_bp.route('/rewards/redeem/<int:coupon_id>', methods=['POST'])
@login_required
@role_required(['household', 'admin'])
def redeem_coupon(coupon_id):
    coupon = db.session.get(Coupon, coupon_id)
    if not coupon:
        flash("Coupon not found.", "danger")
        return redirect(url_for('household.rewards'))

    if not coupon.is_active:
        flash("This coupon is no longer active.", "warning")
        return redirect(url_for('household.rewards'))

    if coupon.valid_until and coupon.valid_until < date.today():
        flash("This coupon offer has expired.", "warning")
        return redirect(url_for('household.rewards'))

    current_points = current_user.reward_points or 0
    if current_points < coupon.points_cost:
        flash(f"Insufficient reward points! You need {coupon.points_cost} points, but currently have {current_points}.", "danger")
        return redirect(url_for('household.rewards'))

    try:
        # Deduct points
        current_user.reward_points = current_points - coupon.points_cost
        
        # Record transaction
        tx = RewardTransaction(
            user_id=current_user.id,
            coupon_id=coupon.id,
            transaction_type='REDEEMED',
            points=coupon.points_cost,
            description=f"Redeemed voucher for: {coupon.title} (Code: {coupon.code})"
        )
        db.session.add(tx)
        db.session.commit()

        flash(f"🎉 Success! You redeemed '{coupon.title}'. Use promo code '{coupon.code}' at partner checkout!", "success")
    except Exception:
        db.session.rollback()
        flash("An error occurred during redemption. Points were not deducted.", "danger")

    return redirect(url_for('household.rewards'))
