"""Owner ZIP download and responsive navigation with isolated sample records."""
import io
import json
import logging
from pathlib import Path
import sys
from threading import Thread
from zipfile import ZipFile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from playwright.sync_api import sync_playwright
from werkzeug.serving import make_server
from app import app
from test_test_data import TestDataTests

logging.getLogger('werkzeug').setLevel(logging.ERROR)
fixture = TestDataTests()
try:
    fixture.setUp()
    fixture.toggle(True)
    server = make_server('127.0.0.1', 0, app, threaded=True)
    worker = Thread(target=server.serve_forever, daemon=True)
    worker.start()
    fixture.addCleanup(server.server_close)
    fixture.addCleanup(worker.join)
    fixture.addCleanup(server.shutdown)
    origin = f'http://127.0.0.1:{server.server_port}'
    cookie = fixture.client.get_cookie('session')
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        for width in (320, 390, 768, 1280):
            context = browser.new_context(viewport={'width': width, 'height': 900}, accept_downloads=True)
            context.add_cookies([{'name': 'session', 'value': cookie.value, 'url': origin}])
            page = context.new_page()
            errors = []
            page.on('pageerror', lambda error: errors.append(str(error)))
            page.goto(origin + '/dashboard/checklist')
            page.get_by_role('link', name='Export tax-year records').click()
            assert page.get_by_role('heading', name='Tax-year export').is_visible()
            assert page.evaluate('document.documentElement.scrollWidth <= innerWidth'), width
            with page.expect_download() as downloaded:
                page.get_by_role('link', name='Download 2026-27 ZIP').click()
            download = downloaded.value
            assert download.suggested_filename == 'TOMS-SAMPLE-tax-2026-27.zip'
            archive = ZipFile(io.BytesIO(Path(download.path()).read_bytes()))
            data = json.loads(archive.read('report.json'))
            assert data['sample_data'] is True
            assert data['counts']['streams'] == 3
            assert 'REAL-INCOME-SENTINEL' not in json.dumps(data)
            page.get_by_role('link', name='Tax estimate', exact=True).last.click()
            page.get_by_role('link', name='Export tax-year records').click()
            assert page.get_by_role('heading', name='Tax-year export').is_visible()
            assert not errors, errors
            page.close()
            page = context.new_page()
            page.on('pageerror', lambda error: errors.append(str(error)))
            page.set_content(archive.read('report.html').decode())
            assert 'SAMPLE DATA ONLY' in page.locator('body').inner_text()
            assert page.get_by_role('heading', name='Annual planning calculation').is_visible()
            assert page.evaluate('document.documentElement.scrollWidth <= innerWidth'), width
            assert not errors, errors
            context.close()
            print(f'PASS {width}px: checklist and estimate links, sample ZIP download, layout and browser errors')
        browser.close()
    fixture.bank.assert_not_called()
finally:
    fixture.doCleanups()
