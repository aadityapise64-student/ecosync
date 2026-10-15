from flask import Blueprint, render_template, redirect, url_for, flash, request
from flask_login import login_required, current_user
from models import db, PickupRequest, WorkerAssignment, CollectionCenter
from routes.auth import role_required

worker_bp = Blueprint('worker', __name__)

@worker_bp.route('/dashboard')
@login_required
@role_required(['worker', 'admin'])
def dashboard():
    # Fetch assignments for current worker with eager-loaded relationships to prevent N+1 queries
    assignments_query = WorkerAssignment.query\
        .options(
            db.joinedload(WorkerAssignment.request).joinedload(PickupRequest.household),
            db.joinedload(WorkerAssignment.request).joinedload(PickupRequest.address),
            db.joinedload(WorkerAssignment.destination_center)
        )
    if current_user.role != 'admin':
        assignments_query = assignments_query.filter_by(worker_id=current_user.id)
    assignments = assignments_query.order_by(WorkerAssignment.assigned_at.desc()).all()
    
    # Active tasks (Assigned, Accepted, Collected)
    active_assignments = [a for a in assignments if a.request and a.request.status in ['Assigned', 'Accepted', 'Collected']]
    delivered_assignments = [a for a in assignments if a.request and a.request.status in ['At Center', 'Segregated']]
    
    # Fetch all collection centers so worker can pick where to drop off waste
    centers = CollectionCenter.query.order_by(CollectionCenter.center_name.asc()).all()

    return render_template(
        'worker/dashboard.html',
        active_assignments=active_assignments,
        delivered_assignments=delivered_assignments,
        centers=centers
    )

@worker_bp.route('/task/<int:request_id>/accept', methods=['POST'])
@login_required
@role_required(['worker', 'admin'])
def accept_task(request_id):
    assignment = WorkerAssignment.query.filter_by(request_id=request_id).with_for_update().first_or_404()
    if current_user.role != 'admin' and assignment.worker_id != current_user.id:
        flash("Unauthorized task.", "danger")
        return redirect(url_for('worker.dashboard'))

    if not assignment.request or assignment.request.status == 'Cancelled':
        flash("This pickup request has been cancelled.", "warning")
        return redirect(url_for('worker.dashboard'))
    if assignment.request.status != 'Assigned':
        flash("This task is not ready to be accepted.", "warning")
        return redirect(url_for('worker.dashboard'))

    try:
        # Transition to Accepted
        assignment.status = 'Accepted'
        assignment.request.status = 'Accepted'
        db.session.commit()
        flash(f"Pickup task #{request_id} accepted. You can now proceed to the household.", "success")
    except Exception:
        db.session.rollback()
        flash("Failed to accept task. Please try again.", "danger")

    return redirect(url_for('worker.dashboard'))

@worker_bp.route('/task/<int:request_id>/collect', methods=['POST'])
@login_required
@role_required(['worker', 'admin'])
def collect_task(request_id):
    assignment = WorkerAssignment.query.filter_by(request_id=request_id).with_for_update().first_or_404()
    if current_user.role != 'admin' and assignment.worker_id != current_user.id:
        flash("Unauthorized task.", "danger")
        return redirect(url_for('worker.dashboard'))

    if not assignment.request or assignment.request.status == 'Cancelled':
        flash("This pickup request has been cancelled.", "warning")
        return redirect(url_for('worker.dashboard'))
    if assignment.request.status != 'Accepted':
        flash("Only an accepted task can be marked collected.", "warning")
        return redirect(url_for('worker.dashboard'))

    try:
        # Transition to Collected
        assignment.status = 'In Progress'
        assignment.request.status = 'Collected'
        db.session.commit()
        flash(f"Waste from household collected successfully for Request #{request_id}. Please transport to a designated Collection Center.", "success")
    except Exception:
        db.session.rollback()
        flash("Failed to update status. Please try again.", "danger")

    return redirect(url_for('worker.dashboard'))

@worker_bp.route('/task/<int:request_id>/deliver', methods=['POST'])
@login_required
@role_required(['worker', 'admin'])
def deliver_to_center(request_id):
    assignment = WorkerAssignment.query.filter_by(request_id=request_id).with_for_update().first_or_404()
    if current_user.role != 'admin' and assignment.worker_id != current_user.id:
        flash("Unauthorized task.", "danger")
        return redirect(url_for('worker.dashboard'))

    if not assignment.request or assignment.request.status != 'Collected':
        flash("Only a collected task can be delivered to a center.", "warning")
        return redirect(url_for('worker.dashboard'))

    center_id = request.form.get('center_id', type=int)
    if not center_id:
        flash("Please select a valid collection center destination.", "warning")
        return redirect(url_for('worker.dashboard'))

    center = db.session.get(CollectionCenter, center_id)
    if not center:
        flash("Selected collection center does not exist.", "warning")
        return redirect(url_for('worker.dashboard'))
    if current_user.role != 'admin' and assignment.center_id and assignment.center_id != center.id:
        flash("This task is assigned to a different collection center.", "danger")
        return redirect(url_for('worker.dashboard'))

    try:
        assignment.center_id = center.id
        assignment.status = 'Delivered'
        if assignment.request:
            assignment.request.status = 'At Center'
        db.session.commit()
        flash(f"Waste for Request #{request_id} successfully delivered to {center.center_name} for segregation.", "success")
    except Exception:
        db.session.rollback()
        flash("Failed to record delivery. Please try again.", "danger")

    return redirect(url_for('worker.dashboard'))
