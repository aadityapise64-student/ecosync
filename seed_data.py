from datetime import datetime, date, timedelta, timezone
from app import create_app
from models import (
    db, User, Address, CollectionCenter, RecyclerOrg, 
    PickupRequest, WorkerAssignment, SegregationRecord, 
    RecyclerDelivery, Coupon, RewardTransaction
)

def seed_database():
    app = create_app()
    with app.app_context():
        # Clear existing tables
        db.drop_all()
        db.create_all()
        print("Dropped and recreated database tables with indexes and cascade rules.")

        # 1. Create Users for all 5 roles
        household = User(
            name="Aaditya Kulkarni",
            email="household@ecosync.com",
            role="household",
            phone="9876543210",
            reward_points=240
        )
        household.set_password("password123")

        household2 = User(
            name="Neha Patil",
            email="neha@ecosync.com",
            role="household",
            phone="9823012345",
            reward_points=85
        )
        household2.set_password("password123")

        # Pickup Workers
        worker1 = User(
            name="Ramesh Shinde",
            email="worker@ecosync.com",
            role="worker",
            phone="9123456780"
        )
        worker1.set_password("password123")

        worker2 = User(
            name="Suresh Gaikwad",
            email="worker2@ecosync.com",
            role="worker",
            phone="9123456781"
        )
        worker2.set_password("password123")

        # Collection Center Manager
        center_user = User(
            name="Pravin More (Center Manager)",
            email="center@ecosync.com",
            role="center",
            phone="9850123456"
        )
        center_user.set_password("password123")

        # Recycler Org Representative
        recycler_user = User(
            name="Vikas Deshmukh (Recycling Partner)",
            email="recycler@ecosync.com",
            role="recycler",
            phone="9422019283"
        )
        recycler_user.set_password("password123")

        # Admin / Municipality
        admin = User(
            name="Municipal Commissioner Admin",
            email="admin@ecosync.com",
            role="admin",
            phone="0231-2540001"
        )
        admin.set_password("admin123")

        db.session.add_all([household, household2, worker1, worker2, center_user, recycler_user, admin])
        db.session.commit()
        print("Created 5-role users.")

        # 2. Create Collection Center Profile
        center = CollectionCenter(
            user_id=center_user.id,
            center_name="Shivaji Nagar Waste Recovery & Segregation Facility",
            address="Plot 42, MIDC Industrial Area, Shiroli, Kolhapur",
            contact_number="0231-2654321",
            capacity_kg=8000.0,
            operating_hours="07:00 AM - 07:00 PM"
        )
        db.session.add(center)
        db.session.commit()

        # 3. Create Recycler Organization Profile
        recycler = RecyclerOrg(
            user_id=recycler_user.id,
            org_name="GreenEarth Materials Recovery & Recycling Pvt Ltd",
            address="Gat No. 115, Gokul Shirgaon Industrial Area, Kolhapur",
            materials_accepted="PET Plastic, HDPE, Aluminium Cans, Corrugated Cardboard, Glass",
            license_number="KMC-PCB-REC-2026-88"
        )
        db.session.add(recycler)
        db.session.commit()

        # 4. Create Addresses for Household
        addr1 = Address(
            user_id=household.id,
            title="Home",
            address_line="Flat 402, Green Meadows, Tarabai Park",
            area="Tarabai Park",
            city="Kolhapur",
            pincode="416003",
            latitude=16.7112,
            longitude=74.2378
        )
        addr2 = Address(
            user_id=household.id,
            title="Parents' House",
            address_line="Bunglow 14, 5th Lane, Rajarampuri",
            area="Rajarampuri",
            city="Kolhapur",
            pincode="416008",
            latitude=16.6954,
            longitude=74.2465
        )
        addr3 = Address(
            user_id=household2.id,
            title="Home",
            address_line="House No. 89, Nagala Park",
            area="Nagala Park",
            city="Kolhapur",
            pincode="416003",
            latitude=16.7160,
            longitude=74.2310
        )
        db.session.add_all([addr1, addr2, addr3])
        db.session.commit()

        # 5. Create Coupons Catalog
        coupons = [
            Coupon(
                code="GROCERY50",
                title="₹50 Discount at D-Mart / Supermarket",
                description="Redeemable on minimum grocery purchase of ₹500 at partnered city supermarkets.",
                points_cost=100,
                discount_value="₹50 OFF",
                partner_name="Kolhapur Supermarket Network",
                category="Grocery",
                valid_until=date.today() + timedelta(days=90),
                is_active=True
            ),
            Coupon(
                code="BIOCOMPOST5KG",
                title="Free 5kg Municipal Organic Compost Bag",
                description="Get a 5kg bag of high-grade enriched organic garden vermicompost produced from wet waste.",
                points_cost=150,
                discount_value="FREE BAG",
                partner_name="KMC Bio-Fertilizer Unit",
                category="Fertilizer/Compost",
                valid_until=date.today() + timedelta(days=180),
                is_active=True
            ),
            Coupon(
                code="TAXRATE5",
                title="5% Property Tax Rebate Voucher",
                description="Apply this coupon code during online municipal property tax payment for direct 5% discount.",
                points_cost=300,
                discount_value="5% TAX REBATE",
                partner_name="Kolhapur Municipal Corporation",
                category="Municipal Rebate",
                valid_until=date.today() + timedelta(days=120),
                is_active=True
            ),
            Coupon(
                code="ECOKIT1",
                title="Eco-Friendly Household Starter Kit",
                description="Contains 3 segregation cloth bins, bamboo toothbrushes, and reusable jute grocery bags.",
                points_cost=200,
                discount_value="GIFT KIT",
                partner_name="Clean City Foundation",
                category="Eco-Store",
                valid_until=date.today() + timedelta(days=60),
                is_active=True
            )
        ]
        db.session.add_all(coupons)
        db.session.commit()

        # 6. Sample Initial Pickup Requests across status stages
        # Request 1: Requested (Pending Worker Assignment)
        req1 = PickupRequest(
            household_id=household.id,
            address_id=addr1.id,
            waste_category="Recyclable",
            scheduled_date=date.today(),
            time_slot="Morning (7-11 AM)",
            status="Requested",
            notes="3 bags of plastic bottles and cardboard packaging from recent deliveries."
        )

        # Request 2: Assigned (Assigned to Worker 1)
        req2 = PickupRequest(
            household_id=household.id,
            address_id=addr2.id,
            waste_category="Organic",
            scheduled_date=date.today(),
            time_slot="Afternoon (12-4 PM)",
            status="Assigned",
            notes="Garden cuttings, dry leaves, and kitchen fruit peels."
        )

        # Request 3: Accepted (Worker 1 accepted, on the way)
        req3 = PickupRequest(
            household_id=household2.id,
            address_id=addr3.id,
            waste_category="Hazardous",
            scheduled_date=date.today(),
            time_slot="Morning (7-11 AM)",
            status="Accepted",
            notes="Expired UPS battery and 2 discarded paint cans."
        )

        # Request 4: At Center (Delivered to Shivaji Nagar center, awaiting segregation)
        req4 = PickupRequest(
            household_id=household.id,
            address_id=addr1.id,
            waste_category="Recyclable",
            scheduled_date=date.today() - timedelta(days=1),
            time_slot="Morning (7-11 AM)",
            status="At Center",
            notes="Assorted metals and mixed plastic."
        )

        # Request 5: Segregated & Completed
        req5 = PickupRequest(
            household_id=household.id,
            address_id=addr1.id,
            waste_category="Recyclable",
            scheduled_date=date.today() - timedelta(days=2),
            time_slot="Morning (7-11 AM)",
            status="Segregated",
            notes="Newspaper bundles, crushed tins, and detergent cans."
        )

        db.session.add_all([req1, req2, req3, req4, req5])
        db.session.commit()

        # Worker assignments
        assign2 = WorkerAssignment(
            request_id=req2.id,
            worker_id=worker1.id,
            center_id=center.id,
            status="Assigned"
        )
        assign3 = WorkerAssignment(
            request_id=req3.id,
            worker_id=worker1.id,
            center_id=center.id,
            status="Accepted"
        )
        assign4 = WorkerAssignment(
            request_id=req4.id,
            worker_id=worker2.id,
            center_id=center.id,
            status="Delivered"
        )
        assign5 = WorkerAssignment(
            request_id=req5.id,
            worker_id=worker1.id,
            center_id=center.id,
            status="Delivered"
        )
        db.session.add_all([assign2, assign3, assign4, assign5])
        db.session.commit()

        # Segregation Record for Request 5
        now_utc = datetime.now(timezone.utc)
        seg5 = SegregationRecord(
            request_id=req5.id,
            center_id=center.id,
            organic_kg=0.0,
            recyclable_plastic_kg=6.5,
            recyclable_metal_kg=3.0,
            recyclable_glass_kg=2.0,
            recyclable_paper_kg=12.5,
            non_recyclable_kg=0.8,
            hazardous_kg=0.0,
            total_weight_kg=24.8,
            points_awarded=150,
            routed_to="Recycler Org",
            remarks="Excellent dry waste segregation by resident.",
            recorded_at=now_utc - timedelta(days=1)
        )
        db.session.add(seg5)
        db.session.commit()

        # Recycler delivery for Request 5
        delivery5 = RecyclerDelivery(
            segregation_id=seg5.id,
            center_id=center.id,
            recycler_id=recycler.id,
            materials_summary="24.0 kg Mixed High-Grade Recyclables (Plastic, Paper, Metal)",
            total_weight_kg=24.0,
            status="Received",
            received_at=now_utc - timedelta(hours=12),
            acknowledgment_notes="Batch received at gate #2. Baled for processing."
        )
        db.session.add(delivery5)

        # Reward transaction history
        reward_tx = RewardTransaction(
            user_id=household.id,
            coupon_id=None,
            transaction_type="EARNED",
            points=150,
            description="Reward for 24.8 kg segregated recyclable pickup #5",
            created_at=now_utc - timedelta(days=1)
        )
        db.session.add(reward_tx)
        db.session.commit()

        print("Database seeded with sample requests, assignments, segregation, deliveries, and coupons!")

if __name__ == '__main__':
    seed_database()
