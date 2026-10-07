from flask import Blueprint, render_template, redirect, url_for, flash, request
from flask_login import login_required, current_user
from sqlalchemy import func
from models import (
    db, PickupRequest, WorkerAssignment, CollectionCenter, 
    SegregationRecord, RecyclerOrg, RecyclerDelivery, RewardTransaction
)
from routes.auth import role_required

center_bp = Blueprint('center', __name__)

def get_current_center():
    """Retrieve the collection center linked to the logged in center user or default center"""
    if current_user.center_profile:
        return current_user.center_profile
    return CollectionCenter.query.first()

@center_bp.route('/dashboard')
@login_required
@role_required(['center', 'admin'])
def dashboard():
    center = get_current_center()
    if not center:
        flash("No collection center facility registered yet.", "warning")
        return redirect(url_for('index'))

    # Incoming requests: delivered to this center or unassigned destination, awaiting segregation
    incoming_requests = PickupRequest.query\
        .join(WorkerAssignment, PickupRequest.id == WorkerAssignment.request_id, isouter=True)\
        .options(
            db.joinedload(PickupRequest.household),
            db.joinedload(PickupRequest.address),
            db.joinedload(PickupRequest.assignment).joinedload(WorkerAssignment.worker)
        )\
        .filter(PickupRequest.status == 'At Center')\
        .filter((WorkerAssignment.center_id == center.id) | (WorkerAssignment.center_id.is_(None)))\
        .order_by(PickupRequest.created_at.desc()).all()

    # Past segregation records at this center
    records = SegregationRecord.query.filter_by(center_id=center.id)\
        .options(db.joinedload(SegregationRecord.request))\
        .order_by(SegregationRecord.recorded_at.desc()).all()

    # Aggregate statistics via database aggregations
    metrics = db.session.query(
        func.coalesce(func.sum(SegregationRecord.total_weight_kg), 0.0),
        func.coalesce(func.sum(
            SegregationRecord.recyclable_plastic_kg + 
            SegregationRecord.recyclable_metal_kg + 
            SegregationRecord.recyclable_glass_kg + 
            SegregationRecord.recyclable_paper_kg
        ), 0.0),
        func.coalesce(func.sum(SegregationRecord.organic_kg), 0.0),
        func.coalesce(func.sum(SegregationRecord.points_awarded), 0)
    ).filter(SegregationRecord.center_id == center.id).first()

    total_processed_kg, total_recyclable_kg, total_organic_kg, total_points_distributed = metrics

    return render_template(
        'center/dashboard.html',
        center=center,
        incoming_requests=incoming_requests,
        records=records,
        total_processed_kg=round(float(total_processed_kg), 2),
        total_recyclable_kg=round(float(total_recyclable_kg), 2),
        total_organic_kg=round(float(total_organic_kg), 2),
        total_points_distributed=int(total_points_distributed)
    )

