from datetime import datetime, timezone
from flask_sqlalchemy import SQLAlchemy
from flask_login import UserMixin
from werkzeug.security import generate_password_hash, check_password_hash

db = SQLAlchemy()

def utc_now():
    """Return current timezone-aware UTC datetime."""
    return datetime.now(timezone.utc)

class User(UserMixin, db.Model):
    __tablename__ = 'users'

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    email = db.Column(db.String(120), unique=True, index=True, nullable=False)
    password_hash = db.Column(db.String(255), nullable=False)
    role = db.Column(db.String(20), nullable=False, default='household', index=True)  # household, worker, center, recycler, admin
    phone = db.Column(db.String(20), nullable=True)
    reward_points = db.Column(db.Integer, default=0, nullable=False)
    created_at = db.Column(db.DateTime, default=utc_now, nullable=False)

    # Relationships with appropriate delete cascades
    addresses = db.relationship('Address', backref='user', lazy=True, cascade='all, delete-orphan')
    pickup_requests = db.relationship('PickupRequest', backref='household', lazy=True, foreign_keys='PickupRequest.household_id', cascade='all, delete-orphan')
    assignments = db.relationship('WorkerAssignment', backref='worker', lazy=True, foreign_keys='WorkerAssignment.worker_id', cascade='all, delete-orphan')
    reward_transactions = db.relationship('RewardTransaction', backref='user', lazy=True, cascade='all, delete-orphan')
    
    # 1-to-1 profiles for center or recycler user accounts
    center_profile = db.relationship('CollectionCenter', backref='manager', uselist=False, lazy=True, cascade='all, delete-orphan')
    recycler_profile = db.relationship('RecyclerOrg', backref='manager', uselist=False, lazy=True, cascade='all, delete-orphan')

    def set_password(self, password: str) -> None:
        self.password_hash = generate_password_hash(password)

    def check_password(self, password: str) -> bool:
        return check_password_hash(self.password_hash, password)

    def __repr__(self):
        return f'<User {self.email} ({self.role})>'


class Address(db.Model):
    __tablename__ = 'addresses'

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id', ondelete='CASCADE'), nullable=False, index=True)
    title = db.Column(db.String(50), default='Home')
    address_line = db.Column(db.String(255), nullable=False)
    area = db.Column(db.String(100), nullable=True)
    city = db.Column(db.String(100), nullable=False, default='Kolhapur')
    pincode = db.Column(db.String(20), nullable=False)
    latitude = db.Column(db.Float, nullable=True)
    longitude = db.Column(db.Float, nullable=True)

    def __repr__(self):
        return f'<Address {self.title}: {self.address_line}, {self.city}>'


class CollectionCenter(db.Model):
    __tablename__ = 'collection_centers'

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id', ondelete='CASCADE'), nullable=False, index=True)
    center_name = db.Column(db.String(120), nullable=False)
    address = db.Column(db.String(255), nullable=False)
    contact_number = db.Column(db.String(20), nullable=True)
    capacity_kg = db.Column(db.Float, default=5000.0)
    operating_hours = db.Column(db.String(50), default='08:00 AM - 06:00 PM')
    
    # Relationships
    segregation_records = db.relationship('SegregationRecord', backref='center', lazy=True, cascade='all, delete-orphan')
    dispatched_deliveries = db.relationship('RecyclerDelivery', backref='center', lazy=True, cascade='all, delete-orphan')

    def __repr__(self):
        return f'<CollectionCenter {self.center_name}>'


class RecyclerOrg(db.Model):
    __tablename__ = 'recycler_orgs'

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id', ondelete='CASCADE'), nullable=False, index=True)
    org_name = db.Column(db.String(120), nullable=False)
    address = db.Column(db.String(255), nullable=False)
    materials_accepted = db.Column(db.String(200), default='Plastic, Metal, Glass, Paper')
    license_number = db.Column(db.String(50), nullable=True)

    # Relationships
    received_deliveries = db.relationship('RecyclerDelivery', backref='recycler', lazy=True, cascade='all, delete-orphan')

    def __repr__(self):
        return f'<RecyclerOrg {self.org_name}>'


