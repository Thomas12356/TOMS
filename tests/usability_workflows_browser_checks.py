"""Complete owner journeys for accounts, reusable defaults and explicit suggestions."""
from datetime import date, datetime, timezone
from decimal import Decimal
import logging
from pathlib import Path
import sys
from threading import Thread

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'tests'))
from playwright.sync_api import sync_playwright
from sqlalchemy.orm import Session
from werkzeug.serving import make_server
from app import app
from models import IncomeShift, MileageEntry, Transaction, TransactionClassification, TransactionIncome
from services.database.connection import db
from services.web.test_data import sample_id
from test_test_data import TestDataTests
from browser_owner import install_owner_cookie

logging.getLogger('werkzeug').setLevel(logging.ERROR)
with sync_playwright() as playwright:
    browser = playwright.chromium.launch(headless=True)
    for width in (320, 390, 768, 1024, 1280):
        fixture = TestDataTests()
        try:
            fixture.setUp()
            fixture.start_sync.return_value = False
            assert fixture.toggle(True).status_code == 303
            engine = fixture.admin.execution_options(schema_translate_map={'toms': fixture.demo_schema})
            with Session(engine) as saved, saved.begin():
                for index in range(3):
                    row = Transaction(account_uid=sample_id(1), category_uid=sample_id(11), feed_item_uid=sample_id(80 + index),
                        amount_minor=2500, currency='GBP', direction='IN', status='SETTLED',
                        transaction_time=datetime(2026, 6, index + 1, tzinfo=timezone.utc),
                        source_updated_at=datetime(2026, 6, index + 1, tzinfo=timezone.utc),
                        confirmed_at=datetime(2026, 6, 4, tzinfo=timezone.utc) if index < 2 else None,
                        source='FASTER_PAYMENTS_IN', counterparty_name='Client <img src=x>', reference='Recurring reference', raw_payload={})
                    if index < 2:
                        row.classification = TransactionClassification(type='income')
                        row.income = TransactionIncome(income_stream_id=sample_id(22), income_type='other',
                            gross_minor=2500, tax_treatment='no_tax_deducted', tax_deducted_minor=0,
                            recorded_currency='GBP', adjustment_minor=0, needs_review=False)
                    saved.add(row)
                saved.add(MileageEntry(id=sample_id(90), income_stream_id=sample_id(22), journey_date=date(2026, 5, 1),
                    location='england', vehicle_type='car_van', vehicle_key='AB12CDE', miles=Decimal('12'),
                    purpose='Customer visit', start_postcode='SW1A 1AA', end_postcode='SW1A 2AA', reimbursed_minor=0))
            server = make_server('127.0.0.1', 0, app, threaded=True)
            worker = Thread(target=server.serve_forever, daemon=True)
            worker.start()
            fixture.addCleanup(server.server_close)
            fixture.addCleanup(worker.join, 5)
            fixture.addCleanup(server.shutdown)
            origin = f'http://127.0.0.1:{server.server_port}'
            context = browser.new_context(viewport={'width': width, 'height': 900}, java_script_enabled=width != 390)
            install_owner_cookie(context, fixture, origin)
            page = context.new_page()
            errors, requests = [], []
            page.on('pageerror', lambda error: errors.append(str(error)))
            page.on('request', lambda request: requests.append(request.url))
            def layout():
                assert page.evaluate('document.documentElement.scrollWidth <= innerWidth'), width
            def go(path):
                page.goto(origin + path)
                layout()
            def submit(button):
                with page.expect_navigation() as navigation:
                    button.click()
                if navigation.value and navigation.value.status >= 400:
                    Path('/tmp/toms-usability-browser-submit-error.html').write_text(page.content())
                    raise AssertionError(('Owner submission rejected', width, navigation.value.status))
                layout()
            go('/dashboard/account-money')
            assert not any('/dashboard/balances' in request for request in requests)
            assert 'not an available-to-spend figure' in page.locator('main').inner_text()
            if width != 390:
                page.get_by_role('button', name='Refresh bank balances', exact=True).click()
                page.wait_for_function("() => document.querySelector('#account-balances').textContent.includes('After earmarks:')")
                assert 'Example balances displayed' in page.locator('#balance-freshness').inner_text()
                # Failure must remove previously displayed cash instead of leaving a stale amount.
                page.route('**/dashboard/balances?reserves=1', lambda route: route.fulfill(status=503, body='{}', content_type='application/json'))
                page.get_by_role('button', name='Refresh bank balances', exact=True).click()
                page.wait_for_function("() => document.querySelector('#balance-freshness').textContent.includes('failed')")
                assert 'GBP 2,450.75' not in page.locator('#account-balances').inner_text()
                page.unroute('**/dashboard/balances?reserves=1')
            account = page.locator('.account-earmark').first
            account.locator('summary').click()
            account.locator('[name=tax]').fill('10')
            account.locator('[name=ni]').fill('2')
            submit(account.get_by_role('button', name='Save earmarks', exact=True))
            assert '/account-money#account-reserves' in page.url, page.url
            go(f'/dashboard/income-streams/{sample_id(21)}/shifts?reuse={sample_id(41)}')
            assert page.locator('#shift-rate').input_value() == '25.00'
            assert page.locator('#shift-start').input_value() == ''
            assert page.locator('#shift-notes').input_value() == ''
            page.locator('#shift-start').fill('2026-05-20T08:00')
            page.locator('#shift-end').fill('2026-05-20T10:00')
            submit(page.get_by_role('button', name='Add overtime', exact=True))
            with Session(engine) as saved:
                assert saved.get(IncomeShift, sample_id(41)).gross_minor == 20000
                assert saved.scalar(db.select(db.func.count()).select_from(IncomeShift)) == 2
            go('/dashboard/deductions?type=mileage&reuse=' + sample_id(90))
            assert page.locator('#mileage-vehicle').input_value() == 'AB12CDE'
            assert page.locator('#mileage-miles').input_value() == ''
            assert page.locator('#mileage-reimbursed').input_value() == ''
            assert not page.locator('[name=eligible]').is_checked()
            page.locator('#journey-date').fill('2026-05-20')
            page.locator('#mileage-miles').fill('20')
            page.locator('#mileage-reimbursed').fill('0')
            page.locator('[name=eligible]').check()
            submit(page.get_by_role('button', name='Save mileage', exact=True))
            with Session(engine) as saved:
                assert saved.get(MileageEntry, sample_id(90)).miles == Decimal('12')
                assert saved.scalar(db.select(db.func.count()).select_from(MileageEntry)) == 2
            review = f'/dashboard/transactions/{sample_id(1)}/{sample_id(11)}/{sample_id(82)}/review'
            go(review)
            assert page.get_by_role('heading', name='Suggested classification: Income', exact=True).is_visible()
            submit(page.get_by_role('button', name='Use suggestion in form', exact=True))
            with Session(engine) as saved:
                target = saved.get(Transaction, (sample_id(1), sample_id(11), sample_id(82)))
                assert target.classification is None and target.income is None and target.confirmed_at is None
            submit(page.get_by_role('button', name='Save classification and continue', exact=True))
            assert page.get_by_role('heading', name='Suggested income source: Demo freelance work', exact=True).is_visible()
            submit(page.get_by_role('button', name='Use suggestion in form', exact=True))
            assert page.locator('#income-stream').input_value() == sample_id(22)
            assert page.locator('#gross').input_value() == ''
            assert page.locator('#tax-treatment').input_value() == 'unknown'
            page.locator('#gross').fill('25')
            page.locator('#tax-treatment').select_option('no_tax_deducted')
            page.locator('#tax_deducted').fill('0')
            submit(page.get_by_role('button', name='Save income and check', exact=True))
            assert page.locator('main img').count() == 0
            page.screenshot(path=f'/tmp/toms-usability-review-{width}.png', full_page=True)
            submit(page.get_by_role('button', name='Confirm these saved details and continue', exact=True))
            go('/dashboard/income-streams/' + sample_id(22))
            assert 'Client <img src=x>' in page.locator('main').inner_text()
            assert fixture.live_value('SELECT count(*) FROM SCHEMA.account_reserves') == 0
            assert fixture.live_value('SELECT count(*) FROM SCHEMA.income_shifts') == 0
            assert fixture.live_value('SELECT count(*) FROM SCHEMA.mileage_entries') == 0
            fixture.bank.assert_not_called()
            assert not errors, errors
            context.close()
            print(f'PASS {width}px: explicit account refresh/failure, earmarks, reusable work/mileage, suggestions and isolated saves')
        finally:
            fixture.doCleanups()
    browser.close()