@center_bp.route('/segregate/<int:request_id>', methods=['GET', 'POST'])
@login_required
@role_required(['center', 'admin'])
def segregate(request_id):
    center = get_current_center()
    if not center:
        flash("No collection center facility registered.", "warning")
        return redirect(url_for('center.dashboard'))

    pickup = PickupRequest.query.options(
        db.joinedload(PickupRequest.household),
        db.joinedload(PickupRequest.address)
    ).filter_by(id=request_id).first_or_404()
    
    recyclers = RecyclerOrg.query.order_by(RecyclerOrg.org_name.asc()).all()

    if pickup.status != 'At Center':
        flash(f"Request #{request_id} is in status '{pickup.status}'. Only waste marked 'At Center' can be segregated.", "warning")
        return redirect(url_for('center.dashboard'))

    if request.method == 'POST':
        try:
            organic_kg = max(0.0, float(request.form.get('organic_kg', 0.0) or 0.0))
            plastic_kg = max(0.0, float(request.form.get('plastic_kg', 0.0) or 0.0))
            metal_kg = max(0.0, float(request.form.get('metal_kg', 0.0) or 0.0))
            glass_kg = max(0.0, float(request.form.get('glass_kg', 0.0) or 0.0))
            paper_kg = max(0.0, float(request.form.get('paper_kg', 0.0) or 0.0))
            non_recyclable_kg = max(0.0, float(request.form.get('non_recyclable_kg', 0.0) or 0.0))
            hazardous_kg = max(0.0, float(request.form.get('hazardous_kg', 0.0) or 0.0))
        except (ValueError, TypeError):
            flash("Please enter valid numeric weights.", "warning")
            return render_template('center/segregate.html', pickup=pickup, center=center, recyclers=recyclers)
        
        routed_to = request.form.get('routed_to', 'Recycler Org').strip()
        recycler_id = request.form.get('recycler_id', type=int)
        remarks = request.form.get('remarks', '').strip()

        total_weight = (organic_kg + plastic_kg + metal_kg + glass_kg + 
                        paper_kg + non_recyclable_kg + hazardous_kg)

        if total_weight <= 0:
            flash("Please enter valid positive weights for at least one segregated waste stream.", "warning")
            return render_template('center/segregate.html', pickup=pickup, center=center, recyclers=recyclers)

        # Automated Reward Points Algorithm
        # 10 pts/kg for plastic & metal
        # 6 pts/kg for paper & glass
        # 4 pts/kg for organic
        # 15 pts/kg for hazardous (safe e-waste/battery diversion)
        # 1 pt/kg for segregated non-recyclable residual
        points = int(
            (plastic_kg * 10) +
            (metal_kg * 10) +
            (paper_kg * 6) +
            (glass_kg * 6) +
            (organic_kg * 4) +
            (hazardous_kg * 15) +
            (non_recyclable_kg * 1)
        )
        if points < 5:
            points = 5  # minimum participation reward

        try:
            # Create Segregation Record
            seg_record = SegregationRecord(
                request_id=pickup.id,
                center_id=center.id,
                organic_kg=round(organic_kg, 2),
                recyclable_plastic_kg=round(plastic_kg, 2),
                recyclable_metal_kg=round(metal_kg, 2),
                recyclable_glass_kg=round(glass_kg, 2),
                recyclable_paper_kg=round(paper_kg, 2),
                non_recyclable_kg=round(non_recyclable_kg, 2),
                hazardous_kg=round(hazardous_kg, 2),
                total_weight_kg=round(total_weight, 2),
                points_awarded=points,
                routed_to=routed_to,
                remarks=remarks
            )
            db.session.add(seg_record)
            db.session.flush()

            # Update Request Status to Segregated
            pickup.status = 'Segregated'

            # Credit Points to Household Account
            household = pickup.household
            household.reward_points = (household.reward_points or 0) + points

            # Log Reward Transaction
            tx = RewardTransaction(
                user_id=household.id,
                coupon_id=None,
                transaction_type='EARNED',
                points=points,
                description=f"Reward for {round(total_weight, 1)} kg segregated waste (Pickup #{pickup.id})"
            )
            db.session.add(tx)

            # If routed to Recycler Org, create RecyclerDelivery dispatch
            total_recyclable = plastic_kg + metal_kg + glass_kg + paper_kg
            if routed_to == 'Recycler Org' and total_recyclable > 0:
                recycler = db.session.get(RecyclerOrg, recycler_id) if recycler_id else RecyclerOrg.query.first()
                if recycler:
                    summary_parts = []
                    if plastic_kg > 0: summary_parts.append(f"{round(plastic_kg, 1)}kg Plastic")
                    if metal_kg > 0: summary_parts.append(f"{round(metal_kg, 1)}kg Metal")
                    if paper_kg > 0: summary_parts.append(f"{round(paper_kg, 1)}kg Paper")
                    if glass_kg > 0: summary_parts.append(f"{round(glass_kg, 1)}kg Glass")
                    
                    delivery = RecyclerDelivery(
                        segregation_id=seg_record.id,
                        center_id=center.id,
                        recycler_id=recycler.id,
                        materials_summary=", ".join(summary_parts),
                        total_weight_kg=round(total_recyclable, 2),
                        status='Dispatched'
                    )
                    db.session.add(delivery)

            db.session.commit()
            flash(f"Waste for Request #{pickup.id} successfully segregated! {points} reward points credited to household {household.name}.", "success")
            return redirect(url_for('center.dashboard'))

        except Exception:
            db.session.rollback()
            flash("Failed to process segregation record. Please try again.", "danger")
            return render_template('center/segregate.html', pickup=pickup, center=center, recyclers=recyclers)

    return render_template('center/segregate.html', pickup=pickup, center=center, recyclers=recyclers)
