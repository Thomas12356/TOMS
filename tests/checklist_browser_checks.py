"""Real checklist links and automatic progress through isolated owner forms."""
import logging
from pathlib import Path
import sys
from threading import Thread

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'tests'))
from playwright.sync_api import sync_playwright
from sqlalchemy import text
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
            with fixture.admin.begin() as connection:
                connection.execute(text(f'UPDATE {fixture.demo_schema}.transactions SET confirmed_at=now()'))
                connection.execute(text(f'UPDATE {fixture.demo_schema}.transactions SET confirmed_at=NULL WHERE feed_item_uid IN (:income,:expense)'),
                                   dict(income=sample_id(33), expense=sample_id(34)))
                connection.execute(text(f'UPDATE {fixture.demo_schema}.income_streams SET expected_gross_minor=NULL, expected_gross_period=NULL, expected_gross_currency=NULL WHERE id=:id'), dict(id=sample_id(21)))
                connection.execute(text(f'UPDATE {fixture.demo_schema}.transaction_income SET tax_deducted_minor=NULL WHERE feed_item_uid=:id'), dict(id=sample_id(33)))
                connection.execute(text(f'UPDATE {fixture.demo_schema}.transactions SET counterparty_name=:name WHERE feed_item_uid=:id'), dict(id=sample_id(33), name='<script>Checklist client</script>'))
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
            errors = []
            page.on('pageerror', lambda error: errors.append(str(error)))
            def layout():
                assert page.evaluate('document.documentElement.scrollWidth <= innerWidth'), width
            def go():
                page.goto(origin + '/dashboard/checklist')
                layout()
            def click(control):
                with page.expect_navigation(): control.click()
                layout()
            go()
            assert page.get_by_role('heading', name='Your checklist').is_visible()
            assert '1 needing attention' in page.locator('#check-forecasts').inner_text()
            assert 'actual tax withheld' in page.locator('#check-income').inner_text()
            assert '<script>Checklist client</script>' in page.locator('#check-income').inner_text()
            assert page.locator('script:not([src])').count() == 0
            assert '2 needing attention' in page.locator('#check-review').inner_text()
            click(page.locator('#check-forecasts .checklist-records a').first)
            configure = page.locator('#configure-' + sample_id(21))
            assert configure.is_visible()
            configure.locator('[name=expected_gross]').fill('40000')
            configure.locator('[name=expected_gross_currency]').select_option('GBP')
            configure.locator('[name=expected_gross_period]').select_option('yearly')
            click(configure.get_by_role('button', name='Save changes', exact=True))
            go()
            assert 'No issues found' in page.locator('#check-forecasts').inner_text()
            click(page.locator('#check-income .checklist-records a').first)
            page.locator('#tax-deducted').fill('200')
            click(page.get_by_role('button', name='Save income details', exact=True))
            go()
            assert 'No issues found' in page.locator('#check-income').inner_text()
            assert 'Received-income target:' in page.locator('.checklist-summary').inner_text()
            click(page.locator('#check-review').get_by_role('link', name='Open transaction review', exact=True))
            page.wait_for_function("() => document.querySelector('#transaction-review').open")
            for remaining in (1, 0):
                with page.expect_response(lambda response: '/confirm' in response.url and response.request.method == 'POST') as confirmed:
                    page.get_by_role('button', name='Confirm details', exact=True).click()
                assert confirmed.value.status == 200
                if remaining:
                    page.wait_for_function('(n) => document.querySelector("#review-count").textContent.startsWith(String(n))', arg=remaining)
                else:
                    page.wait_for_function("() => !document.querySelector('#transaction-review').open")
            go()
            assert '5 of 5 checks clear' in page.locator('.checklist-summary').inner_text()
            assert page.locator('.checklist-summary strong').all_inner_texts() == ['Available', 'Available']
            assert 'REAL-ISOLATION-SENTINEL' not in page.locator('main').inner_text()
            assert fixture.live_value('SELECT count(*) FROM SCHEMA.transactions WHERE confirmed_at IS NOT NULL') == 0
            fixture.bank.assert_not_called()
            assert not errors, errors
            if width in (390, 1280): page.screenshot(path=f'/tmp/toms-checklist-{width}.png', full_page=True)
            page.close()
            print(f'PASS {width}px: checklist links, withholding, forecasts, confirmation, automatic progress and sample isolation')
        finally:
            fixture.doCleanups()
    browser.close()
