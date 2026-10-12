"""Owner evidence uploads, downloads, preview and archive across screen sizes."""
import io
import logging
from pathlib import Path
import sys
from threading import Thread
from werkzeug.serving import make_server
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from playwright.sync_api import sync_playwright
from app import app
from test_test_data import TestDataTests
from browser_owner import install_owner_cookie

logging.getLogger('werkzeug').setLevel(logging.ERROR)
buffer = io.BytesIO()
Image.new('RGB', (800, 1200), 'white').save(buffer, format='PNG')
with sync_playwright() as playwright:
    browser = playwright.chromium.launch(headless=True)
    for width in (320, 390, 768, 1280):
        fixture = TestDataTests()
        try:
            fixture.setUp()
            fixture.toggle(True)
            server = make_server('127.0.0.1', 0, app, threaded=True)
            worker = Thread(target=server.serve_forever, daemon=True)
            worker.start()
            fixture.addCleanup(server.server_close)
            fixture.addCleanup(worker.join, 5)
            fixture.addCleanup(server.shutdown)
            origin = f'http://127.0.0.1:{server.server_port}'
            page = browser.new_page(viewport={'width': width, 'height': 900}, java_script_enabled=width != 390)
            install_owner_cookie(page.context, fixture, origin)
            errors = []
            page.on('pageerror', lambda error: errors.append(str(error)))
            def layout():
                assert page.evaluate('document.documentElement.scrollWidth <= innerWidth'), (width, page.url)
            def click(locator):
                with page.expect_navigation():
                    locator.click()
                layout()
            page.goto(origin+'/dashboard/deductions')
            click(page.get_by_role('link', name='Supporting documents', exact=True).first)
            page.locator('#evidence-file').set_input_files({'name': 'synthetic-receipt.png', 'mimeType': 'image/png', 'buffer': buffer.getvalue()})
            page.locator('#evidence-notes').fill('Synthetic receipt <example>')
            click(page.get_by_role('button', name='Attach document', exact=True))
            assert 'Document attached.' in page.locator('main').inner_text()
            page.locator('img.evidence-preview').wait_for(state='visible')
            assert page.locator('img.evidence-preview').evaluate('image => image.complete && image.naturalWidth > 0')
            with page.expect_download() as downloaded:
                page.get_by_role('link', name='Download original', exact=True).click()
            assert Path(downloaded.value.path()).read_bytes() == buffer.getvalue()
            page.locator('#evidence-file').set_input_files({'name': 'duplicate.png', 'mimeType': 'image/png', 'buffer': buffer.getvalue()})
            click(page.get_by_role('button', name='Attach document', exact=True))
            assert 'already attached' in page.locator('main').inner_text()
            page.get_by_text('Archive document', exact=True).click()
            page.locator('[name=confirm]').check()
            click(page.get_by_role('button', name='Archive', exact=True))
            assert 'Archived' in page.locator('main').inner_text()
            page.get_by_text('Restore document', exact=True).click()
            page.locator('[name=confirm]').check()
            click(page.get_by_role('button', name='Restore', exact=True))
            assert 'Archived' not in page.locator('main').inner_text()
            if width == 390:
                page.screenshot(path='/tmp/toms-evidence-mobile.png', full_page=True)
            page.goto(origin+'/dashboard/tax-export')
            layout()
            with page.expect_download() as downloaded:
                page.get_by_role('link', name='Download ZIP including supporting files', exact=True).click()
            from zipfile import ZipFile
            with ZipFile(downloaded.value.path()) as archive:
                originals = [name for name in archive.namelist() if name.startswith('documents/')]
                assert len(originals) == 1
                assert archive.read(originals[0]) == buffer.getvalue()
            assert not errors, errors
            fixture.bank.assert_not_called()
            page.close()
            print(f'PASS {width}px: evidence upload, duplicate detection, preview, download, archive/restore and ZIP')
        finally:
            fixture.doCleanups()
    browser.close()
