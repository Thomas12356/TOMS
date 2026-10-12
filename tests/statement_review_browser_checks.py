"""Saved mappings, reversible corrections and transfer confirmation through owner forms."""
import logging
from pathlib import Path
import sys
from threading import Thread
from werkzeug.serving import make_server

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from playwright.sync_api import sync_playwright
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
            def create(name):
                page.goto(origin+'/dashboard/accounts/new')
                page.locator('#name').fill(name)
                page.locator('#bank').fill('Synthetic CSV bank')
                page.locator('#opened').fill('2026-04-06')
                click(page.get_by_role('button',name='Add account',exact=True))
                return page.url
            def upload(url,amount,reuse=False):
                page.goto(url+'/import')
                data=f'Date,Amount,Name,Reference\n2026-04-10,{amount},Own account,Synthetic test\n'.encode()
                page.locator('#statement').set_input_files({'name':'synthetic.csv','mimeType':'text/csv','buffer':data})
                click(page.get_by_role('button',name='Upload and map columns',exact=True))
                if reuse:
                    assert '/preview' in page.url,page.locator('main').inner_text()
                else:
                    assert '/map' in page.url
                    for key,value in dict(date='0',amount='1',payer='2',reference='3').items():
                        page.locator('#map-'+key).select_option(value)
                    click(page.get_by_role('button',name='Validate and preview',exact=True))
                page.locator('[name=confirm]').check()
                click(page.get_by_role('button',name='Confirm import',exact=True))
                assert '1 transactions imported' in page.locator('main').inner_text()
            earlier=create('Earlier bank <example>')
            upload(earlier,'-987.65')
            upload(earlier,'-102',reuse=True)
            click(page.get_by_role('link',name='Correct this import',exact=True))
            correction=page.url
            def open_form():
                box=page.locator('article.checklist-step details').first
                box.locator('summary').click()
                return box
            box=open_form()
            box.locator('[name=amount]').fill('103.25')
            box.locator('[name=reason]').fill('Corrected synthetic amount')
            box.locator('[name=confirm]').check()
            click(box.get_by_role('button',name='Save corrected details',exact=True))
            assert '103.25' in page.locator('main').inner_text()
            box=open_form()
            box.locator('[name=reason]').fill('Synthetic duplicate')
            box.locator('[name=confirm]').check()
            click(box.get_by_role('button',name='Exclude duplicate / incorrect payment',exact=True))
            assert 'REVERSED' in page.locator('main').inner_text()
            box=open_form()
            box.locator('[name=reason]').fill('Accidental exclusion')
            box.locator('[name=confirm]').check()
            click(box.get_by_role('button',name='Restore excluded payment',exact=True))
            assert 'SETTLED' in page.locator('main').inner_text()
            bulk=page.locator('details').filter(has=page.get_by_text('Exclude several duplicate or incorrect payments',exact=True))
            bulk.locator('summary').click()
            bulk.locator('[name=selected]').first.check()
            bulk.locator('[name=reason]').fill('Synthetic batch exclusion')
            bulk.locator('[name=confirm]').check()
            click(bulk.get_by_role('button',name='Exclude selected payments',exact=True))
            assert 'REVERSED' in page.locator('main').inner_text()
            box=open_form()
            box.locator('[name=reason]').fill('Restore after batch check')
            box.locator('[name=confirm]').check()
            click(box.get_by_role('button',name='Restore excluded payment',exact=True))
            receiving=create('Receiving bank')
            upload(receiving,'987.65')
            page.goto(earlier+'/transfers')
            layout()
            assert 'Receiving bank' in page.locator('main').inner_text()
            pair=page.locator('article.checklist-step').filter(has=page.get_by_text('Receiving bank',exact=False)).first
            pair.locator('[name=confirm]').check()
            click(pair.get_by_role('button',name='Confirm transfer pair',exact=True))
            assert 'No eligible pairs found' in page.locator('main').inner_text()
            page.goto(correction)
            page.get_by_text('Recent account correction history',exact=True).click()
            assert 'Accidental exclusion' in page.locator('main').inner_text()
            assert 'REAL-ISOLATION-SENTINEL' not in page.locator('main').inner_text()
            layout()
            if width==390:
                page.screenshot(path='/tmp/toms-statement-review-mobile.png',full_page=True)
            assert fixture.bank.call_count==0
            assert not errors,errors
            page.close()
            print(f'PASS {width}px: reusable mapping, correction/exclusion/restore, own-account pair and audit history')
        finally:
            fixture.doCleanups()
    browser.close()
