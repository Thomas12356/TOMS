"""Real owner requests: annual profile, Scottish travel, mobile and no-JS saves."""
from datetime import date
from decimal import Decimal
import logging
from pathlib import Path
import sys
from threading import Thread

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / 'tests')]
from playwright.sync_api import sync_playwright
from sqlalchemy.orm import Session
from werkzeug.serving import make_server
from app import app
from models import MileageEntry, TaxProfile
from services.web.test_data import sample_id
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
            engine = fixture.admin.execution_options(schema_translate_map={'toms': fixture.demo_schema})
            with Session(engine) as saved, saved.begin():
                saved.add(MileageEntry(id=sample_id(880), income_stream_id=sample_id(22),
                    journey_date=date(2026, 4, 10), location='scotland', vehicle_type='car_van',
                    vehicle_key='SAMPLE', miles=Decimal(100), purpose='Scottish business travel',
                    start_postcode='EH1 1AA', end_postcode='EH2 1AA', reimbursed_minor=0))
            server = make_server('127.0.0.1', 0, app, threaded=True)
            worker = Thread(target=server.serve_forever, daemon=True)
            worker.start()
            fixture.addCleanup(server.server_close)
            fixture.addCleanup(worker.join, 5)
            fixture.addCleanup(server.shutdown)
            origin = f'http://127.0.0.1:{server.server_port}'
            context = browser.new_context(viewport={'width': width, 'height': 900}, java_script_enabled=width != 390)
            cookie = fixture.client.get_cookie(app.config['SESSION_COOKIE_NAME'])
            context.add_cookies([dict(name=cookie.key, value=cookie.value, url=origin)])
            page = context.new_page()
            errors = []
            page.on('pageerror', lambda error: errors.append(str(error)))

            def layout():
                assert page.evaluate('document.documentElement.scrollWidth <= innerWidth'), width

            def go(path):
                response = page.goto(origin + path)
                assert response.status == 200, (path, response.status)
                layout()

            def save(jurisdiction, confirm=True):
                page.get_by_label('Tax jurisdiction', exact=True).select_option(jurisdiction)
                if confirm:
                    page.get_by_role('checkbox', name='I confirm this jurisdiction').check()
                with page.expect_navigation():
                    page.get_by_role('button', name='Save tax profile', exact=True).click()
                layout()

            go('/dashboard/tax-estimate')
            assert page.locator('.tax-totals').is_visible()
            original = page.locator('.tax-totals').inner_text()
            assert page.locator('.hmrc-payment-guidance').is_visible()
            assert page.locator('.hmrc-payment-guidance a').get_attribute('href') == 'https://www.gov.uk/understand-self-assessment-bill/payments-on-account'
            go('/dashboard/tax-profile')
            assert page.get_by_role('complementary', name='Test mode', exact=True).is_visible()
            save('wales', confirm=False)
            assert 'Confirm that this is your tax jurisdiction' in page.get_by_role('alert').inner_text()
            assert page.get_by_label('Tax jurisdiction', exact=True).input_value() == 'wales'
            save('wales')
            assert page.locator('.tax-totals').inner_text() == original
            go('/dashboard/tax-profile')
            stale = context.new_page()
            stale.goto(origin + '/dashboard/tax-profile')
            save('scotland')
            assert page.locator('.tax-totals').count() == 0
            assert page.get_by_role('link', name='Review tax profile', exact=True).is_visible()
            stale.get_by_label('Tax jurisdiction', exact=True).select_option('england')
            stale.get_by_role('checkbox', name='I confirm this jurisdiction').check()
            with stale.expect_navigation() as changed:
                stale.get_by_role('button', name='Save tax profile', exact=True).click()
            assert changed.value.status == 409
            assert stale.get_by_role('link', name='Reload current profile').is_visible()
            assert stale.get_by_role('button', name='Save tax profile', exact=True).count() == 0
            stale.close()
            go('/dashboard/account-money')
            assert 'Suggested tax / NI reserve needs review' in page.locator('main').inner_text()
            assert page.locator('.hmrc-payment-guidance').is_visible()
            go('/dashboard/tax-profile')
            save('unknown', confirm=False)
            assert page.locator('.tax-totals').count() == 0
            go('/dashboard/tax-profile')
            save('northern_ireland')
            assert page.locator('.tax-totals').inner_text() == original
            go('/dashboard/tax-profile')
            if width in (390, 1280):
                page.screenshot(path=f'/tmp/toms-tax-profile-{width}.png', full_page=True)
            with Session(fixture.admin.execution_options(schema_translate_map={'toms': fixture.real_schema})) as real:
                assert real.get(TaxProfile, '2026-27') is None
            assert not errors, errors
            fixture.bank.assert_not_called()
            context.close()
            print(f'PASS {width}px: profile validation, unchanged supported figures, Scotland travel, unsupported scopes, stale saves, HMRC guidance and sample isolation', flush=True)
        finally:
            fixture.doCleanups()
    browser.close()
