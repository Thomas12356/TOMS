"""Optional Chromium checks using synthetic data; never contacts the bank.

Run with Playwright available: python tests/browser_checks.py
"""
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from app import app
from flask import render_template
from playwright.sync_api import sync_playwright

rows = [dict(number=i + 1, date='05 Oct 2026', day='Today',
             counterparty='Example payment with a longer merchant name',
             reference='A long reference which should wrap without breaking the mobile layout',
             classification='Transfer between your own accounts or spaces',
             origin='manual' if i else 'automatic', edit_url='/classification', confirmed=False,
             confirm_url=f'/dashboard/transactions/11111111-1111-4111-8111-111111111111/22222222-2222-4222-8222-222222222222/00000000-0000-4000-8000-{i:012d}/confirm',
             direction='IN' if i else 'OUT', amount='GBP 1,234.56', status='Settled') for i in range(3)]
pagination = SimpleNamespace(total=3, pages=1, page=1, has_prev=False, has_next=False)
account = SimpleNamespace(account_uid='11111111-1111-4111-8111-111111111111', name='Current account', currency='GBP')
with app.test_request_context('/dashboard'):
    html = render_template('dashboard.html', rows=rows, page=pagination, error=None,
                           accounts=[account], selected_account=None, account_uid=None,
                           current_user=SimpleNamespace(is_authenticated=True))

with sync_playwright() as playwright:
    browser = playwright.chromium.launch(headless=True)
    for width in (320, 390, 640, 768, 1280):
        context = browser.new_context(viewport={'width': width, 'height': 850})
        page = context.new_page()
        errors = []
        page.on('pageerror', lambda error: errors.append(str(error)))
        state = {'posts': [], 'synced': False, 'stale': False}
        def respond(route):
            path = urlparse(route.request.url).path
            if path.startswith('/static/'):
                file = ROOT / path.lstrip('/')
                route.fulfill(path=str(file))
            elif path == '/dashboard/review':
                remaining = state.get('review_remaining', 0)
                record = None if not remaining else {
                    'version': 'a' * 64, 'confirm_url': rows[3 - remaining]['confirm_url'],
                    'edit_url': '/classification', 'details': [['Payment', 'Example payment'], ['Amount', 'GBP 1,234.56']],
                }
                route.fulfill(json={'remaining': remaining, 'transaction': record})
            elif path.endswith('/confirm'):
                assert route.request.method == 'POST'
                assert route.request.headers.get('x-csrftoken')
                assert route.request.post_data_json == {'version': 'a' * 64}
                state['review_remaining'] -= 1
                route.fulfill(json={'confirmed': True})
            elif path == '/dashboard/sync-status':
                now = datetime.now(timezone.utc)
                last = now - timedelta(minutes=2) if state['stale'] and not state['synced'] else now
                route.fulfill(json={'run': {'status': 'completed', 'started_at': now.isoformat(),
                    'finished_at': now.isoformat() if state['stale'] else last.isoformat(),
                    'last_success_at': last.isoformat()}})
            elif path == '/dashboard/sync':
                state['posts'].append(route.request.url)
                assert route.request.headers.get('x-csrftoken')
                state['synced'] = True
                route.fulfill(status=202, json={'started': True})
            elif path == '/dashboard/balances':
                route.fulfill(json={'balances': [], 'totals': [{'name': 'Total balance · All accounts', 'amount': 'GBP 123.45', 'error': None}]})
            elif path == '/dashboard':
                route.fulfill(content_type='text/html', body=html)
            else:
                route.abort()
        page.route('**/*', respond)
        page.goto('http://toms.test/dashboard')
        page.wait_for_function("() => document.querySelector('#sync-now').disabled === false")
        assert not state['posts'], 'Fresh data must not start an import'
        assert page.locator('.balance-card h3').inner_text() == 'Total balance · All accounts'
        assert page.evaluate('document.documentElement.scrollWidth <= innerWidth'), f'Horizontal overflow at {width}px'
        navigation = page.locator('#main-navigation')
        if width <= 640:
            assert not navigation.is_visible()
            page.locator('#navigation-toggle').click()
            assert navigation.is_visible()
            navigation.locator('a').first.focus()
            page.keyboard.press('Escape')
            assert not navigation.is_visible()
            boxes = page.locator('[data-sync-now]').evaluate_all('(buttons) => buttons.map(b => ({width:b.getBoundingClientRect().width,height:b.getBoundingClientRect().height}))')
            assert boxes[0] == boxes[1] and boxes[0]['height'] >= 44
            cards = page.locator('tbody tr').evaluate_all('(rows) => rows.map(r => ({top:r.getBoundingClientRect().top,bottom:r.getBoundingClientRect().bottom}))')
            assert cards[1]['top'] - cards[0]['bottom'] >= 7
            tip = page.locator('.mobile-classification-tip .tip-trigger').first
            tip.click()
            tooltip = page.locator('.mobile-classification-tip .classification-tooltip').first
            assert tooltip.is_visible()
            tip_box = tooltip.bounding_box()
            assert tip_box['x'] >= 0 and tip_box['x'] + tip_box['width'] <= width
            if width == 390:
                page.screenshot(path='/tmp/toms-mobile-review.png', full_page=True)
        else:
            assert navigation.is_visible()
            assert not page.locator('#navigation-toggle').is_visible()
        footer = page.locator('.sync-footer').bounding_box()
        assert abs(footer['y'] + footer['height'] - 850) < 2
        with page.expect_response(lambda response: '/dashboard/sync?force=1' in response.url):
            page.locator('[data-sync-now]').first.click()
        page.wait_for_function("() => document.querySelector('#sync-message').textContent === 'Up to date.'", timeout=10000)
        assert any('force=1' in url for url in state['posts'])
        # A recent partial import must not hide an older full-sync timestamp.
        state.update(posts=[], synced=False, stale=True)
        with page.expect_response(lambda response: '/dashboard/sync?force=0' in response.url):
            page.reload()
        page.wait_for_function("() => document.querySelector('#sync-message').textContent === 'Up to date.'", timeout=10000)
        assert any('force=0' in url for url in state['posts'])
        assert not errors, errors
        if width == 390:
            state['review_remaining'] = 3
            page.reload()
            dialog = page.locator('#transaction-review')
            page.wait_for_function("() => document.querySelector('#transaction-review').open")
            assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
            page.keyboard.press('Escape')
            assert not dialog.is_visible() and state['review_remaining'] == 3
            assert page.locator('.is-unconfirmed').count() == 3
            page.locator('#review-open').click()
            page.wait_for_function("() => document.querySelector('#transaction-review').open")
            for remaining in (2, 1, 0):
                with page.expect_response(lambda response: response.url.endswith('/confirm')):
                    page.locator('#review-confirm').click()
                if remaining:
                    page.wait_for_function('(count) => document.querySelector("#review-count").textContent.startsWith(String(count))', arg=remaining)
                else:
                    page.wait_for_function("() => !document.querySelector('#transaction-review').open")
            assert page.locator('.is-confirmed').count() == 3
            print('PASS: dismissible mobile popup, individual confirmation and persistent labels')
        print(f'PASS: {width}px layout, navigation, totals, tooltip, footer and sync flow')
        context.close()
    browser.close()
