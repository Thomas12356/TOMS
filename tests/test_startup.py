"""Check launcher hooks without starting a server or issuing real setup tokens."""

import os
import runpy
from pathlib import Path
from unittest.mock import patch

from app import app
from services.database.connection import db
from support import ApiTestCase

ROOT = Path(__file__).resolve().parents[1]


class StartupTests(ApiTestCase):
    def test_only_flask_run_launcher_announces_setup(self):
        for arguments, reloader, expected in ((['flask', 'run'], '', True),
                                               (['flask', 'run'], 'true', False),
                                               (['flask', 'db-upgrade'], '', False),
                                               (['flask', 'owner-password'], '', False)):
            with self.subTest(arguments=arguments, reloader=reloader), \
                    patch.dict(os.environ, {'FLASK_RUN_FROM_CLI': 'true', 'WERKZEUG_RUN_MAIN': reloader}), \
                    patch('sys.argv', arguments), patch('services.web.setup.announce_setup') as announce:
                runpy.run_path(str(ROOT / 'app.py'), run_name='startup_probe')
                self.assertEqual(announce.call_count, int(expected))

    def test_direct_python_launcher_announces_before_run(self):
        with patch.dict(os.environ, {'FLASK_RUN_FROM_CLI': '', 'WERKZEUG_RUN_MAIN': ''}), \
                patch('services.web.setup.announce_setup') as announce, patch('flask.Flask.run') as run:
            runpy.run_path(str(ROOT / 'app.py'), run_name='__main__')
            announce.assert_called_once()
            run.assert_called_once_with(host='127.0.0.1', port=5000)

    def test_gunicorn_announces_once_and_discards_prefork_database_pool(self):
        config = runpy.run_path(str(ROOT / 'gunicorn.conf.py'))
        with app.app_context(), patch.object(db.engine, 'dispose') as dispose, \
                patch('services.web.setup.announce_setup') as announce:
            config['on_starting'](None)
            announce.assert_called_once_with(app)
            dispose.assert_called_once()
