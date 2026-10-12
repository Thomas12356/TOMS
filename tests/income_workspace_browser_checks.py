"""Real owner workflow for stream dialogs, expenses, notices and earmarks."""
import logging
import sys
from pathlib import Path
from threading import Thread

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'tests'))
from playwright.sync_api import sync_playwright
from sqlalchemy import text
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
            fixture.start_sync.return_value = False
            assert fixture.toggle(True).status_code == 303
            original_sources = fixture.live_value('SELECT count(*) FROM SCHEMA.income_streams')
            server = make_server('127.0.0.1', 0, app, threaded=True)
            worker = Thread(target=server.serve_forever, daemon=True)
            worker.start()
            fixture.addCleanup(server.server_close)
            fixture.addCleanup(worker.join, 5)
            fixture.addCleanup(server.shutdown)
            origin = f'http://127.0.0.1:{server.server_port}'
            page = browser.new_page(viewport={'width': width, 'height': 900})
            page.clock.install()
            install_owner_cookie(page.context, fixture, origin)
            errors = []
            page.on('pageerror', lambda error: errors.append(str(error)))
            def layout():
                assert page.evaluate('document.documentElement.scrollWidth <= innerWidth'), width
                for dialog in page.locator('dialog[open]').all():
                    assert dialog.evaluate('(d) => d.scrollWidth <= d.clientWidth'), width
            def go(path='/dashboard/income-streams'):
                page.goto(origin + path)
                layout()
            def submit(button):
                with page.expect_navigation() as navigation: button.click()
                if navigation.value and navigation.value.status >= 400:
                    Path('/tmp/toms-income-workspace-submit-error.html').write_text(page.content())
                    raise AssertionError(f'Owner form returned {navigation.value.status} at {width}px; see temporary sample HTML')
                layout()
            go('/dashboard/income-streams?setup=1')
            page.locator('#source-name').fill('Browser staged source <img src=x>')
            page.locator('#source-kind').select_option('self_employed')
            submit(page.get_by_role('button', name='Continue to income source', exact=True))
            workspace_url = page.url
            assert 'Expected income is unknown' in page.locator('main').inner_text()
            assert page.locator('main img').count() == 0
            assert page.get_by_role('link', name='Open work records', exact=True).count() == 0
            with page.expect_navigation():
                page.get_by_role('link', name='Set up forecast or shifts', exact=True).click()
            page.locator('dialog[open] [name=expected_gross]').fill('0')
            submit(page.get_by_role('button', name='Save changes', exact=True))
            page.goto(workspace_url)
            layout()
            assert 'GBP 0.00 gross for the tax year' in page.locator('main').inner_text()
            assert page.get_by_role('link', name='Open work records', exact=True).is_visible()
            assert 'not added together as extra income' in page.locator('main').inner_text()
            page.screenshot(path=f'/tmp/toms-source-workspace-{width}.png', full_page=True)
            go('/dashboard/account-money')
            assert 'Bank balances have not been requested.' in page.locator('#account-balances').inner_text()
            page.get_by_role('button', name='Refresh bank balances', exact=True).click()
            page.wait_for_function("() => document.querySelector('#account-balances').textContent.includes('After earmarks:')")
            assert 'GBP 2,450.75' in page.locator('#account-balances').inner_text()
            assert 'GBP 2,150.75' in page.locator('#account-balances').inner_text()
            go()
            assert page.locator('#income-payments-dialog').is_hidden()
            page.get_by_role('link', name='Show all income', exact=True).click()
            page.locator('#income-payments-dialog').wait_for(state='visible')
            assert page.locator('#income-payments-dialog .stream-payment').count() == 3
            layout()
            page.keyboard.press('Escape')
            assert page.get_by_role('link', name='Show all income', exact=True).evaluate('(el) => el === document.activeElement')
            freelance = page.locator('.stream-manage', has=page.get_by_role('heading', name='Demo freelance work', exact=True))
            assert freelance.get_by_role('link', name='Add work', exact=True).is_visible()
            assert freelance.get_by_role('link', name='Add overtime', exact=True).count() == 0
            employed = page.locator('.stream-manage', has=page.get_by_role('heading', name='Demo employment', exact=True))
            assert employed.get_by_role('link', name='Add overtime', exact=True).is_visible()
            freelance.get_by_role('link', name='Configure source', exact=True).click()
            assert page.get_by_role('heading', name='Configure Demo freelance work', exact=True).is_visible()
            layout()
            page.keyboard.press('Escape')
            freelance.get_by_text('More actions', exact=True).click()
            freelance.get_by_role('link', name='Show income', exact=True).click()
            page.locator('#income-payments-dialog').wait_for(state='visible')
            assert page.locator('#income-payments-dialog .stream-payment').count() == 1
            assert 'GBP 850.00' in page.locator('#income-payments-dialog').inner_text()
            page.keyboard.press('Escape')
            freelance.get_by_role('link', name='View deductions and expenses', exact=True).click()
            page.locator('#stream-deductions-dialog').wait_for(state='visible')
            assert 'Sample rent expense — tracking only' in page.locator('#stream-deductions-dialog').inner_text()
            layout()
            page.keyboard.press('Escape')
            key = ':'.join((sample_id(1), sample_id(11), sample_id(34)))
            go('/dashboard/deductions?expense=' + key)
            assert page.locator('#expense-tax-status').input_value() == 'tracking'
            assert page.locator('#expense-eligibility').is_hidden()
            page.locator('#expense-stream').select_option(sample_id(22))
            page.locator('#expense-amount').fill('5')
            page.locator('#expense-purpose').fill('Tools <img src=x onerror=alert(1)>')
            submit(page.get_by_role('button', name='Save expense', exact=True))
            go('/dashboard/income-streams?view=deductions&stream=' + sample_id(22))
            if 'Tools <img src=x onerror=alert(1)>' not in page.locator('#stream-deductions-dialog').inner_text():
                Path('/tmp/toms-income-workspace-record-error.html').write_text(page.content())
                raise AssertionError(f'Saved sample expense missing at {width}px; see temporary sample HTML')
            assert page.locator('#stream-deductions-dialog img').count() == 0
            page.keyboard.press('Escape')
            go('/dashboard/deductions?expense=' + key)
            page.locator('#expense-tax-status').select_option('deduction')
            assert page.locator('#expense-eligibility').is_visible()
            assert not page.locator('form:has(#expense-amount)').evaluate('(f) => f.checkValidity()')
            page.locator('#expense-eligibility input').check()
            submit(page.get_by_role('button', name='Save expense', exact=True))
            go()
            go('/dashboard/account-money')
            details = page.locator('.account-earmark').first
            details.locator('summary').click()
            details.locator('[name=tax]').fill('300')
            details.locator('[name=ni]').fill('70')
            submit(details.get_by_role('button', name='Save earmarks', exact=True))
            page.get_by_role('button', name='Refresh bank balances', exact=True).click()
            page.wait_for_function("() => document.querySelector('#account-balances').textContent.includes('GBP 2,080.75')")
            assert 'GBP 300.00 tax' in page.locator('#account-balances').inner_text()
            # Confirm every sample through the existing real review popup.
            go('/dashboard?account=all')
            assert not page.locator('#transaction-review').is_visible()
            if not page.locator('.expert-confirmation').evaluate('(d) => d.open'):
                page.locator('.expert-confirmation > summary').click()
            page.locator('#quick-review').click()
            page.wait_for_function("() => document.querySelector('#transaction-review').open")
            for _ in range(7):
                with page.expect_response(lambda response: '/confirm' in response.url and response.request.method == 'POST') as confirmed:
                    page.get_by_role('button', name='Confirm details', exact=True).click()
                assert confirmed.value.status == 200
                page.wait_for_function("() => !document.querySelector('#review-confirm').disabled || !document.querySelector('#transaction-review').open")
            page.wait_for_function("() => !document.querySelector('#transaction-review').open")
            go()
            page.locator('#income-alert-bar').wait_for(state='visible')
            page.get_by_role('button', name='View income notices', exact=True).click()
            popup = page.locator('#income-notices-dialog')
            assert popup.is_visible()
            assert popup.locator('.income-notice').count() == 3
            assert 'Set aside' in popup.inner_text()
            assert 'needs review' not in ' '.join(popup.locator('.income-notice').all_inner_texts())
            layout()
            for remaining in (2, 1, 0):
                popup.get_by_role('button', name='Mark as read', exact=True).first.click()
                page.wait_for_function('(count) => document.querySelectorAll("#income-notices-list .income-notice").length === count', arg=remaining)
            assert 'All income notices are read.' in popup.inner_text()
            page.keyboard.press('Escape')
            assert page.locator('#income-alert-bar').is_hidden()
            page.reload()
            assert page.locator('#account-balances').count() == 0
            assert page.locator('#income-alert-bar').is_hidden()
            page.wait_for_function("() => document.querySelector('#income-notices-list').textContent.includes('All income notices are read.')")
            # Simulate a newly imported payment after the page is already open.
            # Advancing the browser timer verifies the actual polling interval.
            with fixture.admin.begin() as connection:
                connection.execute(text(f"""INSERT INTO {fixture.demo_schema}.transactions
                    (account_uid, category_uid, feed_item_uid, amount_minor, currency,
                     direction, status, transaction_time, source_updated_at, source, counterparty_name, raw_payload)
                    VALUES (:account, :category, :item, 10000, 'GBP', 'IN', 'SETTLED', now() - interval '5 seconds', now(),
                            'FASTER_PAYMENTS_IN', :payer, '{{}}')"""), dict(
                    account=sample_id(1), category=sample_id(11), item=sample_id(71), payer='<img src=x onerror=alert(1)>'))
            with page.expect_response(lambda response: response.url.endswith('/income-notifications')) as polled:
                page.clock.fast_forward(61000)
            response = polled.value
            assert response.status == 200 and response.json()['count'] == 1
            page.locator('#income-alert-bar').wait_for(state='visible')
            page.get_by_role('button', name='View income notices', exact=True).click()
            assert popup.locator('.income-notice').count() == 1
            assert '<img src=x onerror=alert(1)>' in popup.inner_text()
            assert 'Assign this incoming payment' in popup.inner_text()
            assert popup.locator('img, script').count() == 0
            layout()
            submit(popup.get_by_role('button', name='View account money and tax / NI earmarks', exact=True))
            assert '#account-reserves' in page.url
            assert popup.is_hidden()
            assert 'Suggested tax / NI reserve needs review.' in page.locator('#account-reserves').inner_text()
            if width in (390, 1280): page.screenshot(path=f'/tmp/toms-income-workspace-{width}.png', full_page=True)
            assert fixture.live_value('SELECT count(*) FROM SCHEMA.account_reserves') == 0
            assert fixture.live_value('SELECT count(*) FROM SCHEMA.income_notice_reads') == 0
            assert fixture.live_value('SELECT count(*) FROM SCHEMA.expense_deductions') == 0
            assert fixture.live_value('SELECT count(*) FROM SCHEMA.income_streams') == original_sources
            if width == 390:
                html_context = browser.new_context(java_script_enabled=False, viewport={'width': width, 'height': 900})
                install_owner_cookie(html_context, fixture, origin)
                html_page = html_context.new_page()
                html_page.goto(origin + '/dashboard/income-streams?setup=1')
                html_page.locator('#source-name').fill('Source without JavaScript')
                html_page.locator('#source-kind').select_option('employed')
                with html_page.expect_navigation():
                    html_page.get_by_role('button', name='Continue to income source', exact=True).click()
                assert 'Expected income is unknown' in html_page.locator('main').inner_text()
                assert html_page.evaluate('document.documentElement.scrollWidth <= innerWidth')
                with html_page.expect_navigation():
                    html_page.get_by_role('link', name='Set up forecast or shifts', exact=True).click()
                html_page.locator('dialog[open] [name=expected_gross]').fill('1200')
                with html_page.expect_navigation():
                    html_page.get_by_role('button', name='Save changes', exact=True).click()
                assert 'GBP 1,200.00 gross for the tax year' in html_page.locator('main').inner_text()
                html_context.close()
            fixture.bank.assert_not_called()
            assert not errors, errors
            page.close()
            print(f'PASS {width}px: stream dialogs, tracking/deductible expenses, earmarks, review and durable income notices')
        finally: fixture.doCleanups()
    browser.close()
