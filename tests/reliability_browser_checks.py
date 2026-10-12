"""Conflict recovery and trusted-input session renewal, using isolated records."""
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
from browser_owner import install_owner_cookie
from services.web.test_data import sample_id

logging.getLogger('werkzeug').setLevel(logging.ERROR)

with sync_playwright() as playwright:
    browser = playwright.chromium.launch(headless=True)
    for width in (320, 390, 768, 1280):
        fixture = TestDataTests()
        try:
            fixture.setUp()
            assert fixture.toggle(True).status_code == 303
            server = make_server('127.0.0.1', 0, app, threaded=True)
            worker = Thread(target=server.serve_forever, daemon=True)
            worker.start()
            fixture.addCleanup(server.server_close)
            fixture.addCleanup(worker.join, 5)
            fixture.addCleanup(server.shutdown)
            origin = f'http://127.0.0.1:{server.server_port}'
            context = browser.new_context(viewport={'width': width, 'height': 900})
            install_owner_cookie(context, fixture, origin)
            first, older = context.new_page(), context.new_page()
            identity = sample_id(22)
            path = f'{origin}/dashboard/income-streams?configure={identity}'
            first.goto(path)
            older.goto(path)
            form = first.locator(f'#configure-{identity}')
            form.locator('[name="name"]').fill('New settings from first tab')
            with first.expect_navigation():
                form.get_by_role('button', name='Save changes', exact=True).click()
            form = older.locator(f'#configure-{identity}')
            form.locator('[name="name"]').fill('Retained older-tab entry')
            with older.expect_response(lambda response: response.request.method == 'POST'
                                       and '/income-streams' in response.url) as saved:
                form.get_by_role('button', name='Save changes', exact=True).click()
            assert saved.value.status == 409
            form = older.locator(f'#configure-{identity}')
            assert form.locator('[name="name"]').input_value() == 'Retained older-tab entry'
            assert form.evaluate('(dialog) => dialog.scrollWidth <= dialog.clientWidth')
            if width in (390, 1280):
                older.screenshot(path=f'/tmp/toms-stream-conflict-{width}.png', full_page=True)
            form.get_by_role('link', name='Reload current settings').click()
            assert older.locator(f'#configure-{identity} [name="name"]').input_value() == 'New settings from first tab'
            assert older.evaluate('document.documentElement.scrollWidth <= innerWidth')
            context.close()
            fixture.bank.assert_not_called()
        finally:
            fixture.doCleanups()

        page = browser.new_page(viewport={'width': width, 'height': 900})
        page.clock.install()
        requests = []
        def respond(route):
            path = route.request.url.split('toms.test', 1)[1]
            if path.startswith('/static/'):
                route.fulfill(path=str(ROOT / path.lstrip('/')))
            elif path == '/session/activity':
                requests.append(route.request)
                failure_status = 400 if width == 320 else 401
                route.fulfill(status=failure_status if len(requests) == 3 else 204, headers={'X-CSRFToken': 'refreshed-csrf'})
            else:
                route.fulfill(content_type='text/html', body='''<!doctype html><html><body>
                <span id="session-activity" data-url="/session/activity" data-login-url="/login" data-csrf="test-csrf" hidden></span>
                <p id="session-activity-error" role="alert" hidden></p><input aria-label="Unsaved entry" value="Keep my work"><input type="hidden" name="csrf_token" value="test-csrf">
                <script src="/static/js/session-activity.js" defer></script></body></html>''')
        page.route('http://toms.test/**', respond)
        page.goto('http://toms.test/')
        page.evaluate("document.dispatchEvent(new KeyboardEvent('keydown', {key: 'a'}))")
        page.clock.fast_forward(120000)
        assert len(requests) == 0
        with page.expect_response('http://toms.test/session/activity'):
            page.get_by_label('Unsaved entry').click()
        assert len(requests) == 1
        assert requests[0].headers['x-csrftoken'] == 'test-csrf'
        page.keyboard.press('ArrowRight')
        page.clock.fast_forward(60001)
        assert len(requests) == 1
        with page.expect_response('http://toms.test/session/activity'):
            page.keyboard.press('ArrowRight')
        assert requests[1].headers['x-csrftoken'] == 'refreshed-csrf'
        assert page.locator('[name="csrf_token"]').input_value() == 'refreshed-csrf'
        page.clock.fast_forward(60001)
        with page.expect_response('http://toms.test/session/activity'):
            page.keyboard.press('ArrowRight')
        page.get_by_role('link', name='Sign in again').wait_for()
        assert page.get_by_label('Unsaved entry').input_value() == 'Keep my work'
        page.clock.fast_forward(60001)
        page.keyboard.press('ArrowRight')
        assert len(requests) == 3
        page.close()
        print(f'PASS {width}px: stale settings recovery and trusted activity/expiry without losing entries')
    browser.close()
