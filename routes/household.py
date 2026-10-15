import os
import uuid
import json
import base64
import io
import secrets
import mimetypes
from datetime import datetime, date, timezone
from flask import Blueprint, render_template, redirect, url_for, flash, request, current_app, jsonify, abort
from flask_login import login_required, current_user
from werkzeug.utils import secure_filename
from models import db, User, Address, PickupRequest, WorkerAssignment, SegregationRecord, Coupon, RewardTransaction, AIScan
from routes.auth import role_required
from services.ai_detector import detect_trash_from_file
from services.media_storage import (
    MediaStorageError,
    delete_file as delete_stored_file,
    persist_file,
    remove_local_file,
    supabase_storage_enabled,
)

household_bp = Blueprint('household', __name__)

def allowed_file(filename: str) -> bool:
    return '.' in filename and \
           filename.rsplit('.', 1)[1].lower() in current_app.config['ALLOWED_EXTENSIONS']

def valid_image_upload(file) -> bool:
    """Check that uploaded bytes decode as a supported raster image."""
    from PIL import Image, UnidentifiedImageError
    try:
        file.stream.seek(0)
        with Image.open(file.stream) as image:
            max_pixels = current_app.config.get('MAX_IMAGE_PIXELS', 20_000_000)
            if image.width * image.height > max_pixels:
                file.stream.seek(0)
                return False
            image.verify()
        file.stream.seek(0)
        return True
    except (UnidentifiedImageError, OSError, ValueError):
        file.stream.seek(0)
        return False

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
        if not address or address.user_id != current_user.id:
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

        # Photo upload and AI Trash Classification handling
        photo_filename = None
        ai_detected_items = None
        ai_total_items = 0
        ai_points_awarded = 0
        ai_annotated_photo = None
        ai_summary = None

        if 'photo' in request.files:
            file = request.files['photo']
            if file and file.filename and allowed_file(file.filename) and valid_image_upload(file):
                safe_name = secure_filename(file.filename)
                ext = safe_name.rsplit('.', 1)[1].lower() if '.' in safe_name else 'jpg'
                unique_name = f"waste_{uuid.uuid4().hex[:12]}.{ext}"
                file_path = os.path.join(current_app.config['UPLOAD_FOLDER'], unique_name)
                file.save(file_path)
                photo_filename = unique_name

                # Run Roboflow AI trash detection & reward scoring
                try:
                    ai_result = detect_trash_from_file(file_path, save_annotated=True)
                    if ai_result.get('success') and not ai_result.get('is_simulated'):
                        ai_total_items = ai_result.get('total_items', 0)
                        ai_points_awarded = ai_result.get('points_awarded', 0)
                        ai_detected_items = json.dumps(ai_result.get('class_counts', {}))
                        ai_annotated_photo = ai_result.get('annotated_image')
                        ai_summary = ai_result.get('summary')
                except Exception as e:
                    current_app.logger.error(f"AI trash classification failed: {e}")

        stored_media = []
        try:
            if supabase_storage_enabled() and photo_filename:
                persist_file(file_path, photo_filename, mimetypes.guess_type(photo_filename)[0])
                stored_media.append(photo_filename)
                if ai_annotated_photo:
                    annotated_path = os.path.join(current_app.config['UPLOAD_FOLDER'], ai_annotated_photo)
                    if os.path.isfile(annotated_path):
                        persist_file(annotated_path, ai_annotated_photo, mimetypes.guess_type(ai_annotated_photo)[0])
                        stored_media.append(ai_annotated_photo)

            pickup = PickupRequest(
                household_id=current_user.id,
                address_id=address_id,
                waste_category=waste_category,
                photo_filename=photo_filename,
                scheduled_date=scheduled_date,
                time_slot=time_slot,
                status='Requested',
                notes=notes,
                ai_detected_items=ai_detected_items,
                ai_total_items=ai_total_items,
                ai_points_awarded=ai_points_awarded,
                ai_annotated_photo=ai_annotated_photo,
                ai_summary=ai_summary
            )
            db.session.add(pickup)

            # Award reward points immediately to household if recyclable trash detected
            if ai_points_awarded > 0:
                current_user.reward_points = (current_user.reward_points or 0) + ai_points_awarded
                tx = RewardTransaction(
                    user_id=current_user.id,
                    transaction_type='EARNED',
                    points=ai_points_awarded,
                    description=f"AI Trash Detection Reward for Request #{pickup.id if pickup.id else 'New'}: {ai_summary}"
                )
                db.session.add(tx)

            db.session.commit()

            if supabase_storage_enabled():
                if photo_filename:
                    remove_local_file(file_path)
                if ai_annotated_photo:
                    remove_local_file(os.path.join(current_app.config['UPLOAD_FOLDER'], ai_annotated_photo))

            # Update transaction description with pickup id if needed
            if ai_points_awarded > 0:
                flash(
                    f"🎉 Pickup request #{pickup.id} submitted! "
                    f"AI Model detected {ai_total_items} items ({ai_summary}). "
                    f"You earned +{ai_points_awarded} EcoPoints!",
                    "success"
                )
            else:
                flash(f"Pickup request #{pickup.id} for {waste_category} waste submitted successfully!", "success")

            return redirect(url_for('household.request_detail', id=pickup.id))
        except Exception as e:
            db.session.rollback()
            for stored_filename in stored_media:
                delete_stored_file(stored_filename)
            if supabase_storage_enabled() and photo_filename:
                remove_local_file(file_path)
                if ai_annotated_photo:
                    remove_local_file(os.path.join(current_app.config['UPLOAD_FOLDER'], ai_annotated_photo))
            current_app.logger.error(f"Error creating pickup request: {e}")
            flash("Failed to submit pickup request. Please try again.", "danger")
            return render_template('household/new_request.html', addresses=addresses, today=date.today().isoformat())

    return render_template('household/new_request.html', addresses=addresses, today=date.today().isoformat())

