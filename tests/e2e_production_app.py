"""Production middleware + built UI; cloud inference alone uses fixtures."""
from tests.e2e_import_app import app as api_app
from backend.app.production import create_app

app = create_app(api_app)