class PickupRequest(db.Model):
    __tablename__ = 'pickup_requests'

    id = db.Column(db.Integer, primary_key=True)
    household_id = db.Column(db.Integer, db.ForeignKey('users.id', ondelete='CASCADE'), nullable=False, index=True)
    address_id = db.Column(db.Integer, db.ForeignKey('addresses.id'), nullable=False, index=True)
    waste_category = db.Column(db.String(50), nullable=False)  # Organic, Recyclable, Hazardous, Non-Recyclable
    photo_filename = db.Column(db.String(255), nullable=True)
    scheduled_date = db.Column(db.Date, nullable=False)
    time_slot = db.Column(db.String(50), nullable=False)  # Morning (7-11 AM), Afternoon (12-4 PM), Evening (5-8 PM)
    
    # Status progression: Requested -> Assigned -> Accepted -> Collected -> At Center -> Segregated (or Cancelled)
    status = db.Column(db.String(30), default='Requested', nullable=False, index=True)
    notes = db.Column(db.Text, nullable=True)
    created_at = db.Column(db.DateTime, default=utc_now, nullable=False)

    # Relationships
    address = db.relationship('Address', backref='requests', lazy=True)
    assignment = db.relationship('WorkerAssignment', backref='request', uselist=False, lazy=True, cascade='all, delete-orphan')
    segregation_record = db.relationship('SegregationRecord', backref='request', uselist=False, lazy=True, cascade='all, delete-orphan')

    def __repr__(self):
        return f'<PickupRequest #{self.id} - {self.waste_category} ({self.status})>'


class WorkerAssignment(db.Model):
    __tablename__ = 'worker_assignments'

    id = db.Column(db.Integer, primary_key=True)
    request_id = db.Column(db.Integer, db.ForeignKey('pickup_requests.id', ondelete='CASCADE'), unique=True, nullable=False, index=True)
    worker_id = db.Column(db.Integer, db.ForeignKey('users.id', ondelete='CASCADE'), nullable=False, index=True)
    center_id = db.Column(db.Integer, db.ForeignKey('collection_centers.id'), nullable=True, index=True)
    status = db.Column(db.String(30), default='Assigned', nullable=False, index=True)  # Assigned, Accepted, In Progress, Delivered, Cancelled
    assigned_at = db.Column(db.DateTime, default=utc_now, nullable=False)
    updated_at = db.Column(db.DateTime, default=utc_now, onupdate=utc_now, nullable=False)

    # Designated center to deliver waste to
    destination_center = db.relationship('CollectionCenter', backref='incoming_worker_assignments', lazy=True)

    def __repr__(self):
        return f'<WorkerAssignment Request #{self.request_id} -> Worker #{self.worker_id}>'