@household_bp.route('/ai-scan-preview', methods=['POST'])
@login_required
@role_required(['household', 'admin'])
def ai_scan_preview():
    """AJAX endpoint for instant AI trash detection preview on photo upload."""
    if 'photo' not in request.files:
        return jsonify({'success': False, 'message': 'No image file uploaded.'}), 400

    file = request.files['photo']
    if not file or not file.filename or not allowed_file(file.filename) or not valid_image_upload(file):
        return jsonify({'success': False, 'message': 'Invalid file format. Please upload JPG, PNG, or WEBP.'}), 400

    safe_name = secure_filename(file.filename)
    ext = safe_name.rsplit('.', 1)[1].lower() if '.' in safe_name else 'jpg'
    unique_name = f"preview_{uuid.uuid4().hex[:12]}.{ext}"
    file_path = os.path.join(current_app.config['UPLOAD_FOLDER'], unique_name)
    file.save(file_path)

    try:
        result = detect_trash_from_file(file_path, save_annotated=True)
        if not result.get('success'):
            if os.path.isfile(file_path):
                os.remove(file_path)
            return jsonify({'success': False, 'result': result, 'message': result.get('message')}), 503
        annotated = result.get('annotated_image') or unique_name
        annotated_path = os.path.join(current_app.config['UPLOAD_FOLDER'], annotated)
        original_path = file_path
        preview_path = annotated_path if os.path.isfile(annotated_path) else original_path
        from PIL import Image
        with Image.open(preview_path) as preview_image:
            preview_image = preview_image.convert('RGB')
            preview_image.thumbnail((1200, 1200))
            output = io.BytesIO()
            preview_image.save(output, format='JPEG', quality=82, optimize=True)
            preview_bytes = output.getvalue()
        result['annotated_image_url'] = 'data:image/jpeg;base64,' + base64.b64encode(preview_bytes).decode('ascii')
        if annotated != unique_name and os.path.isfile(annotated_path):
            os.remove(annotated_path)
        if os.path.isfile(original_path):
            os.remove(original_path)
        result['original_image_url'] = None
        return jsonify({'success': True, 'result': result})
    except Exception:
        current_app.logger.exception("AI scanner preview failed while processing an uploaded image.")
        return jsonify({'success': False, 'message': 'The scan could not be completed. Check the server logs for the error details.'}), 503

