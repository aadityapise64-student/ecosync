import unittest
from datetime import date, timedelta
from pathlib import Path

from app import create_app
from models import AIScan, Coupon, RewardTransaction, User, db


class TestConfig:
    TESTING = True
    SECRET_KEY = 'phase-b-test-only'
    WTF_CSRF_ENABLED = False
    SQLALCHEMY_DATABASE_URI = 'sqlite:///:memory:'
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    SQLALCHEMY_ENGINE_OPTIONS = {'pool_pre_ping': True}
    UPLOAD_FOLDER = str(Path(__file__).resolve().parents[1] / 'tmp' / 'phase-b-test-uploads')
    MAX_CONTENT_LENGTH = 16 * 1024 * 1024
    MAX_IMAGE_PIXELS = 20_000_000
    ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg', 'webp'}
    ROBOFLOW_API_KEY = ''
    AI_DEMO_MODE = False


class RewardIntegrityTests(unittest.TestCase):
    def setUp(self):
        self.app = create_app(TestConfig)
        self.context = self.app.app_context()
        self.context.push()
        db.create_all()
        self.client = self.app.test_client()
        self.user = User(name='Test Household', email='household@example.test', role='household', reward_points=0)
        self.user.set_password('test-password-123')
        db.session.add(self.user)
        db.session.commit()
        self.login_as(self.user.id)

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        self.context.pop()

    def login_as(self, user_id):
        with self.client.session_transaction() as session:
            session['_user_id'] = str(user_id)
            session['_fresh'] = True

    def test_live_scan_claim_credits_once(self):
        scan = AIScan(
            user_id=self.user.id,
            original_filename='test-scan.jpg',
            status='live',
            predictions_json='[{"class":"PLASTIC","confidence":0.9}]',
            class_counts_json='{"PLASTIC":2}',
            points_awarded=25,
        )
        db.session.add(scan)
        db.session.commit()

        first = self.client.post(f'/household/ai-scan/{scan.id}/claim')
        second = self.client.post(f'/household/ai-scan/{scan.id}/claim')

        db.session.refresh(self.user)
        db.session.refresh(scan)
        self.assertEqual(first.status_code, 302)
        self.assertEqual(second.status_code, 302)
        self.assertEqual(self.user.reward_points, 25)
        self.assertIsNotNone(scan.claimed_at)
        self.assertEqual(RewardTransaction.query.filter_by(ai_scan_id=scan.id).count(), 1)

    def test_zero_point_scan_cannot_create_reward_transaction(self):
        scan = AIScan(
            user_id=self.user.id,
            original_filename='empty-scan.jpg',
            status='live',
            predictions_json='[]',
            class_counts_json='{}',
            points_awarded=0,
        )
        db.session.add(scan)
        db.session.commit()

        self.client.post(f'/household/ai-scan/{scan.id}/claim')

        db.session.refresh(self.user)
        self.assertEqual(self.user.reward_points, 0)
        self.assertEqual(RewardTransaction.query.filter_by(ai_scan_id=scan.id).count(), 0)
        self.assertIsNone(scan.claimed_at)

    def test_coupon_redemption_idempotency_key_deducts_once(self):
        self.user.reward_points = 100
        coupon = Coupon(
            code='TEST-COUPON',
            title='Test coupon',
            description='For reward-flow tests',
            points_cost=30,
            discount_value='Test discount',
            partner_name='Test partner',
            category='Grocery',
            valid_until=date.today() + timedelta(days=10),
            is_active=True,
        )
        db.session.add(coupon)
        db.session.commit()

        form = {'idempotency_key': 'phase-b-one-time-test-key'}
        first = self.client.post(f'/household/rewards/redeem/{coupon.id}', data=form)
        second = self.client.post(f'/household/rewards/redeem/{coupon.id}', data=form)

        db.session.refresh(self.user)
        self.assertEqual(first.status_code, 302)
        self.assertEqual(second.status_code, 302)
        self.assertEqual(self.user.reward_points, 70)
        self.assertEqual(
            RewardTransaction.query.filter_by(idempotency_key=form['idempotency_key']).count(),
            1,
        )


if __name__ == '__main__':
    unittest.main()
