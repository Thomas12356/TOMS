"""Chromium tax layout, mobile navigation, incomplete states and exact figures."""
import sys
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'tests'))
from app import app
from flask import render_template
from playwright.sync_api import sync_playwright
from services.tax.estimate import estimate_streams, rounded_minor
from services.tax.rules import reviewed_rules
from services.transactions.income_streams import STREAM_KINDS
from routes.dashboard import format_amount
from test_tax_estimate import stream

rules = reviewed_rules()
states = {
    'complete': [stream(4000000, 'employed', 'paye_estimate'), stream(2000000, name='Independent work with a long readable name')],
    'cis': [stream(3000000, 'cis', 'manual', 600000)],
    'unknown': [stream(None, 'cis', 'unknown', name='<script>alert(1)</script>')],
    'foreign': [stream(3000000, expected_gross_currency='EUR')],
    'empty': [],
    'received': [stream(3000000, 'cis')],
}
with sync_playwright() as playwright:
    browser = playwright.chromium.launch(headless=True)
    for state, streams in states.items():
        with app.test_request_context('/dashboard/tax-estimate'):
            html = render_template('tax_estimate.html', estimate=estimate_streams(streams, rules, credits={streams[0].id: 600000} if state == 'cis' else {streams[0].id: 100000} if state == 'received' else {}, received={streams[0].id: 1500000} if state == 'received' else {}), rules=rules,
                                   status=None, format_amount=format_amount, rounded_minor=rounded_minor,
                                   stream_kinds=STREAM_KINDS,
                                   current_user=SimpleNamespace(is_authenticated=True))
        for width in (320, 390, 640, 768, 1280):
            page = browser.new_page(viewport={'width': width, 'height': 900})
            errors = []
            page.on('pageerror', lambda error: errors.append(str(error)))
            def respond(route):
                path = urlparse(route.request.url).path
                if path.startswith('/static/'):
                    route.fulfill(path=str(ROOT / path.lstrip('/')))
                else:
                    route.fulfill(body=html, content_type='text/html')
            page.route('http://toms.test/**', respond)
            page.goto('http://toms.test/dashboard/tax-estimate')
            assert page.evaluate('document.documentElement.scrollWidth <= innerWidth'), (state, width)
            assert page.get_by_role('heading', name='Tax estimate', exact=True).is_visible()
            if state == 'complete':
                assert page.locator('.tax-reserve').inner_text().endswith('GBP 6,391.80')
                assert '31.96%' in page.get_by_role('region', name='Your set-aside plan', exact=True).inner_text()
                assert page.locator('.tax-stream').count() == 2
            elif state == 'cis':
                assert 'GBP 1,468.20' in page.get_by_role('region', name='Your set-aside plan', exact=True).inner_text()
            elif state == 'received':
                assert page.locator('.received-reserve').inner_text() == 'GBP 1,265.90'
                assert 'GBP 1,045.80' in page.locator('.tax-totals').inner_text()
            else:
                assert page.get_by_role('heading', name='Finish your estimate').is_visible()
                assert page.locator('.tax-plan').count() == 0
            if width <= 640:
                page.get_by_role('button', name='Toggle navigation').click()
            assert page.locator('nav a[aria-current=page]').inner_text() == 'Tax estimate'
            assert page.get_by_role('link', name='Income streams', exact=True).is_visible()
            assert not errors, (state, width, errors)
            if state == 'complete' and width in (390, 1280):
                page.screenshot(path=f'/tmp/toms-tax-estimate-{width}.png', full_page=True)
            page.close()
    browser.close()
print('Tax estimate: six states at five widths; layout, figures, navigation and JavaScript passed.')
