"""Task discovery, filtering and completion on disposable owner/sample records."""
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
    for width in (320, 390, 768, 1024, 1280):
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
            cookie = fixture.client.get_cookie(app.config['SESSION_COOKIE_NAME'])
            page.context.add_cookies([dict(name=cookie.key, value=cookie.value, url=origin)])
            requests, errors = [], []
            page.on('request', lambda request: requests.append(request.url))
            page.on('pageerror', lambda error: errors.append(str(error)))
            page.goto(origin + '/dashboard/overview')
            assert page.get_by_role('heading', name='Needs attention', exact=False).is_visible()
            page.locator('#home-task-filter').select_option('payments')
            assert page.get_by_role('button', name='Show actions').bounding_box()['height'] >= 44
            with page.expect_navigation():
                page.get_by_role('button', name='Show actions').click()
            assert 'tasks=payments' in page.url
            assert page.url.endswith('#home-actions')
            cards = page.locator('.home-action-list .home-action')
            before = cards.count()
            assert before >= 1
            transfer = cards.filter(has_text='Demo savings transfer')
            assert transfer.count() == 1
            assert page.evaluate('document.documentElement.scrollWidth <= innerWidth'), width
            if width in (390, 1280):
                page.screenshot(path=f'/tmp/toms-home-actions-{width}.png', full_page=True)
            with page.expect_navigation():
                transfer.get_by_role('link', name='Review this payment').click()
            assert '/review' in page.url
            with page.expect_navigation():
                page.get_by_role('button', name='Save classification and continue').click()
            assert 'step=check' in page.url
            with page.expect_navigation():
                page.get_by_role('button', name='Confirm these saved details and continue').click()
            assert page.url.endswith('/dashboard/overview?tasks=payments#home-actions')
            assert page.locator('.home-action-list .home-action').count() == before - 1
            assert page.locator('.home-action-list').get_by_text('Demo savings transfer', exact=False).count() == 0
            assert fixture.live_value('SELECT count(*) FROM SCHEMA.transactions WHERE confirmed_at IS NOT NULL') == 0
            # Category filtering is a plain GET form, with no JavaScript needed.
            context = browser.new_context(java_script_enabled=False, viewport={'width': width, 'height': 900})
            context.add_cookies([dict(name=cookie.key, value=cookie.value, url=origin)])
            plain = context.new_page()
            plain.goto(origin + '/dashboard/overview')
            plain.locator('#home-task-filter').select_option('imports')
            with plain.expect_navigation():
                plain.get_by_role('button', name='Show actions').click()
            assert 'tasks=imports' in plain.url
            assert plain.locator('#home-actions').get_by_text('No actions in this category.', exact=True).is_visible()
            assert plain.evaluate('document.documentElement.scrollWidth <= innerWidth'), width
            context.close()
            assert not any('/balances' in url or url.endswith('/sync') for url in requests), requests
            fixture.start_sync.assert_not_called()
            fixture.bank.assert_not_called()
            assert not errors, errors
            page.close()
            print(f'PASS {width}px: task filtering, exact payment action, completion, sample isolation and no-JavaScript empty category')
        finally:
            fixture.doCleanups()
    browser.close()
