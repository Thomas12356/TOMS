"""Bills, actual statement payments and available cash using isolated records."""
import logging
from pathlib import Path
import sys
from threading import Thread
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from playwright.sync_api import sync_playwright
from sqlalchemy import text
from werkzeug.serving import make_server
from app import app
from test_test_data import TestDataTests
from browser_owner import install_owner_cookie

logging.getLogger('werkzeug').setLevel(logging.ERROR)
with sync_playwright() as playwright:
    browser=playwright.chromium.launch(headless=True)
    for width in (320,390,768,1280):
        fixture=TestDataTests()
        try:
            fixture.setUp()
            fixture.toggle(True)
            fixture.enterContext(patch('routes.cash_planning.current_plan',return_value=dict(
                tax_year='2026-27',received_reserve_minor=30000,blockers=[],received_issues=[])))
            server=make_server('127.0.0.1',0,app,threaded=True)
            worker=Thread(target=server.serve_forever,daemon=True)
            worker.start()
            fixture.addCleanup(server.server_close)
            fixture.addCleanup(worker.join,5)
            fixture.addCleanup(server.shutdown)
            origin=f'http://127.0.0.1:{server.server_port}'
            page=browser.new_page(viewport={'width':width,'height':900},java_script_enabled=width!=390)
            install_owner_cookie(page.context, fixture, origin)
            errors=[]
            page.on('pageerror',lambda error:errors.append(str(error)))
            def layout():
                if not page.evaluate('document.documentElement.scrollWidth <= innerWidth'):
                    page.screenshot(path='/tmp/toms-cash-overflow.png',full_page=True)
                    overflow=page.evaluate("[...document.querySelectorAll('main *')].filter(e=>e.getBoundingClientRect().right>innerWidth).map(e=>[e.tagName,e.className,e.getBoundingClientRect().width]).slice(0,10)")
                    raise AssertionError((width,overflow))
            def click(control):
                with page.expect_navigation(): control.click()
                layout()
            page.goto(origin+'/dashboard/cash-planning')
            assert page.get_by_role('heading',name='Bills and cash planning',exact=True).is_visible()
            assert 'REAL-ISOLATION-SENTINEL' not in page.locator('main').inner_text()
            click(page.get_by_role('link',name='Add a bill',exact=True))
            page.locator('#commitment-name').fill('Example rent <script>test</script>')
            page.locator('#commitment-amount').fill('100')
            page.locator('#commitment-recurrence').select_option('monthly')
            click(page.get_by_role('button',name='Save commitment',exact=True))
            assert page.get_by_role('heading',name='Example rent <script>test</script>',exact=True).is_visible()
            assert page.locator('script:not([src])').count()==0
            click(page.get_by_role('link',name='Return to bills and cash planning',exact=True))
            click(page.get_by_role('link',name='Add an HMRC statement charge',exact=True))
            page.locator('#commitment-name').fill('HMRC first payment on account')
            page.locator('#commitment-amount').fill('300')
            page.locator('#commitment-component').select_option('first_on_account')
            page.locator('[name=statement_confirmed]').check()
            page.locator('[name=supported_charge_confirmed]').check()
            click(page.get_by_role('button',name='Save commitment',exact=True))
            hmrc_url=page.url
            page.get_by_text('Record a payment already made',exact=True).click()
            page.locator('#payment-amount').fill('50')
            page.locator('#payment-tax-credit').fill('50')
            page.locator('#payment-reference').fill('Sample direct payment')
            click(page.get_by_role('button',name='Record payment',exact=True))
            assert 'GBP 250.00 outstanding' in page.locator('main').inner_text()
            click(page.get_by_role('link',name='Return to bills and cash planning',exact=True))
            forms=page.locator('form').filter(has=page.locator('input[name=action][value=balance]'))
            for index in range(forms.count()):
                form=forms.nth(index)
                form.locator('input[name=amount]').fill('1000')
                click(form.get_by_role('button',name='Save observed balance',exact=True))
            page.locator('[name=confirm]').check()
            click(page.get_by_role('button',name='Confirm planning coverage',exact=True))
            assert 'Coverage confirmed for these records today.' in page.locator('main').inner_text()
            if 'GBP 1,600.00 after recorded commitments' not in page.locator('main').inner_text():
                Path('/tmp/toms-cash-budget-browser-failure.html').write_text(page.content())
                raise AssertionError('Expected sample cash budget; see temporary sample HTML')
            if width==390:
                page.screenshot(path='/tmp/toms-cash-planning-mobile.png',full_page=True)
            page.goto(hmrc_url)
            click(page.get_by_role('button',name='Void incorrect payment',exact=True))
            assert 'Voided' in page.locator('main').inner_text()
            assert 'GBP 300.00 outstanding' in page.locator('main').inner_text()
            click(page.get_by_role('link',name='Return to bills and cash planning',exact=True))
            assert 'Confirm planning coverage' in page.locator('main').inner_text()
            with fixture.admin.connect() as connection:
                assert connection.execute(text(f'SELECT count(*) FROM {fixture.real_schema}.cash_commitments')).scalar()==0
                assert connection.execute(text(f'SELECT count(*) FROM {fixture.demo_schema}.cash_payments')).scalar()==1
            assert fixture.bank.call_count==0
            assert not errors,errors
            page.close()
            print(f'PASS {width}px: bills, HMRC payment/void, observed balances, coverage and isolated cash calculation')
        finally:
            fixture.doCleanups()
    browser.close()
