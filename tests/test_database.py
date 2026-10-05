import unittest
from unittest.mock import patch

from flask import Flask
from sqlalchemy import event, text
from sqlalchemy.exc import OperationalError

from app import app
from services.database.connection import db, init_database


class DatabaseTests(unittest.TestCase):
    def setUp(self):
        self.client = app.test_client()
        config = patch.dict(app.config, {"APP_API_KEY": "test-app-key"})
        config.start()
        self.addCleanup(config.stop)

    def test_database_probe_is_authenticated(self):
        with patch("app.check_database") as probe:
            response = self.client.get("/health/db")
        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.headers["Cache-Control"], "no-store")
        probe.assert_not_called()

    def test_probe_uses_sqlalchemy_session(self):
        with patch.object(db.session, "scalar", return_value="TOMS") as scalar:
            response = self.client.get("/health/db", headers={"Authorization": "Bearer test-app-key"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json(), {"status": "ok", "database": "TOMS"})
        self.assertEqual(str(scalar.call_args.args[0]), "SELECT current_database()")

    def test_connection_failure_returns_safe_error(self):
        error = OperationalError("probe", {}, Exception("private connection details"))
        with patch.object(db.session, "scalar", side_effect=error):
            response = self.client.get("/health/db", headers={"Authorization": "Bearer test-app-key"})
        self.assertEqual(response.status_code, 503)
        self.assertNotIn("private connection details", response.get_data(as_text=True))

    def test_sessions_are_scoped_and_connections_returned_on_teardown(self):
        test_app = Flask(__name__)
        test_app.config.update(SQLALCHEMY_DATABASE_URI="sqlite://", SQLALCHEMY_ENGINE_OPTIONS={})
        init_database(test_app)
        returned = []
        with test_app.app_context():
            engine = db.engine
            event.listen(engine, "checkin", lambda *args: returned.append(True))
            first = db.session()
            self.assertIs(first, db.session())
            self.assertEqual(db.session.scalar(text("SELECT 1")), 1)
            self.assertEqual(returned, [])
        self.assertEqual(returned, [True])
        with test_app.app_context():
            self.assertIsNot(first, db.session())
        engine.dispose()

    def test_postgres_password_with_special_characters_is_not_interpolated(self):
        test_app = Flask(__name__)
        password = "fake:@/% password"
        with patch.dict("os.environ", {"PGPASSWORD": password}):
            init_database(test_app)
        url = test_app.config["SQLALCHEMY_DATABASE_URI"]
        self.assertEqual(url.password, password)
        self.assertEqual(url.drivername, "postgresql+psycopg")
        self.assertNotIn(password, str(url))
        with test_app.app_context():
            db.engine.dispose()


if __name__ == "__main__":
    unittest.main()