@household_bp.route('/ai-scanner', methods=['GET', 'POST'])
@login_required
@role_required(['household', 'admin'])
def ai_scanner():
    """Dedicated AI Trash Scanner & Rewards Page."""
    addresses = Address.query.filter_by(user_id=current_user.id).all()
    if request.method == 'POST':
        if request.form.get('claim_points'):
            flash("Please run a new live scan before claiming points.", "warning")
            return redirect(url_for('household.ai_scanner'))

        if 'photo' not in request.files:
            flash("Please choose an image of trash to scan.", "warning")
            return render_template('household/ai_scanner.html', addresses=addresses, result=None)

        file = request.files['photo']
        if not file or not file.filename or not allowed_file(file.filename) or not valid_image_upload(file):
            flash("Invalid file format. Allowed formats: PNG, JPG, JPEG, WEBP.", "warning")
            return render_template('household/ai_scanner.html', addresses=addresses, result=None)

        safe_name = secure_filename(file.filename)
        ext = safe_name.rsplit('.', 1)[1].lower() if '.' in safe_name else 'jpg'
        unique_name = f"scan_{uuid.uuid4().hex[:12]}.{ext}"
        file_path = os.path.join(current_app.config['UPLOAD_FOLDER'], unique_name)
        file.save(file_path)

        try:
            result = detect_trash_from_file(file_path, save_annotated=True)
        except Exception:
            current_app.logger.exception("AI scanner failed while processing an uploaded image.")
            if supabase_storage_enabled():
                remove_local_file(file_path)
            flash("The scanner could not process that image. Check the server logs for details and try again.", "danger")
            return render_template('household/ai_scanner.html', addresses=addresses, result=None, today=date.today().isoformat()), 503
        result['original_filename'] = unique_name

        stored_media = []
        try:
            if supabase_storage_enabled():
                persist_file(file_path, unique_name, mimetypes.guess_type(unique_name)[0])
                stored_media.append(unique_name)
                annotated_filename = result.get('annotated_image')
                if annotated_filename:
                    annotated_path = os.path.join(current_app.config['UPLOAD_FOLDER'], annotated_filename)
                    if os.path.isfile(annotated_path):
                        persist_file(annotated_path, annotated_filename, mimetypes.guess_type(annotated_filename)[0])
                        stored_media.append(annotated_filename)
        except MediaStorageError:
            current_app.logger.warning('AI scan media could not be saved to configured private storage.')
            for stored_filename in stored_media:
                delete_stored_file(stored_filename)
            remove_local_file(file_path)
            if result.get('annotated_image'):
                remove_local_file(os.path.join(current_app.config['UPLOAD_FOLDER'], result['annotated_image']))
            flash("Image storage is not available right now. Please try again.", "danger")
            return render_template('household/ai_scanner.html', addresses=addresses, result=None, today=date.today().isoformat()), 503

        status = 'live' if result.get('success') and not result.get('is_simulated') else (
            'demo' if result.get('is_simulated') else 'failed'
        )
        scan = AIScan(
            user_id=current_user.id,
            original_filename=unique_name,
            annotated_filename=result.get('annotated_image'),
            status=status,
            model_version=current_app.config.get('ROBOFLOW_WORKFLOW_ID'),
            predictions_json=json.dumps(result.get('predictions', [])),
            class_counts_json=json.dumps(result.get('class_counts', {})),
            points_awarded=result.get('points_awarded', 0) if status == 'live' else 0
        )
        try:
            db.session.add(scan)
            db.session.commit()
        except Exception:
            db.session.rollback()
            for stored_filename in stored_media:
                delete_stored_file(stored_filename)
            if supabase_storage_enabled():
                remove_local_file(file_path)
                if result.get('annotated_image'):
                    remove_local_file(os.path.join(current_app.config['UPLOAD_FOLDER'], result['annotated_image']))
            current_app.logger.exception('Could not save AI scan record.')
            flash("The scan could not be saved. Please try again.", "danger")
            return render_template('household/ai_scanner.html', addresses=addresses, result=None, today=date.today().isoformat()), 503

        if supabase_storage_enabled():
            remove_local_file(file_path)
            if result.get('annotated_image'):
                remove_local_file(os.path.join(current_app.config['UPLOAD_FOLDER'], result['annotated_image']))
        result['scan_id'] = scan.id
        result['points_claimed'] = False

        return render_template(
            'household/ai_scanner.html',
            addresses=addresses,
            result=result,
            uploaded_photo=unique_name,
            today=date.today().isoformat()
        )

    return render_template('household/ai_scanner.html', addresses=addresses, result=None, today=date.today().isoformat())


