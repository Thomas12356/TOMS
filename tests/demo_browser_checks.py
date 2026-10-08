"""End-to-end switch/forms/review/tax/sync through Flask and isolated PostgreSQL.

Unlike static layout fixtures, browser POSTs here run real routes and commits.
No actual owner data, bank calls or real application tables are used.
"""
import sys
from pathlib import Path
from threading import Thread
import logging
from werkzeug.serving import make_server
from unittest.mock import patch
from urllib.parse import urlparse, parse_qs

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'tests'))
from playwright.sync_api import sync_playwright
from app import app
from services.database.session import test_data_active
from services.web.test_data import sample_id
from test_test_data import TestDataTests


def real_fixture_balance(*args, **kwargs):
    assert not test_data_active(), 'Demo must not call the real provider path'
    return {'effectiveBalance': {'minorUnits': 999, 'currency': 'GBP'}}


logging.getLogger('werkzeug').setLevel(logging.ERROR)

with sync_playwright() as playwright:
    browser = playwright.chromium.launch(headless=True)
    for width in (320, 390, 1280):
        fixture = TestDataTests()
        try:
            fixture.setUp()
            fixture.start_sync.return_value = False
            provider = fixture.enterContext(patch('routes.dashboard.starling_request', side_effect=real_fixture_balance))
            server = make_server('127.0.0.1', 0, app, threaded=True)
            worker = Thread(target=server.serve_forever, daemon=True)
            worker.start()
            fixture.addCleanup(server.server_close)
            fixture.addCleanup(worker.join, 5)
            fixture.addCleanup(server.shutdown)
            origin = f'http://127.0.0.1:{server.server_port}'
            page = browser.new_page(viewport={'width': width, 'height': 900})
            cookie = fixture.client.get_cookie(app.config['SESSION_COOKIE_NAME'])
            page.context.add_cookies([{'name': cookie.key, 'value': cookie.value, 'url': origin}])
            errors = []
            page.on('pageerror', lambda error: errors.append(str(error)))
            page.on('console', lambda message: errors.append(message.text) if 'Content Security Policy' in message.text else None)
            def open_navigation():
                if not page.get_by_role('switch', name='Test data').is_visible():
                    page.get_by_role('button', name='Toggle navigation').click()
            def close_review():
                if page.locator('#transaction-review').count():
                    page.wait_for_function("() => document.querySelector('#transaction-review').open")
                    page.get_by_role('button', name='Close transaction review').click()
            page.goto(origin + '/dashboard/income-streams')
            open_navigation()
            switch = page.get_by_role('switch', name='Test data')
            assert switch.get_attribute('aria-checked') == 'false'
            with page.expect_navigation():
                switch.click()
            assert page.locator('.test-data-notice').is_visible()
            close_review()
            assert 'Demo employer' in page.locator('main').inner_text()
            assert 'REAL-ISOLATION-SENTINEL' not in page.locator('main').inner_text()
            page.wait_for_function("() => document.querySelector('#last-synced').textContent.startsWith('Last simulated sync:')")
            assert 'GBP 7,550.75' in page.locator('#account-balances').inner_text()
            payroll_path = f'/dashboard/transactions/{sample_id(1)}/{sample_id(11)}/{sample_id(31)}/income'
            page.goto(origin + payroll_path)
            assert page.locator('#ni-deducted').is_visible()
            assert page.locator('#ni-deducted').input_value() == '76.16'
            page.locator('#income-stream').select_option(sample_id(22))
            assert page.locator('#ni-deducted').is_hidden()
            assert page.locator('#ni-deducted').is_disabled()
            page.locator('#income-stream').select_option(sample_id(21))
            assert page.locator('#ni-deducted').is_visible()
            assert page.locator('#ni-deducted').input_value() == '76.16'
            assert page.evaluate('document.documentElement.scrollWidth <= innerWidth'), width
            page.goto(origin + '/dashboard')
            close_review()

            assert page.evaluate('document.documentElement.scrollWidth <= innerWidth'), width
            with page.expect_navigation():
                page.locator('#sync-now').click()
            close_review()
            provider.assert_not_called()
            fixture.start_sync.assert_not_called()
            page.get_by_role('button', name='Review transactions', exact=True).click()
            page.get_by_role('button', name='Confirm details', exact=True).click()
            page.wait_for_function("() => document.querySelector('#review-count').textContent.includes('6')")
            page.get_by_role('button', name='Close transaction review').click()
            open_navigation()
            with page.expect_navigation():
                page.get_by_role('link', name='Tax estimate', exact=True).click()
            assert 'GBP 70,000.00' in page.locator('.tax-totals').inner_text()
            assert 'GBP 10,791.80' in page.locator('.tax-reserve').inner_text()
            assert page.get_by_role('heading', name='Needs attention', exact=True).is_visible()
            assert page.locator('.received-reserve').inner_text() == 'Needs review'
            assert 'GBP 76.16' in page.locator('#ni-heading').locator('..').inner_text()
            page.locator('.tax-readiness summary').first.click()
            assert page.locator('.tax-readiness').get_by_role('link', name='Review records').first.is_visible()
            assert page.evaluate('document.documentElement.scrollWidth <= innerWidth'), width
            open_navigation()
            with page.expect_navigation():
                page.get_by_role('link', name='Income streams', exact=True).click()
            page.get_by_role('link', name='Create a Stream', exact=True).click()
            page.locator('#stream-name').fill('Browser-created sample work')
            page.locator('#stream-kind').select_option('self_employed')
            page.locator('#stream-gross').fill('oops')
            with page.expect_navigation():
                page.get_by_role('button', name='Create stream', exact=True).click()
            assert page.locator('#create-stream-dialog').is_visible()
            assert page.locator('#stream-name').input_value() == 'Browser-created sample work'
            assert page.locator('#stream-gross').input_value() == 'oops'
            page.locator('#stream-gross').fill('1000')
            with page.expect_navigation():
                page.get_by_role('button', name='Create stream', exact=True).click()
            assert 'Browser-created sample work' in page.locator('#your-streams-heading').locator('..').inner_text()
            assert fixture.live_value('SELECT count(*) FROM SCHEMA.income_streams') == 1
            assert fixture.live_value('SELECT count(*) FROM SCHEMA.transactions WHERE confirmed_at IS NOT NULL') == 0
            stream_card = page.locator('.stream-manage').filter(has=page.locator('.stream-heading strong', has_text='Browser-created sample work'))
            assert stream_card.get_by_role('link', name='Manage shifts', exact=True).count() == 0
            overtime_button = stream_card.get_by_role('link', name='Add overtime', exact=True)
            box = overtime_button.bounding_box()
            assert box['height'] >= 44 and box['x'] >= 0 and box['x'] + box['width'] <= width
            with page.expect_navigation():
                overtime_button.click()
            assert page.locator('.shift-create').get_attribute('open') is not None
            assert 'above your regular forecast' in page.locator('main').inner_text()
            page.locator('#shift-start').fill('2026-10-06T18:00')
            page.locator('#shift-end').fill('2026-10-06T21:00')
            page.locator('#shift-payment-mode').select_option('hourly')
            page.locator('#shift-rate').fill('oops')
            page.locator('#shift-notes').fill('Browser overtime')
            with page.expect_navigation():
                page.get_by_role('button', name='Add overtime', exact=True).click()
            assert page.locator('#shift-rate').input_value() == 'oops'
            page.locator('#shift-rate').fill('15')
            with page.expect_navigation():
                page.get_by_role('button', name='Add overtime', exact=True).click()
            assert 'GBP 45.00' in page.locator('main').inner_text()
            assert 'GBP 71,045.00' in page.request.get(origin + '/dashboard/tax-estimate').text()
            with page.expect_navigation():
                page.get_by_role('link', name='Edit overtime', exact=True).click()
            page.locator('#shift-payment-mode').select_option('total')
            page.locator('#shift-total').fill('50')
            with page.expect_navigation():
                page.get_by_role('button', name='Save overtime', exact=True).click()
            assert 'GBP 71,050.00' in page.request.get(origin + '/dashboard/tax-estimate').text()
            with page.expect_navigation():
                page.get_by_role('link', name='Edit overtime', exact=True).click()
            with page.expect_navigation():
                page.get_by_role('button', name='Remove overtime', exact=True).click()
            assert 'GBP 71,000.00' in page.request.get(origin + '/dashboard/tax-estimate').text()
            with page.expect_navigation():
                page.get_by_role('link', name='← Income stream', exact=True).click()
            stream_card = page.locator('.stream-manage').filter(has=page.locator('.stream-heading strong', has_text='Browser-created sample work'))
            stream_card.locator('summary').click()
            stream_card.locator('[data-income-mode]').select_option('shifts')
            assert not stream_card.locator('[data-regular-forecast]').is_visible()
            with page.expect_navigation():
                stream_card.get_by_role('button', name='Save changes', exact=True).click()
            stream_card = page.locator('.stream-manage').filter(has=page.locator('.stream-heading strong', has_text='Browser-created sample work'))
            shifts_button = stream_card.get_by_role('link', name='Manage shifts', exact=True)
            assert shifts_button.is_visible()
            box = shifts_button.bounding_box()
            assert box['height'] >= 44 and box['x'] >= 0 and box['x'] + box['width'] <= width
            with page.expect_navigation():
                stream_card.get_by_role('link', name='Manage shifts', exact=True).click()
            shift_stream_id = urlparse(page.url).path.split('/')[-2]
            page.locator('.shift-create summary').click()
            page.locator('#shift-start').fill('2026-10-06T09:00')
            page.locator('#shift-end').fill('2026-10-06T12:00')
            page.locator('#shift-payment-mode').select_option('hourly')
            assert not page.locator('#shift-total').is_visible()
            page.locator('#shift-rate').fill('15.00')
            page.locator('#shift-notes').fill('Sample variable-rate shift')
            for selector in ('#shift-start','#shift-end','#shift-rate'):
                box = page.locator(selector).bounding_box()
                assert box['x'] >= 0 and box['x']+box['width'] <= width, (width,selector,box)
            with page.expect_navigation():
                page.get_by_role('button', name='Add shift', exact=True).click()
            assert 'GBP 45.00' in page.locator('main').inner_text()
            shift_identity = parse_qs(urlparse(page.get_by_role('link',name='Edit shift',exact=True).get_attribute('href')).query)['edit'][0]
            page.locator('.shift-create summary').click()
            page.locator('#shift-start').fill('2026-10-06T14:00')
            page.locator('#shift-end').fill('2026-10-06T17:00')
            page.locator('#shift-total').fill('80.00')
            page.locator('#shift-notes').fill('Sample total-payment shift')
            with page.expect_navigation():
                page.get_by_role('button', name='Add shift', exact=True).click()
            assert 'GBP 125.00' in page.locator('main').inner_text()
            assert page.evaluate('document.documentElement.scrollWidth <= innerWidth'), width
            assert fixture.live_value('SELECT count(*) FROM SCHEMA.income_shifts') == 0
            open_navigation()
            with page.expect_navigation():
                page.locator('#main-navigation').get_by_role('link',name='Tax estimate',exact=True).click()
            assert 'GBP 70,125.00' in page.locator('.tax-totals').inner_text()

            open_navigation()
            with page.expect_navigation():
                page.get_by_role('link', name='Deductions', exact=True).click()
            assert page.evaluate('document.documentElement.scrollWidth <= innerWidth'), width
            assert page.locator('#expense-payment').count() == 0
            assert page.locator('#mileage-miles').count() == 0
            with page.expect_navigation():
                page.locator('#deduction-type').select_option('expense')
            assert page.locator('#mileage-miles').count() == 0
            page.locator('#expense-payment').select_option(f'{sample_id(1)}:{sample_id(11)}:{sample_id(34)}')
            with page.expect_navigation():
                page.get_by_role('button', name='Choose expense').click()
            page.locator('#expense-stream').select_option(shift_stream_id)
            page.locator('#expense-shift').select_option(shift_identity)
            page.locator('#expense-amount').fill('10.00')
            page.locator('#expense-purpose').fill('Sample business tools')
            page.locator('form:has(#expense-amount) input[name=eligible]').check()
            with page.expect_navigation():
                page.get_by_role('button', name='Save deduction', exact=True).click()
            assert 'Sample business tools' in page.locator('main').inner_text()
            with page.expect_navigation():
                page.locator('#deduction-type').select_option('mileage')
            assert page.locator('#expense-payment').count() == 0
            assert page.locator('#mileage-group').count() == 0
            page.locator('#mileage-stream').select_option(shift_stream_id)
            page.locator('#mileage-shift').select_option(shift_identity)
            assert page.locator('#journey-date').input_value() == '2026-10-06'
            assert 'Sample variable-rate shift' in page.locator('#mileage-purpose').input_value()
            page.locator('#journey-date').fill('2026-04-10')
            page.locator('#mileage-purpose').fill('Actual trip purpose')
            page.locator('#mileage-shift').select_option('')
            page.locator('#mileage-shift').select_option(shift_identity)
            assert page.locator('#journey-date').input_value() == '2026-04-10'
            assert page.locator('#mileage-purpose').input_value() == 'Actual trip purpose'
            page.locator('#mileage-vehicle').fill('DEMO CAR')
            page.locator('#mileage-miles').fill('100')
            page.locator('#mileage-purpose').fill('Sample customer journey')
            page.locator('#start-postcode').fill('SW1A 1AA')
            page.locator('#end-postcode').fill('SW1A 2AA')
            page.locator('form:has(#mileage-miles) input[name=eligible]').check()
            for selector in ('#mileage-stream','#mileage-vehicle','#mileage-miles','#start-postcode'):
                box=page.locator(selector).bounding_box()
                assert box['x'] >= 0 and box['x']+box['width'] <= width, (width,selector,box)
            with page.expect_navigation():
                page.get_by_role('button', name='Save mileage', exact=True).click()
            assert 'calculated mileage relief GBP 55.00' in page.locator('main').inner_text()
            assert page.evaluate('document.documentElement.scrollWidth <= innerWidth'), width
            assert fixture.live_value('SELECT count(*) FROM SCHEMA.expense_deductions') == 0
            assert fixture.live_value('SELECT count(*) FROM SCHEMA.mileage_entries') == 0
            with page.expect_navigation():
                page.get_by_role('link', name='Manage income streams', exact=True).click()
            page.get_by_role('link', name='Create a Stream', exact=True).click()
            page.locator('#stream-name').fill('Browser-linked source')
            page.locator('#stream-kind').select_option('self_employed')
            relationship = page.locator('#stream-mileage-business')
            assert relationship.locator('option[data-employed="true"]').first.is_disabled()
            relationship.select_option(shift_stream_id)
            box = relationship.bounding_box()
            assert box['x'] >= 0 and box['x'] + box['width'] <= width
            page.locator('#stream-gross').fill('30000')
            with page.expect_navigation():
                page.get_by_role('button', name='Create stream', exact=True).click()
            partner_card = page.locator('.stream-manage').filter(has=page.locator('.stream-heading strong', has_text='Browser-linked source'))
            assert 'Mileage: counted with Browser-created sample work' in partner_card.inner_text()
            partner_card.locator('summary').click()
            partner_id = partner_card.locator('[name=stream_id]').input_value()
            assert partner_card.locator('[data-mileage-relationship]').input_value() == shift_stream_id
            open_navigation()
            with page.expect_navigation():
                page.locator('#main-navigation').get_by_role('link', name='Deductions', exact=True).click()
            with page.expect_navigation():
                page.locator('#deduction-type').select_option('mileage')
            page.locator('#mileage-stream').select_option(partner_id)
            page.locator('#journey-date').fill('2026-04-11')
            page.locator('#mileage-vehicle').fill('DEMO CAR')
            page.locator('#mileage-miles').fill('10000')
            page.locator('#mileage-purpose').fill('Partner journey')
            page.locator('#start-postcode').fill('SW1A 1AA')
            page.locator('#end-postcode').fill('SW1A 2AA')
            page.locator('form:has(#mileage-miles) input[name=eligible]').check()
            with page.expect_navigation():
                page.get_by_role('button', name='Save mileage', exact=True).click()
            assert 'calculated mileage relief GBP 5,470.00' in page.locator('main').inner_text()
            with page.expect_navigation():
                page.get_by_role('link', name='Manage income streams', exact=True).click()
            partner_card = page.locator('.stream-manage').filter(has=page.locator('.stream-heading strong', has_text='Browser-linked source'))
            partner_card.locator('summary').click()
            partner_card.locator('[data-mileage-relationship]').select_option('')
            with page.expect_navigation():
                partner_card.get_by_role('button', name='Save changes', exact=True).click()
            open_navigation()
            with page.expect_navigation():
                page.locator('#main-navigation').get_by_role('link', name='Deductions', exact=True).click()
            assert 'calculated mileage relief GBP 5,500.00' in page.locator('main').inner_text()
            with page.expect_navigation():
                page.locator('.stream-payment').filter(has_text='Partner journey').get_by_role('button', name='Remove journey', exact=True).click()
            with page.expect_navigation():
                page.get_by_role('link', name='Edit deduction', exact=True).click()
            with page.expect_navigation():
                page.get_by_role('button', name='Remove deduction', exact=True).click()
            with page.expect_navigation():
                page.get_by_role('button', name='Remove journey', exact=True).click()
            assert 'No linked expenses.' in page.locator('main').inner_text()
            assert 'No mileage logged.' in page.locator('main').inner_text()
            open_navigation()
            assert page.get_by_role('switch', name='Test data').get_attribute('aria-checked') == 'true'
            with page.expect_navigation():
                page.get_by_role('switch', name='Test data').click()
            close_review()
            assert page.locator('.test-data-notice').count() == 0
            assert 'REAL-ISOLATION-SENTINEL' in page.locator('main').inner_text()
            assert 'Demo employer' not in page.locator('main').inner_text()
            fixture.enterContext(patch('routes.system.backup_inventory', return_value=dict(rows=[], count=0, total_bytes=0, error=None)))
            open_navigation()
            with page.expect_navigation():
                page.get_by_role('link', name='System', exact=True).click()
            assert page.get_by_role('heading', name='System management', exact=True).is_visible()
            assert page.get_by_role('heading', name='Database storage', exact=True).is_visible()
            assert page.locator('.activity-record').count() > 0
            page.locator('#activity-mode').select_option('test')
            with page.expect_navigation():
                page.get_by_role('button', name='Filter', exact=True).click()
            assert page.locator('.activity-record').count() > 0
            assert all('Test data' in text for text in page.locator('.activity-record').all_inner_texts())
            assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
            assert not errors, errors
            fixture.bank.assert_not_called()
            page.close()
            print(f'PASS: {width}px stream relationships and shared mileage, popup, overtime CRUD and forecasts, hourly/total shifts, linked expense/mileage CRUD, isolated edits, review, balances, simulated sync, tax, return to real mode and System activity filtering')
        finally:
            fixture.doCleanups()
    browser.close()
