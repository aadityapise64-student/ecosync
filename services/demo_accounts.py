"""Idempotently provision the public prototype's shared demo accounts.

This deliberately does not clear or recreate any database tables. Enable it only
for a demo deployment because these shared credentials are published on the
landing page.
"""

from datetime import date, timedelta

from models import Address, CollectionCenter, Coupon, RecyclerOrg, User, db


DEMO_USERS = (
    ("Aaditya Kulkarni", "household@ecosync.com", "household", "password123", "9876543210", 240),
    ("Ramesh Shinde", "worker@ecosync.com", "worker", "password123", "9123456780", 0),
    ("Pravin More (Center Manager)", "center@ecosync.com", "center", "password123", "9850123456", 0),
    ("Vikas Deshmukh (Recycling Partner)", "recycler@ecosync.com", "recycler", "password123", "9422019283", 0),
    ("Municipal Commissioner Admin", "admin@ecosync.com", "admin", "admin123", "0231-2540001", 0),
)


def provision_demo_accounts():
    """Create/update only the fixed demo identities and minimum dashboard data."""
    if not db.session.get_bind():
        raise RuntimeError("Database is not initialized.")

    users = {}
    for name, email, role, password, phone, initial_points in DEMO_USERS:
        user = User.query.filter_by(email=email).one_or_none()
        if user is None:
            user = User(name=name, email=email, role=role, phone=phone, reward_points=initial_points)
            db.session.add(user)
        else:
            # These exact addresses are advertised as shared demo identities.
            user.name = name
            user.role = role
            user.phone = phone
        user.set_password(password)
        users[role] = user

    db.session.flush()

    household = users["household"]
    if not Address.query.filter_by(user_id=household.id).first():
        db.session.add(Address(
            user_id=household.id,
            title="Demo Home",
            address_line="42 Sample Road",
            area="Demo Locality",
            city="Kolhapur",
            pincode="416003",
        ))

    center_user = users["center"]
    center = CollectionCenter.query.filter_by(user_id=center_user.id).one_or_none()
    if center is None:
        center = CollectionCenter(
            user_id=center_user.id,
            center_name="EcoSync Demo Collection Center",
            address="42 Sample Road, Kolhapur",
            contact_number=center_user.phone,
            capacity_kg=8000.0,
            operating_hours="08:00 AM - 06:00 PM",
        )
        db.session.add(center)

    recycler_user = users["recycler"]
    if RecyclerOrg.query.filter_by(user_id=recycler_user.id).one_or_none() is None:
        db.session.add(RecyclerOrg(
            user_id=recycler_user.id,
            org_name="EcoSync Demo Recycling Partner",
            address="Industrial Area, Kolhapur",
            materials_accepted="Plastic, Metal, Glass, Paper, Cardboard",
            license_number="DEMO-ONLY",
        ))

    if Coupon.query.filter_by(code="DEMO-GROCERY50").one_or_none() is None:
        db.session.add(Coupon(
            code="DEMO-GROCERY50",
            title="₹50 Demo Grocery Discount",
            description="Sample reward for testing the prototype.",
            points_cost=100,
            discount_value="₹50 OFF",
            partner_name="EcoSync Demo Partner",
            category="Grocery",
            valid_until=date.today() + timedelta(days=365),
            is_active=True,
        ))

    db.session.commit()