@household_bp.route('/ai-scan/<int:scan_id>/claim', methods=['POST'])
@login_required
@role_required(['household'])
def claim_ai_scan(scan_id):
    scan = AIScan.query.filter_by(id=scan_id, user_id=current_user.id).with_for_update().first_or_404()
    if scan.status != 'live' or scan.points_awarded <= 0:
        flash("Only a successful live scan can award points.", "warning")
        return redirect(url_for('household.ai_scanner'))
    if scan.claimed_at is not None:
        flash("Points for this scan have already been claimed.", "info")
        return redirect(url_for('household.ai_scanner'))

    try:
        scan.claimed_at = datetime.now(timezone.utc)
        current_user.reward_points = (current_user.reward_points or 0) + scan.points_awarded
        db.session.add(RewardTransaction(
            user_id=current_user.id,
            ai_scan_id=scan.id,
            transaction_type='EARNED',
            points=scan.points_awarded,
            description=f"AI scanner reward for scan #{scan.id}"
        ))
        db.session.commit()
        flash(f"You claimed {scan.points_awarded} EcoPoints for scan #{scan.id}.", "success")
    except Exception:
        db.session.rollback()
        current_app.logger.exception("AI scan reward claim failed.")
        flash("Could not claim points. Please refresh and check your balance.", "danger")
    return redirect(url_for('household.ai_scanner'))

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
        abort(404)

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
        abort(404)

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

    redemption_tokens = {coupon.id: secrets.token_urlsafe(32) for coupon in coupons}
    return render_template(
        'household/rewards.html',
        coupons=coupons,
        transactions=transactions,
        redemption_tokens=redemption_tokens
    )

@household_bp.route('/rewards/redeem/<int:coupon_id>', methods=['POST'])
@login_required
@role_required(['household', 'admin'])
def redeem_coupon(coupon_id):
    idempotency_key = request.form.get('idempotency_key', '').strip()
    if not idempotency_key or len(idempotency_key) > 96:
        flash("Please reload the rewards page and try again.", "warning")
        return redirect(url_for('household.rewards'))

    previous = RewardTransaction.query.filter_by(idempotency_key=idempotency_key).first()
    if previous:
        flash("This redemption was already processed.", "info")
        return redirect(url_for('household.rewards'))

    coupon = Coupon.query.filter_by(id=coupon_id).with_for_update().first()
    if not coupon:
        flash("Coupon not found.", "danger")
        return redirect(url_for('household.rewards'))

    if not coupon.is_active:
        flash("This coupon is no longer active.", "warning")
        return redirect(url_for('household.rewards'))

    if coupon.valid_until and coupon.valid_until < date.today():
        flash("This coupon offer has expired.", "warning")
        return redirect(url_for('household.rewards'))

    locked_user = User.query.filter_by(id=current_user.id).with_for_update().first()
    current_points = locked_user.reward_points or 0
    if coupon.points_cost <= 0 or current_points < coupon.points_cost:
        flash(f"Insufficient reward points! You need {coupon.points_cost} points, but currently have {current_points}.", "danger")
        return redirect(url_for('household.rewards'))

    try:
        # Deduct points
        locked_user.reward_points = current_points - coupon.points_cost
        
        # Record transaction
        tx = RewardTransaction(
            user_id=current_user.id,
            coupon_id=coupon.id,
            idempotency_key=idempotency_key,
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
