"""Adversarial popup checks in Chromium, with synthetic data and no bank traffic."""
import sys
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from app import app
from flask import render_template
from playwright.sync_api import sync_playwright

urls = [f'/dashboard/transactions/11111111-1111-4111-8111-111111111111/22222222-2222-4222-8222-222222222222/00000000-0000-4000-8000-{i:012d}/confirm' for i in range(2)]
rows = [dict(number=i + 1, date='05 Oct 2026', day='Today', counterparty='Example payment',
             reference='Example reference', classification='Expense', origin='automatic',
             edit_url='/classification', confirm_url=urls[i], confirmed=False,
             direction='OUT', amount='GBP 12.50', status='Settled') for i in range(2)]
with app.test_request_context('/dashboard'):
    html = render_template('dashboard.html', rows=rows,
        page=SimpleNamespace(page=1, total=2, pages=1, has_prev=False, has_next=False),
        accounts=[], selected_account=None, account_uid=None, error=None,
        current_user=SimpleNamespace(is_authenticated=True))

with sync_playwright() as playwright:
    browser = playwright.chromium.launch(headless=True)
    for width in (320, 390, 1280):
        context = browser.new_context(viewport={'width': width, 'height': 740})
        page = context.new_page()
        errors = []
        page.on('pageerror', lambda failure: errors.append(str(failure)))
        state = {'remaining': 2, 'version': 'a' * 64, 'posted': [], 'mode': 'normal', 'held': None, 'fail_load': False}
        def respond(route):
            path = urlparse(route.request.url).path
            if path.startswith('/static/'):
                route.fulfill(path=str(ROOT / path.lstrip('/')))
            elif path == '/dashboard':
                route.fulfill(content_type='text/html', body=html)
            elif path == '/dashboard/review':
                if state['fail_load']:
                    route.fulfill(status=503, json={'error': 'Unavailable'})
                    return
                remaining = state['remaining']
                record = None if not remaining else {
                    'version': state['version'], 'confirm_url': urls[2 - remaining], 'edit_url': '/classification',
                    'details': [['Payment', '<img src=x onerror="window.injected=true">'],
                                ['Reference', ('Long bank reference ' * 60)], ['Amount', 'GBP 12.50'],
                                ['Notes', '<script>window.injected=true</script>']],
                }
                route.fulfill(json={'remaining': remaining, 'transaction': record})
            elif path.endswith('/confirm'):
                assert route.request.headers.get('x-csrftoken')
                state['posted'].append(route.request.post_data_json['version'])
                if state['mode'] == 'login-redirect':
                    route.fulfill(status=302, headers={'Location': '/login'})
                    return
                if state['mode'] == 'bad-acknowledgement':
                    route.fulfill(json={'confirmed': False})
                    return
                if state['mode'] == 'hold':
                    state['held'] = route
                    return
                if state['mode'] == 'conflict':
                    state['version'] = 'b' * 64
                    state['mode'] = 'normal'
                    route.fulfill(status=409, json={'error': 'Changed details'})
                    return
                if state['mode'] == 'failure':
                    route.fulfill(status=503, json={'error': 'Unavailable'})
                    return
                state['remaining'] -= 1
                route.fulfill(json={'confirmed': True})
            elif path == '/dashboard/sync-status':
                stamp = datetime.now(timezone.utc).isoformat()
                route.fulfill(json={'run': {'status': 'completed', 'finished_at': stamp, 'last_success_at': stamp}})
            elif path == '/dashboard/balances':
                route.fulfill(json={'balances': [], 'totals': []})
            else:
                route.abort()
        page.route('**/*', respond)
        page.goto('http://toms.test/dashboard')
        page.wait_for_function("document.querySelector('#transaction-review').open")
        assert page.locator('#review-details img, #review-details script').count() == 0
        assert not page.evaluate('Boolean(window.injected)')
        box = page.locator('#transaction-review').bounding_box()
        assert box['x'] >= 0 and box['x'] + box['width'] <= width and box['height'] <= 740
        page.locator('#review-close').click()
        assert not page.locator('#transaction-review').is_visible()
        assert state['remaining'] == 2 and not state['posted']
        page.locator('#review-open').click()
        page.wait_for_function("document.querySelector('#transaction-review').open")
        page.keyboard.press('Escape')
        assert not page.locator('#transaction-review').is_visible()
        page.locator('#review-open').click()
        page.wait_for_function("document.querySelector('#transaction-review').open")

        state['mode'] = 'conflict'
        page.locator('#review-confirm').click()
        page.wait_for_function("document.querySelector('#review-error').textContent.includes('Details changed')")
        assert page.locator('.is-unconfirmed').count() == 2 and state['remaining'] == 2
        state['mode'] = 'failure'
        page.locator('#review-confirm').click()
        page.wait_for_function("document.querySelector('#review-error').textContent.includes('Unable to complete')")
        assert not page.locator('#review-confirm').is_disabled()
        assert state['remaining'] == 2

        for mode in ('login-redirect', 'bad-acknowledgement'):
            state['mode'] = mode
            with page.expect_request(lambda request: request.url.endswith('/confirm')):
                page.locator('#review-confirm').click()
            page.wait_for_function("!document.querySelector('#review-confirm').disabled")
            assert page.locator('.is-confirmed').count() == 0 and state['remaining'] == 2

        # Hold the request open so closing cannot be undone by its eventual response.
        state['mode'] = 'hold'
        page.locator('#review-confirm').click()
        for _ in range(50):
            if state['held'] is not None:
                break
            page.wait_for_timeout(20)
        assert state['held'] is not None
        assert page.locator('#review-confirm').is_disabled()
        before = len(state['posted'])
        page.locator('#review-confirm').dispatch_event('click')
        assert len(state['posted']) == before
        page.locator('#review-close').click()
        state['remaining'] -= 1
        state['held'].fulfill(json={'confirmed': True})
        page.wait_for_function("document.querySelectorAll('.is-confirmed').length === 1")
        page.wait_for_function("document.querySelector('#review-count').textContent.startsWith('1')")
        assert not page.locator('#transaction-review').is_visible()

        # A saved confirmation with a failed next fetch must not reconfirm the old item.
        state['mode'] = 'normal'
        page.locator('#review-open').click()
        page.wait_for_function("document.querySelector('#transaction-review').open")
        state['fail_load'] = True
        page.locator('#review-confirm').click()
        page.wait_for_function("document.querySelector('#review-error').textContent.includes('Confirmation saved')")
        assert page.locator('#review-confirm').is_disabled()
        assert page.locator('#review-retry').is_visible()
        assert state['remaining'] == 0 and page.locator('.is-confirmed').count() == 2
        state['fail_load'] = False
        page.locator('#review-retry').click()
        page.wait_for_function("!document.querySelector('#transaction-review').open")
        assert not page.locator('#review-open').is_visible()

        # Empty and failed initial lists are handled without a usable stale confirmation.
        page.reload()
        page.wait_for_function("document.querySelector('#review-open').hidden")
        assert not page.locator('#transaction-review').is_visible()
        state['fail_load'] = True
        page.reload()
        page.wait_for_function("document.querySelector('#transaction-review').open")
        assert page.locator('#review-retry').is_visible() and page.locator('#review-confirm').is_disabled()
        assert not errors, errors
        print(f'PASS: {width}px dismissal, escaping, overflow, stale versions, failures, retries and duplicate clicks')
        context.close()
    browser.close()
