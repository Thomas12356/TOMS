"""Owner overview, local-only reads and grouped navigation at four widths."""
import logging
from pathlib import Path
import sys
from threading import Thread
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'tests'))
from playwright.sync_api import sync_playwright
from sqlalchemy import text
from werkzeug.serving import make_server
from app import app
from test_test_data import TestDataTests

logging.getLogger('werkzeug').setLevel(logging.ERROR)
with sync_playwright() as playwright:
    browser = playwright.chromium.launch(headless=True)
    for width in (320, 390, 768, 1280):
        fixture = TestDataTests()
        try:
            fixture.setUp()
            fixture.start_sync.return_value = False
            server = make_server('127.0.0.1', 0, app, threaded=True)
            worker = Thread(target=server.serve_forever, daemon=True)
            worker.start()
            fixture.addCleanup(server.server_close)
            fixture.addCleanup(worker.join, 5)
            fixture.addCleanup(server.shutdown)
            origin = f'http://127.0.0.1:{server.server_port}'
            page = browser.new_page(viewport={'width': width, 'height': 900})
            cookie = fixture.client.get_cookie(app.config['SESSION_COOKIE_NAME'])
            page.context.add_cookies([dict(name=cookie.key, value=cookie.value, url=origin)])
            errors, requests = [], []
            page.on('pageerror', lambda error: errors.append(str(error)))
            page.on('request', lambda request: requests.append(request.url))
            page.goto(origin + '/')
            assert page.url.endswith('/dashboard/overview')
            assert page.get_by_role('heading', name='Your money overview', exact=True).is_visible()
            assert 'Unavailable' in page.locator('#main-content').inner_text()
            assert 'No successful full import recorded.' in page.locator('#main-content').inner_text()
            page.keyboard.press('Tab')
            assert page.get_by_role('link', name='Skip to content').evaluate('(node) => node === document.activeElement')
            page.keyboard.press('Enter')
            assert page.locator('#main-content').evaluate('(node) => node === document.activeElement')
            assert page.evaluate('document.documentElement.scrollWidth <= innerWidth'), width
            assert not any('/balances' in url or url.endswith('/sync') for url in requests), requests
            page.goto(origin + '/dashboard/overview')
            # The browser retains its own dataset selection. Switch with its form.
            if width <= 640:
                page.get_by_role('button', name='Toggle navigation').click()
            with page.expect_navigation():
                page.get_by_role('switch', name='Test data').click()
            # The existing switch returns to the ledger, which loads mock
            # balances. Scope this assertion to opening the overview itself.
            page.wait_for_load_state('networkidle')
            requests.clear()
            page.goto(origin + '/dashboard/overview')
            assert page.get_by_role('complementary', name='Test mode', exact=True).is_visible()
            assert 'sample' in page.locator('#import-heading').locator('..').inner_text()
            assert page.evaluate('document.documentElement.scrollWidth <= innerWidth'), width
            page.screenshot(path=f'/tmp/toms-overview-{width}.png', full_page=True)
            if width <= 640:
                page.get_by_role('button', name='Toggle navigation').click()
            assert page.get_by_role('group', name='Income and work', exact=True).is_visible()
            assert page.get_by_role('group', name='Settings', exact=True).is_visible()
            nav = page.locator('#main-navigation')
            nav.get_by_role('link', name='Overview', exact=True).focus()
            if width <= 640:
                page.keyboard.press('Escape')
                assert nav.is_hidden()
                assert page.get_by_role('button', name='Toggle navigation').evaluate('(node) => node === document.activeElement')
            with fixture.admin.begin() as connection:
                connection.execute(text(f'UPDATE {fixture.demo_schema}.income_streams SET expected_gross_minor=NULL, expected_gross_period=NULL, expected_gross_currency=NULL'))
            page.reload()
            assert 'Needs attention' in page.locator('#main-content').inner_text()
            assert 'Unavailable' in page.locator('#reserve-heading').locator('..').inner_text()
            with patch('routes.overview.saved_overview', side_effect=ValueError('not used')):
                # Query validation runs before building the snapshot.
                page.goto(origin + '/dashboard/overview?unexpected=1')
                assert page.get_by_role('alert').inner_text() == 'Unknown query parameter.'
            assert not any('/balances' in url or url.endswith('/sync') for url in requests), requests
            fixture.start_sync.assert_not_called()
            fixture.bank.assert_not_called()
            assert not errors, errors
            page.close()
        finally:
            fixture.doCleanups()
    browser.close()
print('Overview: saved-only reads, incomplete/sample states, keyboard navigation and layouts passed at four widths.')
