"""Chromium system-page layouts, escaping, mobile nav and action-log controls."""
import sys
from pathlib import Path
from datetime import datetime, timezone
from types import SimpleNamespace
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from flask import render_template
from playwright.sync_api import sync_playwright
from app import app
from services.system.overview import format_bytes, format_timestamp
from services.web.activity import ACTIONS

now = datetime(2026, 10, 8, 12, tzinfo=timezone.utc)
rows = [SimpleNamespace(action='classification.save', occurred_at=now, sample_data=False,
                        record_id='<script>PRIVATE</script>'),
        SimpleNamespace(action='shift.create', occurred_at=now, sample_data=True, record_id='a' * 160)]
backups = dict(count=2, total_bytes=1048576, error=None, rows=[
    dict(name='toms-20261008T020000Z-12345678', size=524288, created=now, verified=now, state='Restore tested'),
    dict(name='toms-20261007T020000Z-12345678', size=524288, created=now, verified=None, state='Not restore-tested')])
with sync_playwright() as playwright:
    browser = playwright.chromium.launch(headless=True)
    for state in ('records', 'empty', 'error'):
        with app.test_request_context('/dashboard/system'):
            html = render_template('system.html', storage=dict(total=10485760, real=1048576, sample=524288),
                backups=backups if state == 'records' else dict(count=0, total_bytes=0, error=None, rows=[]),
                actions=SimpleNamespace(items=rows if state == 'records' else [], pages=2 if state == 'records' else 0,
                    page=1, has_prev=False, has_next=True, next_num=2), mode='all', labels=ACTIONS,
                format_bytes=format_bytes, format_timestamp=format_timestamp, error='System data is unavailable.' if state == 'error' else None,
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
            page.goto('http://toms.test/dashboard/system')
            assert page.evaluate('document.documentElement.scrollWidth <= innerWidth'), (state, width)
            assert page.get_by_role('heading', name='System management', exact=True).is_visible()
            if width <= 640:
                page.get_by_role('button', name='Toggle navigation').click()
            assert page.locator('nav a[aria-current=page]').inner_text() == 'System'
            if width <= 640:
                page.get_by_role('button', name='Toggle navigation').click()
            if state == 'records':
                assert page.locator('.backup-record').count() == 2
                assert page.locator('.activity-record').count() == 2
                page.get_by_text('Record ID', exact=True).first.click()
                assert '<script>PRIVATE</script>' in page.locator('.activity-record').first.inner_text()
                assert page.locator('script').count() == 1  # Navigation script only; escaped record ID.
                assert page.get_by_role('link', name='Older').get_attribute('href').endswith('page=2&mode=all')
            elif state == 'empty':
                assert page.get_by_text('No actions recorded yet.').is_visible()
            else:
                assert page.get_by_role('alert').is_visible()
            page.locator('#activity-mode').select_option('test')
            assert page.locator('#activity-mode').input_value() == 'test'
            assert not errors, (state, width, errors)
            if state == 'records' and width in (390, 1280):
                page.screenshot(path=f'/tmp/toms-system-{width}.png', full_page=True)
            page.close()
    browser.close()
print('System page: records, empty/error states and mobile navigation passed at five widths.')
