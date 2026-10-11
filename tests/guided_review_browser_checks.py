"""Real filtered review journeys, conflict recovery and JavaScript-free forms."""
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
from services.web.test_data import sample_id

logging.getLogger('werkzeug').setLevel(logging.ERROR)
with sync_playwright() as playwright:
    browser = playwright.chromium.launch(headless=True)
    for width in (320, 390, 768, 1280):
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
            context = browser.new_context(viewport={'width': width, 'height': 900})
            fixture.addCleanup(context.close)
            cookie = fixture.client.get_cookie(app.config['SESSION_COOKIE_NAME'])
            context.add_cookies([dict(name=cookie.key, value=cookie.value, url=origin)])
            page = context.new_page()
            errors = []
            page.on('pageerror', lambda error: errors.append(str(error)))
            def layout():
                assert page.evaluate('document.documentElement.scrollWidth <= innerWidth'), (width, page.url)
            def click(control):
                with page.expect_navigation():
                    control.click()
                layout()
            page.goto(origin + '/dashboard?q=Demo+employer&classification=income&attention=unconfirmed')
            layout()
            assert not page.locator('#transaction-review').is_visible()
            assert page.locator('#review-open').inner_text() == 'Review 1 transaction'
            assert page.locator('tbody tr').count() == 1
            page.screenshot(path=f'/tmp/toms-filtered-ledger-{width}.png', full_page=True)
            click(page.locator('#review-open'))
            assert 'q=Demo+employer' in page.url
            page.locator('#review-notes').fill('Checked the payslip')
            click(page.get_by_role('button', name='Save classification and continue'))
            page.locator('#gross').fill('3333.34')
            click(page.get_by_role('button', name='Save income and check'))
            assert 'Amounts must reconcile' in page.get_by_role('alert').inner_text()
            assert page.locator('#gross').input_value() == '3333.34'
            page.locator('#gross').fill('3333.33')
            click(page.get_by_role('button', name='Save income and check'))
            assert 'GBP 457.17' in page.locator('#main-content').inner_text()
            assert 'GBP 76.16' in page.locator('#main-content').inner_text()
            stale = context.new_page()
            stale.goto(page.url)
            click(page.get_by_role('link', name='2. Complete details', exact=True))
            page.locator('#source-name').fill('Updated payslip source')
            click(page.get_by_role('button', name='Save income and check'))
            with stale.expect_navigation() as navigation:
                stale.get_by_role('button', name='Confirm these saved details and continue').click()
            assert navigation.value.status == 409
            assert 'changed' in stale.get_by_role('alert').inner_text()
            assert 'Reload the current saved details' in stale.locator('#main-content').inner_text()
            stale.close()
            page.screenshot(path=f'/tmp/toms-guided-check-{width}.png', full_page=True)
            click(page.get_by_role('button', name='Confirm these saved details and continue'))
            assert page.get_by_role('heading', name='No transactions awaiting confirmation in this view').is_visible()
            click(page.get_by_role('link', name='Back to transactions'))
            assert 'q=Demo+employer' in page.url and 'attention=unconfirmed' in page.url
            assert page.locator('#review-open').inner_text() == 'Review 0 transactions'
            assert page.get_by_role('heading', name='No matching transactions').is_visible()
            page.goto(origin + '/dashboard?q=Demo+groceries&classification=expense&attention=unconfirmed')
            click(page.locator('#review-open'))
            click(page.get_by_role('button', name='Save classification and continue'))
            page.locator('#expense-stream').select_option(sample_id(22))
            page.locator('#expense-tax-status').select_option('deduction')
            page.locator('#expense-amount').fill('4.00')
            page.locator('#expense-purpose').fill('<script>Business supplies</script>')
            page.get_by_role('checkbox').check()
            click(page.get_by_role('button', name='Save expense and check'))
            assert '<script>Business supplies</script>' in page.locator('#main-content').inner_text()
            assert page.locator('script:not([src])').count() == 0
            assert 'Tax deduction' in page.locator('#main-content').inner_text()
            assert 'GBP 4.00' in page.locator('#main-content').inner_text()
            click(page.get_by_role('button', name='Confirm these saved details and continue'))
            assert page.get_by_role('heading', name='No transactions awaiting confirmation in this view').is_visible()
            # Every step, including filtered selection and confirmation, also
            # works with JavaScript disabled and without the quick-review dialog.
            plain = browser.new_context(java_script_enabled=False, viewport={'width': width, 'height': 900})
            fixture.addCleanup(plain.close)
            plain.add_cookies([dict(name=cookie.key, value=cookie.value, url=origin)])
            simple = plain.new_page()
            simple.goto(origin + '/dashboard')
            simple.locator('#ledger-search').fill('Demo savings transfer')
            simple.locator('#ledger-classification').select_option('internal_transfer')
            simple.locator('#ledger-attention').select_option('unconfirmed')
            with simple.expect_navigation():
                simple.get_by_role('button', name='Apply filters').click()
            assert simple.locator('#review-open').inner_text() == 'Review 1 transaction'
            with simple.expect_navigation(): simple.locator('#review-open').click()
            with simple.expect_navigation(): simple.get_by_role('button', name='Save classification and continue').click()
            assert 'step=check' in simple.url
            with simple.expect_navigation(): simple.get_by_role('button', name='Confirm these saved details and continue').click()
            assert simple.get_by_role('heading', name='No transactions awaiting confirmation in this view').is_visible()
            assert not errors, errors
            fixture.bank.assert_not_called()
            assert fixture.live_value('SELECT count(*) FROM SCHEMA.transaction_income') == 0
            print(f'PASS {width}px: filters, PAYE/NI details, stale confirmation, expense claim, preserved context and JavaScript-free transfer review')
        finally:
            fixture.doCleanups()
    browser.close()
