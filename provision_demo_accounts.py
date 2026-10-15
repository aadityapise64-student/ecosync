"""Safe, idempotent provisioning entry point for Render demo deployments."""

from app import create_app
from services.demo_accounts import provision_demo_accounts


app = create_app()
if not app.config.get("ENABLE_DEMO_ACCOUNTS", False):
    raise SystemExit("Demo accounts are disabled. Set ENABLE_DEMO_ACCOUNTS=true to provision them.")

with app.app_context():
    provision_demo_accounts()
    print("EcoSync demo accounts and dashboard prerequisites are ready.")
