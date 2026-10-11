"""Focused entry, compact attention, complete history and accessible disclosures."""
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
import logging
from pathlib import Path
import sys
from threading import Thread

ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT),str(ROOT/'tests')]
from playwright.sync_api import sync_playwright
from sqlalchemy.orm import Session
from werkzeug.serving import make_server
from app import app
from models import IncomeShift, MileageEntry
from services.web.test_data import sample_id
from test_test_data import TestDataTests

logging.getLogger('werkzeug').setLevel(logging.ERROR)
with sync_playwright() as p:
    browser=p.chromium.launch(headless=True)
    for width in (320,390,768,1024,1280):
        fixture=TestDataTests()
        try:
            fixture.setUp()
            fixture.start_sync.return_value=False
            assert fixture.toggle(True).status_code==303
            engine=fixture.admin.execution_options(schema_translate_map={'toms':fixture.demo_schema})
            with Session(engine) as saved, saved.begin():
                for index in range(25):
                    start=datetime(2026,5,1,8,tzinfo=timezone.utc)+timedelta(days=index)
                    saved.add(IncomeShift(id=sample_id(100+index),income_stream_id=sample_id(21),
                        starts_at=start,ends_at=start+timedelta(hours=2),unpaid_break_minutes=0,payment_mode='hourly',
                        hourly_rate_minor=1250,gross_minor=2500,currency='GBP',is_overtime=True,notes=f'Work history {index}'))
                    saved.add(MileageEntry(id=sample_id(200+index),income_stream_id=sample_id(22),
                        journey_date=start.date(),location='england',vehicle_type='car_van',vehicle_key='AB12CDE',
                        miles=Decimal('500'),purpose=f'Journey history {index}',start_postcode='SW1A 1AA',
                        end_postcode='SW1A 2AA',reimbursed_minor=0))
            server=make_server('127.0.0.1',0,app,threaded=True)
            worker=Thread(target=server.serve_forever,daemon=True);worker.start()
            fixture.addCleanup(server.server_close);fixture.addCleanup(worker.join,5);fixture.addCleanup(server.shutdown)
            origin=f'http://127.0.0.1:{server.server_port}'
            context=browser.new_context(viewport={'width':width,'height':900},java_script_enabled=width!=390)
            cookie=fixture.client.get_cookie(app.config['SESSION_COOKIE_NAME'])
            context.add_cookies([dict(name=cookie.key,value=cookie.value,url=origin)])
            page=context.new_page();errors=[]
            page.on('pageerror',lambda error:errors.append(str(error)))
            def layout():
                assert page.evaluate('document.documentElement.scrollWidth <= innerWidth'),width
            def go(path):
                response=page.goto(origin+path);assert response.status==200,(path,response.status);layout()
            def submit(button):
                with page.expect_navigation():button.click()
                layout()
            go('/dashboard/income-streams?setup=1')
            assert page.locator('#your-streams-heading').count()==0
            assert page.locator('#start-source-heading').bounding_box()['y']<900
            assert page.get_by_role('complementary',name='Test mode',exact=True).is_visible()
            disclosure=page.locator('.test-data-notice summary')
            disclosure.focus();page.keyboard.press('Enter')
            assert page.locator('.test-data-notice details').evaluate('(d)=>d.open')
            disclosure.click()
            if width!=390:
                page.locator('#income-alert-bar').wait_for(state='visible')
                assert page.locator('#income-alert-bar').bounding_box()['height']<=80
                assert 'Demo employment' not in page.locator('#income-alert-summary').inner_text()
                page.get_by_role('button',name='View income notices',exact=True).click()
                assert 'Demo employment' in page.locator('#income-notices-dialog').inner_text()
                page.keyboard.press('Escape')
            page.screenshot(path=f'/tmp/toms-focused-setup-{width}.png',full_page=True)
            go(f'/dashboard/income-streams/{sample_id(21)}/shifts?add=1&return_to=source&source={sample_id(21)}')
            assert not page.locator('#entry-history').evaluate('(d)=>d.open')
            page.locator('#shift-start').fill('2026-05-28T08:00');page.locator('#shift-end').fill('2026-05-28T10:00')
            page.locator('#shift-payment-mode').select_option('hourly');page.locator('#shift-rate').fill('12.50')
            submit(page.get_by_role('button',name='Add overtime',exact=True))
            assert page.url.endswith('/income-streams/'+sample_id(21)),page.url
            go(f'/dashboard/income-streams/{sample_id(21)}/shifts?history_page=2')
            assert page.locator('#entry-history .stream-payment').count()==7
            assert 'GBP 850.00' in page.locator('main').inner_text()
            assert '27 work records across all saved history' in page.locator('main').inner_text()
            go(f'/dashboard/deductions?type=mileage&reuse={sample_id(200)}&return_to=source&source={sample_id(22)}')
            assert not page.locator('#entry-history').evaluate('(d)=>d.open')
            assert page.locator('#mileage-stream').input_value()==sample_id(22)
            assert page.locator('#mileage-miles').input_value()==''
            assert not page.locator('[name=eligible]').is_checked()
            page.locator('#journey-date').fill('2026-05-28');page.locator('#mileage-miles').fill('12')
            page.locator('#mileage-reimbursed').fill('0');page.locator('[name=eligible]').check()
            page.screenshot(path=f'/tmp/toms-focused-mileage-{width}.png',full_page=True)
            submit(page.get_by_role('button',name='Save mileage',exact=True))
            assert page.url.endswith('/income-streams/'+sample_id(22)),page.url
            go('/dashboard/deductions?history_page=2&history_source='+sample_id(22))
            assert page.locator('#entry-history .stream-payment').count()==6
            assert 'GBP 6,128.00' in page.locator('#entry-history').inner_text()
            page.locator('#history-from').fill('2026-05-28');page.locator('#history-to').fill('2026-05-28')
            submit(page.get_by_role('button',name='Filter history',exact=True))
            assert page.locator('#entry-history .stream-payment').count()==1
            assert 'GBP 6,128.00' in page.locator('#entry-history').inner_text()
            go('/dashboard/tax-estimate')
            assert page.get_by_role('region',name='Next planning action').is_visible()
            assert not page.locator('.planning-detail').first.evaluate('(d)=>d.open')
            page.locator('.planning-detail > summary').first.focus();page.keyboard.press('Enter')
            assert page.get_by_role('heading',name='How the tax is calculated',exact=True).is_visible()
            go('/dashboard/reconciliation')
            assert page.get_by_role('heading',name='Receipts needing review (3)',exact=True).is_visible()
            assert page.get_by_role('heading',name='By income source and currency',exact=True).is_hidden()
            page.get_by_text('All source totals and evidence',exact=True).click()
            assert page.get_by_role('heading',name='By income source and currency',exact=True).is_visible()
            page.screenshot(path=f'/tmp/toms-focused-matching-{width}.png',full_page=True)
            go('/dashboard?account=all')
            assert page.locator('.ledger-editing').first.evaluate('(d)=>!d.open')
            assert page.locator('.guided-review-link').first.is_visible()
            page.locator('.ledger-editing > summary').first.click()
            assert page.locator('.edit-classification').first.is_visible()
            assert not errors,errors
            assert fixture.live_value('SELECT count(*) FROM SCHEMA.income_shifts')==0
            assert fixture.live_value('SELECT count(*) FROM SCHEMA.mileage_entries')==0
            context.close()
            print(f'PASS {width}px: focused setup, compact attention, accessible details, source returns and complete paged history',flush=True)
        finally:
            fixture.doCleanups()
    browser.close()