class SegregationRecord(db.Model):
    __tablename__ = 'segregation_records'

    id = db.Column(db.Integer, primary_key=True)
    request_id = db.Column(db.Integer, db.ForeignKey('pickup_requests.id', ondelete='CASCADE'), unique=True, nullable=False, index=True)
    center_id = db.Column(db.Integer, db.ForeignKey('collection_centers.id', ondelete='CASCADE'), nullable=False, index=True)
    
    # Material Streams weighed at the collection center (in kg)
    organic_kg = db.Column(db.Float, default=0.0, nullable=False)
    recyclable_plastic_kg = db.Column(db.Float, default=0.0, nullable=False)
    recyclable_metal_kg = db.Column(db.Float, default=0.0, nullable=False)
    recyclable_glass_kg = db.Column(db.Float, default=0.0, nullable=False)
    recyclable_paper_kg = db.Column(db.Float, default=0.0, nullable=False)
    non_recyclable_kg = db.Column(db.Float, default=0.0, nullable=False)
    hazardous_kg = db.Column(db.Float, default=0.0, nullable=False)
    
    total_weight_kg = db.Column(db.Float, default=0.0, nullable=False)
    points_awarded = db.Column(db.Integer, default=0, nullable=False)
    routed_to = db.Column(db.String(50), default='Recycler Org', nullable=False)  # Recycler Org, Composting, Disposal
    remarks = db.Column(db.Text, nullable=True)
    recorded_at = db.Column(db.DateTime, default=utc_now, nullable=False)

    # Relationship to delivery dispatch if sent to recycler
    delivery = db.relationship('RecyclerDelivery', backref='segregation_record', uselist=False, lazy=True, cascade='all, delete-orphan')

    @property
    def total_recyclable_kg(self):
        return (self.recyclable_plastic_kg + self.recyclable_metal_kg + 
                self.recyclable_glass_kg + self.recyclable_paper_kg)


class RecyclerDelivery(db.Model):
    __tablename__ = 'recycler_deliveries'

    id = db.Column(db.Integer, primary_key=True)
    segregation_id = db.Column(db.Integer, db.ForeignKey('segregation_records.id', ondelete='CASCADE'), unique=True, nullable=False, index=True)
    center_id = db.Column(db.Integer, db.ForeignKey('collection_centers.id', ondelete='CASCADE'), nullable=False, index=True)
    recycler_id = db.Column(db.Integer, db.ForeignKey('recycler_orgs.id', ondelete='CASCADE'), nullable=False, index=True)
    materials_summary = db.Column(db.String(255), nullable=False)
    total_weight_kg = db.Column(db.Float, nullable=False)
    status = db.Column(db.String(30), default='Dispatched', nullable=False, index=True)  # Dispatched, Received, Processed
    dispatched_at = db.Column(db.DateTime, default=utc_now, nullable=False)
    received_at = db.Column(db.DateTime, nullable=True)
    acknowledgment_notes = db.Column(db.Text, nullable=True)

    def __repr__(self):
        return f'<RecyclerDelivery #{self.id} -> Recycler #{self.recycler_id}>'


class Coupon(db.Model):
    __tablename__ = 'coupons'

    id = db.Column(db.Integer, primary_key=True)
    code = db.Column(db.String(50), unique=True, nullable=False, index=True)
    title = db.Column(db.String(100), nullable=False)
    description = db.Column(db.Text, nullable=False)
    points_cost = db.Column(db.Integer, nullable=False)
    discount_value = db.Column(db.String(50), nullable=False)
    partner_name = db.Column(db.String(100), nullable=False)
    category = db.Column(db.String(50), default='Grocery', nullable=False)  # Grocery, Fertilizer/Compost, Municipal Rebate, Eco-Store
    valid_until = db.Column(db.Date, nullable=False)
    is_active = db.Column(db.Boolean, default=True, nullable=False, index=True)
    created_at = db.Column(db.DateTime, default=utc_now, nullable=False)

    # Transactions associated with coupon redemptions
    redemptions = db.relationship('RewardTransaction', backref='coupon', lazy=True)

    def __repr__(self):
        return f'<Coupon {self.code} - {self.title}>'


class RewardTransaction(db.Model):
    __tablename__ = 'reward_transactions'

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id', ondelete='CASCADE'), nullable=False, index=True)
    coupon_id = db.Column(db.Integer, db.ForeignKey('coupons.id'), nullable=True, index=True)
    transaction_type = db.Column(db.String(20), nullable=False, index=True)  # EARNED, REDEEMED
    points = db.Column(db.Integer, nullable=False)
    description = db.Column(db.String(255), nullable=False)
    created_at = db.Column(db.DateTime, default=utc_now, nullable=False)

    def __repr__(self):
        return f'<RewardTransaction {self.transaction_type}: {self.points} pts for User #{self.user_id}>'
