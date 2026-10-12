"""Actual forms survive a bounded server-clock regression without mode loss."""
import logging
from pathlib import Path
import sys
from threading import Thread
import time
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from playwright.sync_api import sync_playwright
from werkzeug.serving import make_server
from app import app
from browser_owner import install_owner_cookie
from test_test_data import TestDataTests

logging.getLogger('werkzeug').setLevel(logging.ERROR)
with sync_playwright() as playwright:
    browser = playwright.chromium.launch(headless=True)
    for width in (320, 390, 1280):
        fixture = TestDataTests()
        try:
            fixture.setUp()
            fixture.start_sync.return_value = False
            now = int(time.time())
            clock = fixture.enterContext(patch('itsdangerous.timed.TimestampSigner.get_timestamp', return_value=now))
            server = make_server('127.0.0.1', 0, app, threaded=True)
            worker = Thread(target=server.serve_forever, daemon=True)
            worker.start()
            fixture.addCleanup(server.server_close)
            fixture.addCleanup(worker.join, 5)
            fixture.addCleanup(server.shutdown)
            page = browser.new_page(viewport={'width':width,'height':900}, java_script_enabled=width!=390)
            fixture.addCleanup(page.context.close)
            origin = f'http://127.0.0.1:{server.server_port}'
            install_owner_cookie(page.context, fixture, origin)
            failures = []
            page.on('response', lambda response: failures.append(response.status) if response.request.method=='POST' and response.status>=400 else None)
            page.goto(origin+'/dashboard/overview')
            clock.return_value = now-1
            if width<=900 and width!=390:
                page.get_by_role('button',name='Toggle navigation').click()
            with page.expect_navigation():
                page.get_by_role('switch',name='Test data').click()
            page.wait_for_load_state('networkidle')
            assert page.get_by_role('complementary',name='Test mode',exact=True).is_visible()
            # Restore the clock for display, regress it again for actual saving.
            clock.return_value = now
            page.goto(origin+'/dashboard/cash-planning/new?kind=bill')
            clock.return_value = now-1
            page.locator('#commitment-name').fill('Clock regression bill')
            page.locator('#commitment-amount').fill('12.34')
            with page.expect_navigation():
                page.get_by_role('button',name='Save commitment',exact=True).click()
            assert page.get_by_role('heading',name='Clock regression bill',exact=True).is_visible()
            assert 'GBP 12.34 outstanding' in page.locator('main').inner_text()
            assert page.get_by_role('complementary',name='Test mode',exact=True).is_visible()
            assert not failures, failures
            assert fixture.live_value('SELECT count(*) FROM SCHEMA.cash_commitments')==0
            fixture.bank.assert_not_called()
            print(f'PASS {width}px: one-second backward server clock, authenticated mode switch and saved bill')
        finally:
            fixture.doCleanups()
    browser.close()
