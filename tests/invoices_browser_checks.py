"""Real owner invoice forms and settlement through Chromium + disposable schemas."""
import logging
from pathlib import Path
import sys
from threading import Thread

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
sys.path.insert(0,str(ROOT/'tests'))
from playwright.sync_api import sync_playwright
from sqlalchemy import text
from werkzeug.serving import make_server
from app import app
from test_test_data import TestDataTests
from services.web.test_data import sample_id

logging.getLogger('werkzeug').setLevel(logging.ERROR)
with sync_playwright() as playwright:
    browser=playwright.chromium.launch(headless=True)
    for width in (320,390,768,1280):
        fixture=TestDataTests()
        try:
            fixture.setUp()
            fixture.start_sync.return_value=False
            assert fixture.toggle(True).status_code==303
            server=make_server('127.0.0.1',0,app,threaded=True)
            worker=Thread(target=server.serve_forever,daemon=True)
            worker.start()
            fixture.addCleanup(server.server_close)
            fixture.addCleanup(worker.join,5)
            fixture.addCleanup(server.shutdown)
            origin=f'http://127.0.0.1:{server.server_port}'
            page=browser.new_page(viewport={'width':width,'height':900})
            cookie=fixture.client.get_cookie(app.config['SESSION_COOKIE_NAME'])
            page.context.add_cookies([dict(name=cookie.key,value=cookie.value,url=origin)])
            errors=[]
            page.on('pageerror',lambda error:errors.append(str(error)))
            def assert_layout():
                assert page.evaluate('document.documentElement.scrollWidth <= innerWidth'),width
            def click(button):
                with page.expect_navigation(): button.click()
                assert_layout()
            def open_allocate():
                page.locator('.invoice-payment-form > summary').click()
            page.goto(origin+'/dashboard/invoices')
            assert page.get_by_role('heading',name='Invoices',exact=True).is_visible()
            click(page.get_by_role('link',name='Draft invoice',exact=True))
            click(page.get_by_role('button',name='Issue invoice',exact=True))
            assert page.get_by_role('heading',name='TEST-INV-000001').is_visible()
            assert 'Unpaid' in page.locator('.invoice-totals').inner_text()
            assert 'No available matching payments' in page.locator('main').inner_text()
            with fixture.admin.begin() as connection:
                connection.execute(text(f'UPDATE {fixture.demo_schema}.transactions SET confirmed_at=now() WHERE feed_item_uid=:id'),dict(id=sample_id(31)))
            page.reload()
            open_allocate()
            page.locator('#invoice-payment').select_option('|'.join((sample_id(1),sample_id(11),sample_id(31))))
            page.locator('#invoice-cash').fill('100')
            click(page.get_by_role('button',name='Allocate payment',exact=True))
            assert 'Part-paid' in page.locator('.invoice-totals').inner_text()
            open_allocate()
            page.locator('#invoice-payment').select_option('|'.join((sample_id(1),sample_id(11),sample_id(31))))
            page.locator('#invoice-cash').fill('100')
            click(page.get_by_role('button',name='Allocate payment',exact=True))
            assert 'Paid' in page.locator('.invoice-totals').inner_text()
            assert page.locator('.invoice-payment-form').count()==0
            click(page.get_by_role('button',name='Remove allocation',exact=True).first)
            assert 'Part-paid' in page.locator('.invoice-totals').inner_text()
            click(page.get_by_role('button',name='Remove allocation',exact=True).first)
            page.locator('details > summary',has_text='Cancel invoice').click()
            page.locator('[name=confirm_cancel]').check()
            click(page.get_by_role('button',name='Cancel invoice',exact=True))
            assert 'Cancelled' in page.locator('.invoice-totals').inner_text()
            page.goto(origin+'/dashboard/invoices/new?stream='+sample_id(21))
            page.locator('[name=shift_id]').check()
            page.locator('#invoice-client').fill('<script>Example client</script>')
            click(page.get_by_role('button',name='Create draft',exact=True))
            assert '<script>Example client</script>' in page.locator('main').inner_text()
            assert page.locator('script:not([src])').count()==0
            click(page.get_by_role('link',name='Edit draft',exact=True))
            page.locator('#invoice-client').fill('Replacement client')
            click(page.get_by_role('button',name='Save draft',exact=True))
            click(page.get_by_role('button',name='Issue invoice',exact=True))
            assert page.get_by_role('heading',name='TEST-INV-000002').is_visible()
            # A second stream: two shifts, one CIS payment and only logged deductions.
            for amount,note in (('600','First CIS shift'),('400','Second CIS shift')):
                page.goto(origin+f'/dashboard/income-streams/{sample_id(23)}/shifts?add=1')
                page.locator('#shift-start').fill('2026-10-06T08:00')
                page.locator('#shift-end').fill('2026-10-06T16:00')
                page.locator('#shift-payment-mode').select_option('total')
                page.locator('#shift-total').fill(amount)
                page.locator('#shift-notes').fill(note)
                click(page.get_by_role('button',name='Add work',exact=True))
            assert 'GBP 71,000.00' in page.request.get(origin+'/dashboard/tax-estimate').text()
            page.goto(origin+'/dashboard/invoices/new?stream='+sample_id(23))
            for box in page.locator('[name=shift_id]').all(): box.check()
            page.locator('#invoice-client').fill('CIS client with a longer readable name')
            page.locator('#invoice-address').fill('Example business address\nExample town')
            click(page.get_by_role('button',name='Create draft',exact=True))
            click(page.get_by_role('button',name='Issue invoice',exact=True))
            with fixture.admin.begin() as connection:
                connection.execute(text(f'UPDATE {fixture.demo_schema}.transactions SET confirmed_at=now() WHERE feed_item_uid=:id'),dict(id=sample_id(33)))
            page.reload()
            open_allocate()
            page.locator('#invoice-payment').select_option('|'.join((sample_id(1),sample_id(11),sample_id(33))))
            page.locator('#invoice-cash').fill('800')
            page.locator('#invoice-tax').fill('200')
            click(page.get_by_role('button',name='Allocate payment',exact=True))
            assert 'Paid' in page.locator('.invoice-totals').inner_text()
            assert 'GBP 0.00' in page.locator('.invoice-totals').inner_text()
            assert page.locator('#invoice-work').locator('..').locator('.invoice-list-row').count()==2
            assert 'GBP 71,000.00' in page.request.get(origin+'/dashboard/tax-estimate').text()
            if width in (390,1280): page.screenshot(path=f'/tmp/toms-invoice-{width}.png',full_page=True)
            assert fixture.live_value('SELECT count(*) FROM SCHEMA.invoices')==0
            assert fixture.live_value('SELECT count(*) FROM SCHEMA.invoice_payments')==0
            fixture.bank.assert_not_called()
            assert not errors,errors
            page.close()
            print(f'PASS {width}px: invoice issue/edit/cancel, partial payments, CIS settlement, multi-shift selection, escaping, forecast and real-data isolation')
        finally: fixture.doCleanups()
    browser.close()
