"""Other-bank setup, mapping, duplicate decisions and history through real owner forms."""
import logging
from pathlib import Path
import sys
from threading import Thread
from datetime import datetime, timezone
from werkzeug.serving import make_server

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from playwright.sync_api import sync_playwright
from app import app
from test_test_data import TestDataTests
from browser_owner import install_owner_cookie
from services.invoices.workflow import LOCAL_TIME

logging.getLogger('werkzeug').setLevel(logging.ERROR)
with sync_playwright() as playwright:
    browser=playwright.chromium.launch(headless=True)
    for width in (320,390,768,1280):
        fixture=TestDataTests()
        try:
            fixture.setUp()
            fixture.toggle(True)
            server=make_server('127.0.0.1',0,app,threaded=True)
            worker=Thread(target=server.serve_forever,daemon=True)
            worker.start()
            fixture.addCleanup(server.server_close)
            fixture.addCleanup(worker.join,5)
            fixture.addCleanup(server.shutdown)
            origin=f'http://127.0.0.1:{server.server_port}'
            page=browser.new_page(viewport={'width':width,'height':900},java_script_enabled=width!=390)
            install_owner_cookie(page.context,fixture,origin)
            errors=[]
            page.on('pageerror',lambda error:errors.append(str(error)))
            def layout():
                assert page.evaluate('document.documentElement.scrollWidth <= innerWidth'),(width,page.url)
            def click(locator):
                with page.expect_navigation():
                    locator.click()
                layout()
            page.goto(origin+'/dashboard/accounts')
            layout()
            assert 'REAL-ISOLATION-SENTINEL' not in page.locator('main').inner_text()
            click(page.get_by_role('link',name='Add an account from another bank',exact=True))
            page.locator('#name').fill('Earlier bank <example>')
            page.locator('#bank').fill('CSV bank')
            page.locator('#opened').fill('2026-04-06')
            click(page.get_by_role('button',name='Add account',exact=True))
            account_url=page.url
            assert 'Incomplete history blocks' in page.locator('main').inner_text(),page.locator('main').inner_text()
            click(page.get_by_role('link',name='Import a CSV statement',exact=True))
            data=b'Date,Amount,Name,Reference\n2026-04-10,100,Own account,transfer\n2026-04-10,100,Own account,transfer\n'
            page.locator('#statement').set_input_files({'name':'example.csv','mimeType':'text/csv','buffer':data})
            click(page.get_by_role('button',name='Upload and map columns',exact=True))
            for key,value in dict(date='0',amount='1',payer='2',reference='3').items():
                page.locator('#map-'+key).select_option(value)
            click(page.get_by_role('button',name='Validate and preview',exact=True))
            assert page.locator('#row-3').input_value()==''
            assert 'Possible duplicate' in page.locator('main').inner_text()
            page.locator('#row-3').select_option('skip')
            page.locator('[name=confirm]').check()
            click(page.get_by_role('button',name='Confirm import',exact=True))
            assert '1 transactions imported; 1 skipped' in page.locator('main').inner_text()
            click(page.get_by_role('link',name='Review imported transactions',exact=True))
            assert page.locator('.sync-controls').count()==0
            assert page.locator('#account-balances').count()==0
            # Change the classification through the existing owner workflow.
            click(page.locator('.transaction-ledger').get_by_role('link',name='Guided review',exact=True).first)
            page.locator('[name=type]').select_option('internal_transfer')
            click(page.get_by_role('button',name='Save classification and continue',exact=True))
            click(page.get_by_role('button',name='Confirm these saved details and continue',exact=True))
            page.goto(account_url)
            assert '0 unreviewed' in page.locator('main').inner_text()
            form=page.locator('form').filter(has=page.locator('#through'))
            form.locator('[name=confirm]').check()
            click(page.get_by_role('button',name='Confirm history coverage',exact=True))
            assert 'Coverage is confirmed.' in page.locator('main').inner_text()
            # A new missing payment uses the same review, never an opening cash entry.
            click(page.get_by_role('link',name='Add a missing transaction',exact=True))
            page.locator('#date').fill(datetime.now(timezone.utc).astimezone(LOCAL_TIME).date().isoformat())
            page.locator('#amount').fill('2.50')
            page.locator('#direction').select_option('OUT')
            page.locator('#payer').fill('Sample payment')
            click(page.get_by_role('button',name='Check transaction preview',exact=True))
            assert 'OUT · GBP 2.50' in page.locator('main').inner_text()
            page.locator('[name=confirm]').check()
            click(page.get_by_role('button',name='Confirm import',exact=True))
            page.goto(account_url)
            assert 'Incomplete history blocks' in page.locator('main').inner_text()
            click(page.get_by_role('link',name='Record its current available balance',exact=True))
            identity=account_url.rsplit('/',1)[1]
            page.locator(f'[name="amount_{identity}"]').fill('0')
            click(page.get_by_role('button',name='Save balances and review',exact=True))
            page.goto(account_url)
            closure=page.locator('details').filter(has=page.get_by_text('Close this account after moving its money',exact=True))
            closure.locator('summary').click()
            closure.locator('[name=closed_on]').fill(datetime.now(timezone.utc).astimezone(LOCAL_TIME).date().isoformat())
            closure.locator('[name=confirm]').check()
            click(closure.get_by_role('button',name='Close account',exact=True))
            assert 'Closed on' in page.locator('main').inner_text()
            reopen=page.locator('details').filter(has=page.get_by_text('Correct an accidental closure',exact=True))
            reopen.locator('summary').click()
            reopen.locator('[name=confirm]').check()
            click(reopen.get_by_role('button',name='Reopen account',exact=True))
            assert 'Included in current cash' in page.locator('main').inner_text()
            if width==390:
                page.screenshot(path='/tmp/toms-statement-account-mobile.png',full_page=True)
            assert fixture.bank.call_count==0
            assert not errors,errors
            page.close()
            print(f'PASS {width}px: account, CSV mapping/duplicates, transfer review, history, missing payment and closure correction')
        finally:
            fixture.doCleanups()
    browser.close()
