"""First-owner setup on disposable authentication/business schemas."""
import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path
import sys
from threading import Thread
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'tests'))
from playwright.sync_api import sync_playwright
from werkzeug.serving import make_server
from app import app
from models import BrowserSession, OwnerLogin, OwnerSetup
from services.database.connection import db
from services.web.sessions import token_hash
from test_test_data import TestDataTests

logging.getLogger('werkzeug').setLevel(logging.ERROR)

with sync_playwright() as playwright:
    browser = playwright.chromium.launch(headless=True)
    for width in (320, 390, 768, 1280):
        fixture = TestDataTests()
        try:
            fixture.setUp()
            fixture.start_sync.return_value = False
            fixture.enterContext(patch('routes.dashboard.starling_request', return_value={
                'effectiveBalance': {'minorUnits': 999, 'currency': 'GBP'}}))
            db.session.execute(db.delete(OwnerLogin))
            db.session.execute(db.delete(BrowserSession))
            db.session.add(OwnerSetup(id=1, token_hash=token_hash('synthetic-setup-token'),
                expires_at=datetime.now(timezone.utc) + timedelta(hours=1)))
            db.session.commit()
            db.session.remove()
            server = make_server('127.0.0.1', 0, app, threaded=True)
            worker = Thread(target=server.serve_forever, daemon=True)
            worker.start()
            fixture.addCleanup(server.server_close)
            fixture.addCleanup(worker.join, 5)
            fixture.addCleanup(server.shutdown)
            origin = f'http://127.0.0.1:{server.server_port}'
            page = browser.new_page(viewport={'width': width, 'height': 900})
            fixture.addCleanup(page.context.close)
            page.goto(origin + '/')
            assert page.url.endswith('/setup')
            for label, value in (('Setup token', 'invalid'), ('Username', 'synthetic-owner'),
                                 ('Confirm password', 'synthetic-password-123')):
                page.get_by_label(label, exact=True).fill(value)
            page.locator('#password').fill('synthetic-password-123')
            with page.expect_navigation():
                page.get_by_role('button', name='Create owner account').click()
            assert page.get_by_role('alert').inner_text() == 'Setup token is incorrect or expired.'
            page.get_by_label('Setup token', exact=True).fill('synthetic-setup-token')
            page.get_by_label('Username', exact=True).fill('synthetic-owner')
            page.locator('#password').fill('synthetic-password-123')
            page.get_by_label('Confirm password', exact=True).fill('synthetic-mismatch-123')
            with page.expect_navigation():
                page.get_by_role('button', name='Create owner account').click()
            assert page.get_by_role('alert').inner_text() == 'Passwords do not match.'
            assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
            page.screenshot(path=f'/tmp/toms-setup-{width}.png', full_page=True)
            page.get_by_label('Setup token', exact=True).fill('synthetic-setup-token')
            page.get_by_label('Username', exact=True).fill('synthetic-owner')
            page.locator('#password').fill('synthetic-password-123')
            page.get_by_label('Confirm password', exact=True).fill('synthetic-password-123')
            with page.expect_navigation():
                page.get_by_role('button', name='Create owner account').click()
            assert page.url.endswith('/login')
            page.get_by_label('Username', exact=True).fill('synthetic-owner')
            page.get_by_label('Password', exact=True).fill('synthetic-password-123')
            with page.expect_navigation():
                page.get_by_role('button', name='Sign in', exact=True).click()
            assert page.url.endswith('/dashboard/overview')
            page.goto(origin + '/setup')
            assert page.url.endswith('/login')
            fixture.bank.assert_not_called()
            print(f'PASS {width}px: isolated first-owner token validation, password correction, setup and login')
        finally:
            fixture.doCleanups()
    browser.close()
