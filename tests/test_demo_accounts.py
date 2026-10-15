import unittest
from pathlib import Path

from app import create_app
from models import Address, CollectionCenter, Coupon, RecyclerOrg, User, db
from services.demo_accounts import DEMO_USERS, provision_demo_accounts


class DemoConfig:
    TESTING = True
    SECRET_KEY = "demo-account-test-only"
    WTF_CSRF_ENABLED = False
    SQLALCHEMY_DATABASE_URI = "sqlite:///:memory:"
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    SQLALCHEMY_ENGINE_OPTIONS = {"pool_pre_ping": True}
    UPLOAD_FOLDER = str(Path(__file__).resolve().parents[1] / "tmp" / "demo-test-uploads")
    MAX_CONTENT_LENGTH = 16 * 1024 * 1024
    MAX_IMAGE_PIXELS = 20_000_000
    ALLOWED_EXTENSIONS = {"png", "jpg", "jpeg", "webp"}
    ENABLE_DEMO_ACCOUNTS = True
    ROBOFLOW_API_KEY = ""
    AI_DEMO_MODE = False


class DemoAccountTests(unittest.TestCase):
    def setUp(self):
        self.app = create_app(DemoConfig)
        self.context = self.app.app_context()
        self.context.push()
        db.create_all()
        self.client = self.app.test_client()

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        self.context.pop()

    def test_provision_is_safe_and_idempotent(self):
        provision_demo_accounts()
        provision_demo_accounts()

        self.assertEqual(User.query.count(), len(DEMO_USERS))
        self.assertEqual(Address.query.count(), 1)
        self.assertEqual(CollectionCenter.query.count(), 1)
        self.assertEqual(RecyclerOrg.query.count(), 1)
        self.assertEqual(Coupon.query.filter_by(code="DEMO-GROCERY50").count(), 1)
        for _, email, role, password, *_ in DEMO_USERS:
            user = User.query.filter_by(email=email).one()
            self.assertEqual(user.role, role)
            self.assertTrue(user.check_password(password))

    def test_each_advertised_account_can_log_in_and_open_its_dashboard(self):
        provision_demo_accounts()
        dashboard_by_role = {
            "household": "/household/dashboard",
            "worker": "/worker/dashboard",
            "center": "/center/dashboard",
            "recycler": "/recycler/dashboard",
            "admin": "/admin/dashboard",
        }

        for _, email, role, password, *_ in DEMO_USERS:
            with self.subTest(role=role):
                login = self.client.post(
                    "/auth/login",
                    data={"email": email, "password": password},
                    environ_base={"REMOTE_ADDR": f"192.0.2.{len(role) + 1}"},
                )
                self.assertEqual(login.status_code, 302)
                dashboard = self.client.get(dashboard_by_role[role])
                self.assertEqual(dashboard.status_code, 200)
                self.client.post("/auth/logout")


if __name__ == "__main__":
    unittest.main()
