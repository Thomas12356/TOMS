"""Real owner login/password/logout flows in disposable PostgreSQL schemas."""
import logging
import sys
from pathlib import Path
from threading import Thread
from unittest.mock import patch

from werkzeug.serving import make_server
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'tests'))
from app import app
from test_test_data import TestDataTests

logging.getLogger('werkzeug').setLevel(logging.ERROR)
with sync_playwright() as playwright:
    browser = playwright.chromium.launch(headless=True)
    for width in (320, 390, 1280):
        fixture = TestDataTests()
        try:
            fixture.setUp()
            fixture.start_sync.return_value = False
            fixture.enterContext(patch('routes.dashboard.starling_request', return_value={
                'effectiveBalance': {'minorUnits': 999, 'currency': 'GBP'}}))
            server = make_server('127.0.0.1', 0, app, threaded=True)
            worker = Thread(target=server.serve_forever, daemon=True)
            worker.start()
            fixture.addCleanup(server.server_close)
            fixture.addCleanup(worker.join, 5)
            fixture.addCleanup(server.shutdown)
            origin = f'http://127.0.0.1:{server.server_port}'
            page = browser.new_page(viewport={'width': width, 'height': 850})
            errors = []
            page.on('pageerror', lambda error: errors.append(str(error)))
            fixture.addCleanup(page.context.close)

            def sign_in(password):
                page.goto(origin + '/login')
                page.get_by_label('Username', exact=True).fill('demo-test-owner')
                page.get_by_label('Password', exact=True).fill(password)
                with page.expect_navigation():
                    page.get_by_role('button', name='Sign in', exact=True).click()

            page.goto(origin + '/')
            assert page.url.endswith('/login')
            assert page.evaluate('document.documentElement.scrollWidth <= innerWidth'), width
            sign_in('wrong-password')
            assert page.get_by_role('alert').inner_text() == 'Incorrect username or password.'
            sign_in('test-pass-123')
            assert page.url.endswith('/dashboard')
            page.goto(origin + '/settings/password')
            assert page.get_by_role('heading', name='Change password', exact=True).is_visible()
            page.get_by_label('Current password', exact=True).fill('test-pass-123')
            page.locator('#password').fill('updated-pass-123')
            page.get_by_label('Confirm password', exact=True).fill('different-pass-123')
            with page.expect_navigation():
                page.get_by_role('button', name='Change password', exact=True).click()
            assert page.get_by_role('alert').inner_text() == 'Passwords do not match.'
            assert page.evaluate('document.documentElement.scrollWidth <= innerWidth'), width
            page.get_by_label('Current password', exact=True).fill('test-pass-123')
            page.locator('#password').fill('updated-pass-123')
            page.get_by_label('Confirm password', exact=True).fill('updated-pass-123')
            with page.expect_navigation():
                page.get_by_role('button', name='Change password', exact=True).click()
            assert page.url.endswith('/login')
            # The fixture's original session must also be revoked.
            assert fixture.client.get('/dashboard').status_code == 302
            sign_in('test-pass-123')
            assert page.get_by_role('alert').inner_text() == 'Incorrect username or password.'
            sign_in('updated-pass-123')
            page.goto(origin + '/settings/password')
            if width <= 640:
                page.get_by_role('button', name='Toggle navigation').click()
            with page.expect_navigation():
                page.get_by_role('button', name='Sign out', exact=True).click()
            assert page.url.endswith('/login')
            page.goto(origin + '/dashboard')
            assert page.url.endswith('/login')
            assert not errors, errors
            fixture.bank.assert_not_called()
            print(f'PASS: {width}px login failures, password mismatch/correction, session revocation and logout')
        finally:
            fixture.doCleanups()
    browser.close()
