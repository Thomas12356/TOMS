"""Owner overview, local-only reads and compact navigation at five widths."""
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
    for width in (320, 390, 768, 1024, 1280):
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
            if width <= 900:
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
            if width <= 900:
                page.get_by_role('button', name='Toggle navigation').click()
            assert page.get_by_role('group', name='Income and work', exact=True).is_visible()
            assert page.get_by_role('group', name='Settings', exact=True).is_visible()
            nav = page.locator('#main-navigation')
            work = nav.locator('details').filter(has=page.locator('summary', has_text='Work & income'))
            settings = nav.locator('details').filter(has=page.locator('summary', has_text='Settings'))
            assert work.get_attribute('open') is None
            assert nav.get_by_role('link', name='Income sources', exact=True).is_hidden()
            work.locator('summary').focus()
            page.keyboard.press('Enter')
            assert nav.get_by_role('link', name='Income sources', exact=True).is_visible()
            nav.get_by_role('link', name='Income sources', exact=True).focus()
            page.keyboard.press('Escape')
            assert work.locator('summary').evaluate('(node) => node === document.activeElement')
            assert work.get_attribute('open') is None
            assert nav.is_visible()
            work.locator('summary').click()
            settings.locator('summary').click()
            assert nav.get_by_role('button', name='Sign out', exact=True).is_visible()
            nav.get_by_role('link', name='Income sources', exact=True).wait_for(state='hidden')
            assert work.get_attribute('open') is None
            assert page.evaluate('document.documentElement.scrollWidth <= innerWidth'), width
            if width in (390, 1024):
                # Full-page capture temporarily resizes Chromium to 1px and
                # correctly closes menus at the responsive breakpoint.
                page.screenshot(path=f'/tmp/toms-compact-menu-{width}.png')
                assert nav.get_by_role('button', name='Sign out', exact=True).is_visible()
            page.locator('h1').click()
            assert settings.get_attribute('open') is None
            if width >= 1024:
                # Existing income notices are a separate row in this header.
                assert nav.bounding_box()['height'] <= 50
                assert abs(nav.bounding_box()['y'] - page.locator('.brand').bounding_box()['y']) < 10
            if width in (390, 1024, 1280):
                if width <= 900:
                    page.get_by_role('button', name='Toggle navigation').click()
                page.screenshot(path=f'/tmp/toms-compact-nav-{width}.png', full_page=True)
                if width <= 900:
                    page.get_by_role('button', name='Toggle navigation').click()
            nav.get_by_role('link', name='Home', exact=True).focus()
            if width <= 900:
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
            # Native details remain usable with JavaScript disabled.
            context = browser.new_context(java_script_enabled=False, viewport={'width': width, 'height': 900})
            context.add_cookies([dict(name=cookie.key, value=cookie.value, url=origin)])
            plain = context.new_page()
            plain.goto(origin + '/dashboard/overview')
            plain.locator('#main-navigation summary').filter(has_text='Work & income').click()
            plain.locator('#main-navigation').get_by_role('link', name='Work and payment matching', exact=True).click()
            assert plain.url.endswith('/dashboard/reconciliation')
            assert plain.locator('details.current-section summary').inner_text() == 'Work & income'
            assert plain.evaluate('document.documentElement.scrollWidth <= innerWidth')
            context.close()
            fixture.bank.assert_not_called()
            fixture.start_sync.assert_not_called()
            print(f'PASS {width}px: compact menus, keyboard, focus, sample switch, no-JavaScript navigation and overview')
        finally:
            fixture.doCleanups()
    browser.close()
    print('Overview: saved-only reads, incomplete/sample states, keyboard and no-JavaScript navigation passed at five widths.')
