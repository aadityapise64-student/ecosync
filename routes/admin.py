from datetime import datetime, date, timedelta
from flask import Blueprint, render_template, redirect, url_for, flash, request
from flask_login import login_required
from sqlalchemy import func
from models import (
    db, User, Address, PickupRequest, WorkerAssignment, 
    CollectionCenter, RecyclerOrg, SegregationRecord, 
    RecyclerDelivery, Coupon, RewardTransaction
)
from routes.auth import role_required

admin_bp = Blueprint('admin', __name__)

@admin_bp.route('/dashboard')
@login_required
@role_required(['admin'])
def dashboard():
    # Overall user metrics using SQL count
    total_households = User.query.filter_by(role='household').count()
    total_workers = User.query.filter_by(role='worker').count()
    total_centers = CollectionCenter.query.count()
    total_recyclers = RecyclerOrg.query.count()

    # Request lifecycle counts via SQL queries
    total_requests = PickupRequest.query.count()
    pending_assignment = PickupRequest.query.filter_by(status='Requested').count()
    in_transit = PickupRequest.query.filter(PickupRequest.status.in_(['Assigned', 'Accepted', 'Collected', 'At Center'])).count()
    completed_segregated = PickupRequest.query.filter_by(status='Segregated').count()

    # Waste segregation metrics via single SQL aggregation query
    waste_metrics = db.session.query(
        func.coalesce(func.sum(SegregationRecord.total_weight_kg), 0.0),
        func.coalesce(func.sum(SegregationRecord.organic_kg), 0.0),
        func.coalesce(func.sum(SegregationRecord.recyclable_plastic_kg), 0.0),
        func.coalesce(func.sum(SegregationRecord.recyclable_metal_kg), 0.0),
        func.coalesce(func.sum(SegregationRecord.recyclable_glass_kg), 0.0),
        func.coalesce(func.sum(SegregationRecord.recyclable_paper_kg), 0.0),
        func.coalesce(func.sum(SegregationRecord.hazardous_kg), 0.0),
        func.coalesce(func.sum(SegregationRecord.non_recyclable_kg), 0.0)
    ).first()

    (
        total_waste_kg, total_organic_kg, total_plastic_kg, 
        total_metal_kg, total_glass_kg, total_paper_kg, 
        total_hazardous_kg, total_non_recyclable_kg
    ) = [float(v) for v in waste_metrics]

    total_recyclable_kg = total_plastic_kg + total_metal_kg + total_glass_kg + total_paper_kg
    recovery_rate = round((total_recyclable_kg + total_organic_kg) / total_waste_kg * 100, 1) if total_waste_kg > 0 else 0.0

    # Optimized recent requests query with eager loading (limit 10)
    recent_requests = PickupRequest.query.options(
        db.joinedload(PickupRequest.household),
        db.joinedload(PickupRequest.address),
        db.joinedload(PickupRequest.assignment).joinedload(WorkerAssignment.worker)
    ).order_by(PickupRequest.created_at.desc()).limit(10).all()

    # Chart data dictionaries for Jinja template
    waste_chart_data = {
        'Organic': round(total_organic_kg, 1),
        'Plastic': round(total_plastic_kg, 1),
        'Metal': round(total_metal_kg, 1),
        'Glass': round(total_glass_kg, 1),
        'Paper': round(total_paper_kg, 1),
        'Hazardous': round(total_hazardous_kg, 1),
        'Non-Recyclable': round(total_non_recyclable_kg, 1)
    }

    status_chart_data = {
        'Requested': pending_assignment,
        'In Transit': in_transit,
        'Segregated': completed_segregated
    }

    return render_template(
        'admin/dashboard.html',
        total_households=total_households,
        total_workers=total_workers,
        total_centers=total_centers,
        total_recyclers=total_recyclers,
        total_requests=total_requests,
        pending_assignment=pending_assignment,
        in_transit=in_transit,
        completed_segregated=completed_segregated,
        total_waste_kg=round(total_waste_kg, 1),
        total_recyclable_kg=round(total_recyclable_kg, 1),
        recovery_rate=recovery_rate,
        recent_requests=recent_requests,
        waste_chart_data=waste_chart_data,
        status_chart_data=status_chart_data
    )

