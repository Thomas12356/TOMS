"""Reconciliation navigation, filtering and explicit allocation in Chromium."""
import logging
from pathlib import Path
import sys
from threading import Thread

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from playwright.sync_api import sync_playwright
from sqlalchemy import text
from werkzeug.serving import make_server
from app import app
from test_test_data import TestDataTests
from services.web.test_data import sample_id

logging.getLogger('werkzeug').setLevel(logging.ERROR)
with sync_playwright() as playwright:
    browser = playwright.chromium.launch(headless=True)
    for width in (320,390,768,1280):
        fixture = TestDataTests()
        try:
            fixture.setUp()
            fixture.toggle(True)
            with fixture.admin.begin() as connection:
                connection.execute(text(f'UPDATE {fixture.demo_schema}.transactions SET confirmed_at=now()'))
                connection.execute(text(f'UPDATE {fixture.demo_schema}.income_streams SET name=:name WHERE id=:id'),
                    dict(name='<script>Client</script>'+' Long name'*15,id=sample_id(22)))
            server = make_server('127.0.0.1',0,app,threaded=True)
            worker = Thread(target=server.serve_forever,daemon=True)
            worker.start()
            fixture.addCleanup(server.server_close)
            fixture.addCleanup(worker.join,5)
            fixture.addCleanup(server.shutdown)
            origin = f'http://127.0.0.1:{server.server_port}'
            page = browser.new_page(viewport={'width':width,'height':900})
            cookie = fixture.client.get_cookie('session')
            page.context.add_cookies([dict(name='session',value=cookie.value,url=origin)])
            errors = []
            page.on('pageerror',lambda error: errors.append(str(error)))
            def layout():
                assert page.evaluate('document.documentElement.scrollWidth <= innerWidth'), width
            def click(control):
                with page.expect_navigation(): control.click()
                layout()
            page.goto(origin+'/dashboard/invoices')
            click(page.get_by_role('link',name='Review work and payments'))
            assert page.get_by_role('heading',name='Work and payment reconciliation').is_visible()
            assert 'regular forecast' in page.locator('main').inner_text()
            assert 'REAL-INCOME-SENTINEL' not in page.locator('main').inner_text()
            assert page.locator('script:not([src])').count()==0
            page.locator('#reconciliation-stream').select_option(sample_id(21))
            click(page.get_by_role('button',name='Filter',exact=True))
            assert page.locator('#reconciliation-stream').input_value()==sample_id(21)
            assert page.get_by_role('heading',name='Invoices needing attention (1)').is_visible()
            click(page.locator('#reconciliation-invoices').get_by_role('link').first)
            click(page.get_by_role('button',name='Issue invoice',exact=True))
            page.locator('.invoice-payment-form > summary').click()
            page.locator('#invoice-payment').select_option('|'.join((sample_id(1),sample_id(11),sample_id(31))))
            page.locator('#invoice-cash').fill('200')
            page.locator('#invoice-tax').fill('0')
            page.locator('#invoice-ni').fill('0')
            click(page.get_by_role('button',name='Allocate payment',exact=True))
            assert 'Paid' in page.locator('.invoice-totals').inner_text()
            page.goto(origin+'/dashboard/reconciliation?stream='+sample_id(21))
            layout()
            assert page.get_by_role('heading',name='Invoices needing attention (0)').is_visible()
            assert page.get_by_role('heading',name='Receipts available for allocation (1)').is_visible()
            assert fixture.bank.call_count==0
            assert not errors, errors
            page.close()
            print(f'PASS {width}px: responsive layout, sample isolation, source filter, invoice allocation and refreshed balances')
        finally:
            fixture.doCleanups()
    browser.close()
