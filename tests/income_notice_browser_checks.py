"""Notice polling failure/recovery and retry after an acknowledgement refresh fails."""
import logging
from pathlib import Path
import sys
from threading import Thread

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'tests'))
from playwright.sync_api import sync_playwright
from werkzeug.serving import make_server
from app import app
from test_test_data import TestDataTests

logging.getLogger('werkzeug').setLevel(logging.ERROR)
with sync_playwright() as playwright:
    browser = playwright.chromium.launch(headless=True)
    for width in (320, 1280):
        fixture = TestDataTests()
        try:
            fixture.setUp()
            fixture.start_sync.return_value = False
            assert fixture.toggle(True).status_code == 303
            server = make_server('127.0.0.1', 0, app, threaded=True)
            worker = Thread(target=server.serve_forever, daemon=True)
            worker.start()
            fixture.addCleanup(server.server_close)
            fixture.addCleanup(worker.join, 5)
            fixture.addCleanup(server.shutdown)
            origin = f'http://127.0.0.1:{server.server_port}'
            page = browser.new_page(viewport={'width': width, 'height': 900})
            page.clock.install()
            errors = []
            page.on('pageerror', lambda error: errors.append(str(error)))
            cookie = fixture.client.get_cookie(app.config['SESSION_COOKIE_NAME'])
            page.context.add_cookies([dict(name=cookie.key, value=cookie.value, url=origin)])
            notice_url = origin + '/dashboard/income-notifications'
            fail = lambda route: route.fulfill(status=503, json={'error': 'Unavailable'})
            page.route(notice_url, fail)
            page.goto(origin + '/dashboard/checklist')
            page.locator('#income-alert-bar').wait_for(state='visible')
            page.get_by_role('button', name='View income notices', exact=True).click()
            error = page.locator('#income-notices-error')
            error.wait_for(state='visible')
            page.unroute(notice_url, fail)
            with page.expect_response(lambda response: response.url == notice_url):
                page.clock.fast_forward(61000)
            page.wait_for_function("() => document.querySelector('#income-alert-summary').textContent.includes('3 income notices')")
            assert error.is_hidden(), 'A recovered poll must clear its old error'
            popup = page.locator('#income-notices-dialog')
            assert popup.locator('.income-notice').count() == 3, 'Recovery must replace the empty error view'
            # Mark-as-read succeeds, but the subsequent GET fails. It must be
            # possible to retry/refresh without leaving a permanently disabled button.
            page.route(notice_url, fail)
            with page.expect_response(lambda response: response.url.endswith('/income-notifications/read')) as acknowledged:
                popup.get_by_role('button', name='Mark as read', exact=True).first.click()
            assert acknowledged.value.status == 204
            error.wait_for(state='visible')
            assert popup.get_by_role('button', name='Mark as read', exact=True).first.is_enabled(), 'Failed refresh must not strand the action'
            page.unroute(notice_url, fail)
            with page.expect_response(lambda response: response.url == notice_url):
                page.clock.fast_forward(61000)
            page.wait_for_function("() => document.querySelectorAll('#income-notices-list .income-notice').length === 2")
            assert error.is_hidden()
            assert not errors, errors
            fixture.bank.assert_not_called()
            assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
            page.close()
            print(f'PASS {width}px: notice failures, recovery, acknowledgement-refresh retry and stable actions')
        finally:
            fixture.doCleanups()
    browser.close()
