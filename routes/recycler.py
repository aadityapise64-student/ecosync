from datetime import datetime, timezone
from flask import Blueprint, render_template, redirect, url_for, flash, request
from flask_login import login_required, current_user
from models import db, RecyclerOrg, RecyclerDelivery
from routes.auth import role_required

recycler_bp = Blueprint('recycler', __name__)

def get_current_recycler():
    """Return the logged-in recycler's organization; admins may inspect the first."""
    if current_user.recycler_profile:
        return current_user.recycler_profile
    if current_user.role == 'admin':
        return RecyclerOrg.query.first()
    return None

@recycler_bp.route('/dashboard')
@login_required
@role_required(['recycler', 'admin'])
def dashboard():
    recycler = get_current_recycler()
    if not recycler:
        flash("No recycler organization profile registered.", "warning")
        return redirect(url_for('index'))

    # Deliveries routed to this recycler with eager-loaded center
    deliveries = RecyclerDelivery.query.filter_by(recycler_id=recycler.id)\
        .options(db.joinedload(RecyclerDelivery.center))\
        .order_by(RecyclerDelivery.dispatched_at.desc()).all()

    pending_deliveries = [d for d in deliveries if d.status == 'Dispatched']
    received_deliveries = [d for d in deliveries if d.status in ['Received', 'Processed']]
    
    total_recovered_kg = sum(d.total_weight_kg for d in received_deliveries)

    return render_template(
        'recycler/dashboard.html',
        recycler=recycler,
        pending_deliveries=pending_deliveries,
        received_deliveries=received_deliveries,
        total_recovered_kg=round(total_recovered_kg, 2)
    )

@recycler_bp.route('/delivery/<int:delivery_id>/receive', methods=['POST'])
@login_required
@role_required(['recycler', 'admin'])
def receive_delivery(delivery_id):
    delivery = RecyclerDelivery.query.get_or_404(delivery_id)
    recycler = get_current_recycler()

    if current_user.role != 'admin' and (not recycler or delivery.recycler_id != recycler.id):
        flash("Unauthorized access to this delivery batch.", "danger")
        return redirect(url_for('recycler.dashboard'))

    if delivery.status != 'Dispatched':
        flash("Only dispatched deliveries can be received.", "warning")
        return redirect(url_for('recycler.dashboard'))

    notes = request.form.get('notes', 'Verified and weighed at recycling intake.').strip()

    try:
        delivery.status = 'Received'
        delivery.received_at = datetime.now(timezone.utc)
        delivery.acknowledgment_notes = notes
        db.session.commit()
        flash(f"Material delivery #{delivery.id} ({delivery.total_weight_kg} kg) marked as Received and verified.", "success")
    except Exception:
        db.session.rollback()
        flash("Failed to update delivery status. Please try again.", "danger")

    return redirect(url_for('recycler.dashboard'))

@recycler_bp.route('/delivery/<int:delivery_id>/process', methods=['POST'])
@login_required
@role_required(['recycler', 'admin'])
def process_delivery(delivery_id):
    delivery = RecyclerDelivery.query.get_or_404(delivery_id)
    recycler = get_current_recycler()

    if current_user.role != 'admin' and (not recycler or delivery.recycler_id != recycler.id):
        flash("Unauthorized access to this delivery batch.", "danger")
        return redirect(url_for('recycler.dashboard'))

    if delivery.status != 'Received':
        flash("A delivery must be received before it can be processed.", "warning")
        return redirect(url_for('recycler.dashboard'))

    try:
        delivery.status = 'Processed'
        db.session.commit()
        flash(f"Delivery #{delivery.id} processed into secondary recycled raw materials!", "success")
    except Exception:
        db.session.rollback()
        flash("Failed to mark delivery as processed. Please try again.", "danger")

    return redirect(url_for('recycler.dashboard'))