@admin_bp.route('/assign', methods=['GET', 'POST'])
@login_required
@role_required(['admin'])
def assign_workers():
    if request.method == 'POST':
        request_id = request.form.get('request_id', type=int)
        worker_id = request.form.get('worker_id', type=int)
        center_id = request.form.get('center_id', type=int)

        if not request_id or not worker_id or not center_id:
            flash("Please select request, worker, and drop-off center.", "warning")
            return redirect(url_for('admin.assign_workers'))

        pickup = db.session.get(PickupRequest, request_id)
        worker = User.query.filter_by(id=worker_id, role='worker').first()
        center = db.session.get(CollectionCenter, center_id)

        if not pickup or not worker or not center:
            flash("Invalid selection. Request, worker, or center was not found.", "danger")
            return redirect(url_for('admin.assign_workers'))

        if pickup.status in ['Segregated', 'Cancelled']:
            flash(f"Cannot assign a request that is already {pickup.status}.", "warning")
            return redirect(url_for('admin.assign_workers'))

        try:
            # Check if assignment already exists
            if pickup.assignment:
                pickup.assignment.worker_id = worker.id
                pickup.assignment.center_id = center.id
                pickup.assignment.status = 'Assigned'
            else:
                assignment = WorkerAssignment(
                    request_id=pickup.id,
                    worker_id=worker.id,
                    center_id=center.id,
                    status='Assigned'
                )
                db.session.add(assignment)

            pickup.status = 'Assigned'
            db.session.commit()

            flash(f"Pickup Request #{pickup.id} successfully assigned to worker {worker.name} with dropoff at {center.center_name}!", "success")
        except Exception:
            db.session.rollback()
            flash("Failed to assign worker. Please try again.", "danger")

        return redirect(url_for('admin.assign_workers'))

    # Requests awaiting assignment with eager loading
    unassigned_requests = PickupRequest.query.options(
        db.joinedload(PickupRequest.household),
        db.joinedload(PickupRequest.address)
    ).filter_by(status='Requested').order_by(PickupRequest.created_at.asc()).all()

    workers = User.query.filter_by(role='worker').order_by(User.name.asc()).all()
    centers = CollectionCenter.query.order_by(CollectionCenter.center_name.asc()).all()

    return render_template(
        'admin/assign.html',
        unassigned_requests=unassigned_requests,
        workers=workers,
        centers=centers
    )

@admin_bp.route('/coupons', methods=['GET', 'POST'])
@login_required
@role_required(['admin'])
def manage_coupons():
    if request.method == 'POST':
        code = request.form.get('code', '').strip().upper()
        title = request.form.get('title', '').strip()
        description = request.form.get('description', '').strip()
        points_cost = request.form.get('points_cost', type=int)
        discount_value = request.form.get('discount_value', '').strip()
        partner_name = request.form.get('partner_name', '').strip()
        category = request.form.get('category', 'Grocery').strip()
        valid_until_str = request.form.get('valid_until')

        if not code or not title or not points_cost or not valid_until_str:
            flash("Please fill all required coupon fields.", "warning")
            return redirect(url_for('admin.manage_coupons'))

        if points_cost <= 0:
            flash("Points cost must be a positive integer.", "warning")
            return redirect(url_for('admin.manage_coupons'))

        if Coupon.query.filter_by(code=code).first():
            flash(f"Coupon code '{code}' already exists.", "warning")
            return redirect(url_for('admin.manage_coupons'))

        try:
            valid_until = datetime.strptime(valid_until_str, '%Y-%m-%d').date()
        except ValueError:
            flash("Invalid expiry date format.", "warning")
            return redirect(url_for('admin.manage_coupons'))

        try:
            new_coupon = Coupon(
                code=code,
                title=title,
                description=description,
                points_cost=points_cost,
                discount_value=discount_value,
                partner_name=partner_name,
                category=category,
                valid_until=valid_until,
                is_active=True
            )
            db.session.add(new_coupon)
            db.session.commit()
            flash(f"Coupon '{code}' added to marketplace catalog!", "success")
        except Exception:
            db.session.rollback()
            flash("Failed to add coupon. Please try again.", "danger")

        return redirect(url_for('admin.manage_coupons'))

    coupons = Coupon.query.order_by(Coupon.created_at.desc()).all()
    return render_template('admin/coupons.html', coupons=coupons, default_expiry=(date.today() + timedelta(days=90)).isoformat())

@admin_bp.route('/coupons/<int:id>/toggle', methods=['POST'])
@login_required
@role_required(['admin'])
def toggle_coupon(id):
    coupon = db.session.get(Coupon, id)
    if not coupon:
        flash("Coupon not found.", "danger")
        return redirect(url_for('admin.manage_coupons'))

    try:
        coupon.is_active = not coupon.is_active
        db.session.commit()
        flash(f"Coupon '{coupon.code}' status changed to {'Active' if coupon.is_active else 'Inactive'}.", "info")
    except Exception:
        db.session.rollback()
        flash("Failed to toggle coupon status.", "danger")

    return redirect(url_for('admin.manage_coupons'))

@admin_bp.route('/users')
@login_required
@role_required(['admin'])
def view_users():
    users = User.query.order_by(User.created_at.desc()).all()
    return render_template('admin/users.html', users=users)
