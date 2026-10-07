import os
from datetime import date, timedelta
from app import create_app
from seed_data import seed_database
from models import db, User, Address, PickupRequest, WorkerAssignment, CollectionCenter, RecyclerOrg, SegregationRecord, RecyclerDelivery, Coupon, RewardTransaction

def run_e2e_verification():
    print("=" * 60)
    print("STARTING E2E VERIFICATION OF ECOSYNC PLATFORM (OPTIMIZED)")
    print("=" * 60)

    # Re-seed test database
    seed_database()

    app = create_app()
    client = app.test_client()

    with app.app_context():
        # 1. Verify Home Page & Security Headers
        res = client.get('/')
        assert res.status_code == 200
        assert b"Smart Door-to-Door" in res.data
        assert res.headers.get('X-Content-Type-Options') == 'nosniff'
        assert res.headers.get('X-Frame-Options') == 'SAMEORIGIN'
        print("[PASS] 1. Landing page & security headers verified (200 OK)")

        # 2. Login as Household
        res = client.post('/auth/login', data={
            'email': 'household@ecosync.com',
            'password': 'password123'
        }, follow_redirects=True)
        assert res.status_code == 200
        assert b"Welcome, Aaditya Kulkarni" in res.data
        print("[PASS] 2. Household login successful & redirected to Dashboard")

        # Get household user and address
        household = User.query.filter_by(email='household@ecosync.com').first()
        address = Address.query.filter_by(user_id=household.id).first()
        initial_points = household.reward_points

        # 3. Create a new Pickup Request
        res = client.post('/household/request/new', data={
            'address_id': address.id,
            'waste_category': 'Recyclable',
            'scheduled_date': date.today().isoformat(),
            'time_slot': 'Morning (7-11 AM)',
            'notes': 'Test verification request batch: PET bottles and aluminium cans'
        }, follow_redirects=True)
        assert res.status_code == 200
        
        # Verify request exists in DB
        pickup = PickupRequest.query.filter_by(notes='Test verification request batch: PET bottles and aluminium cans').first()
        assert pickup is not None
        assert pickup.status == 'Requested'
        print(f"[PASS] 3. Pickup Request #{pickup.id} submitted (Status: {pickup.status})")

        # 4. Security Check: IDOR address protection
        other_user_address = Address.query.filter(Address.user_id != household.id).first()
        if other_user_address:
            res = client.post('/household/request/new', data={
                'address_id': other_user_address.id,
                'waste_category': 'Recyclable',
                'scheduled_date': date.today().isoformat(),
                'time_slot': 'Morning (7-11 AM)',
                'notes': 'Malicious address injection attempt'
            }, follow_redirects=True)
            assert b"Invalid address selected" in res.data
            print("[PASS] 4. IDOR address authorization protection verified")

        # 5. Logout & Login as Admin to assign worker
        client.get('/auth/logout')
        res = client.post('/auth/login', data={
            'email': 'admin@ecosync.com',
            'password': 'admin123'
        }, follow_redirects=True)
        assert res.status_code == 200

        # Assign worker and center
        worker = User.query.filter_by(email='worker@ecosync.com').first()
        center = CollectionCenter.query.first()

        res = client.post('/admin/assign', data={
            'request_id': pickup.id,
            'worker_id': worker.id,
            'center_id': center.id
        }, follow_redirects=True)
        assert res.status_code == 200
        
        db.session.refresh(pickup)
        assert pickup.status == 'Assigned'
        assert pickup.assignment.worker_id == worker.id
        print(f"[PASS] 5. Admin assigned Request #{pickup.id} to Worker {worker.name} (Status: {pickup.status})")

        # 6. Worker accepts request
        client.get('/auth/logout')
        client.post('/auth/login', data={
            'email': 'worker@ecosync.com',
            'password': 'password123'
        })
        res = client.post(f'/worker/task/{pickup.id}/accept', follow_redirects=True)
        assert res.status_code == 200
        db.session.refresh(pickup)
        assert pickup.status == 'Accepted'
        print(f"[PASS] 6. Worker accepted Request #{pickup.id} (Status: {pickup.status})")

        # 7. Worker collects waste from household
        res = client.post(f'/worker/task/{pickup.id}/collect', follow_redirects=True)
        assert res.status_code == 200
        db.session.refresh(pickup)
        assert pickup.status == 'Collected'
        print(f"[PASS] 7. Worker collected waste from doorstep (Status: {pickup.status})")

        # 8. Worker delivers to Collection Center
        res = client.post(f'/worker/task/{pickup.id}/deliver', data={
            'center_id': center.id
        }, follow_redirects=True)
        assert res.status_code == 200
        db.session.refresh(pickup)
        assert pickup.status == 'At Center'
        print(f"[PASS] 8. Worker delivered waste to {center.center_name} (Status: {pickup.status})")

        # 9. Collection Center weighs & segregates materials
        client.get('/auth/logout')
        client.post('/auth/login', data={
            'email': 'center@ecosync.com',
            'password': 'password123'
        })
        recycler = RecyclerOrg.query.first()
        
        res = client.post(f'/center/segregate/{pickup.id}', data={
            'plastic_kg': 6.0,
            'metal_kg': 2.0,
            'paper_kg': 4.0,
            'glass_kg': 0.0,
            'organic_kg': 1.5,
            'hazardous_kg': 0.0,
            'non_recyclable_kg': 0.5,
            'routed_to': 'Recycler Org',
            'recycler_id': recycler.id,
            'remarks': 'Verified batch. Clean segregated recyclables.'
        }, follow_redirects=True)
        assert res.status_code == 200
        db.session.refresh(pickup)
        db.session.refresh(household)
        assert pickup.status == 'Segregated'

        # Check calculated points: (6*10) + (2*10) + (4*6) + (1.5*4) + (0.5*1) = 60 + 20 + 24 + 6 + 0 = 110 points
        expected_points = int((6.0 * 10) + (2.0 * 10) + (4.0 * 6) + (1.5 * 4) + (0.5 * 1))
        assert pickup.segregation_record.points_awarded == expected_points
        assert household.reward_points == initial_points + expected_points
        print(f"[PASS] 9. Collection Center segregated batch. Credited {expected_points} points to {household.name} (Status: {pickup.status})")

        # Check Recycler Delivery dispatch
        delivery = RecyclerDelivery.query.filter_by(segregation_id=pickup.segregation_record.id).first()
        assert delivery is not None
        assert delivery.status == 'Dispatched'
        print(f"[PASS] 10. Dispatched {delivery.total_weight_kg} kg recyclables to {recycler.org_name}")

        # 10. Recycler Organization receives and processes batch
        client.get('/auth/logout')
        client.post('/auth/login', data={
            'email': 'recycler@ecosync.com',
            'password': 'password123'
        })
        res = client.post(f'/recycler/delivery/{delivery.id}/receive', data={
            'notes': 'Intake quality verification passed.'
        }, follow_redirects=True)
        assert res.status_code == 200
        db.session.refresh(delivery)
        assert delivery.status == 'Received'

        res = client.post(f'/recycler/delivery/{delivery.id}/process', follow_redirects=True)
        assert res.status_code == 200
        db.session.refresh(delivery)
        assert delivery.status == 'Processed'
        print(f"[PASS] 11. Recycler acknowledged intake and marked batch Processed (Status: {delivery.status})")

        # 11. Household redeems reward coupon
        client.get('/auth/logout')
        client.post('/auth/login', data={
            'email': 'household@ecosync.com',
            'password': 'password123'
        })
        coupon = Coupon.query.filter_by(code='GROCERY50').first()
        points_before_redeem = household.reward_points
        res = client.post(f'/household/rewards/redeem/{coupon.id}', follow_redirects=True)
        assert res.status_code == 200
        db.session.refresh(household)
        assert household.reward_points == points_before_redeem - coupon.points_cost
        print(f"[PASS] 12. Household redeemed coupon '{coupon.code}' (-{coupon.points_cost} points). Remaining: {household.reward_points} pts")

        # 12. Admin Dashboard KPIs & Charts
        client.get('/auth/logout')
        client.post('/auth/login', data={
            'email': 'admin@ecosync.com',
            'password': 'admin123'
        })
        res = client.get('/admin/dashboard')
        assert res.status_code == 200
        assert b"Municipality Administrative Oversight" in res.data
        assert b"wasteDistributionChart" in res.data
        print("[PASS] 13. Municipality Admin Dashboard, SQL analytics, and Chart.js verified")

    print("=" * 60)
    print("ALL VERIFICATION & SECURITY CHECKS PASSED WITH ZERO ERRORS!")
    print("=" * 60)

if __name__ == '__main__':
    run_e2e_verification()
