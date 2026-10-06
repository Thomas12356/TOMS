"""Optional Chromium layout and input checks with synthetic income data."""
import sys
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import urlparse, parse_qs

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from app import app
from flask import render_template
from playwright.sync_api import sync_playwright
from services.transactions.income import TAX_TREATMENTS
from services.transactions.income_streams import STREAM_KINDS, FORECAST_PERIODS, FORECAST_CURRENCIES, TAX_YEARS, active_days, annual_gross, holiday_amount
from services.transactions.income_form import display_amount
from routes.dashboard import format_amount
from services.transactions.income_form import FIELDS

stream = SimpleNamespace(id='11111111-1111-4111-8111-111111111111', name='My job', kind='employed', archived=False, expected_gross_minor=100050, expected_gross_period='monthly', expected_gross_currency='GBP', unpaid_holiday_weeks=0, unpaid_holiday_unit='weeks', forecast_tax_year='2026-27', forecast_starts_on=None, forecast_ends_on=None)

with app.test_request_context('/dashboard/income'):
    html = render_template('income.html', transaction=SimpleNamespace(currency='GBP',
        counterparty_name='Example employer with a longer name', reference='Example reference'),
        amount='GBP 25.00', version='a' * 64, values=dict.fromkeys(FIELDS, ''), streams=[stream], stream_kinds=STREAM_KINDS,
        treatments=TAX_TREATMENTS, decimal_places=2, currency_changed=False,
        needs_review=False, back_url='/dashboard?account=example&page=2', error=None)

with app.test_request_context('/dashboard/income-streams'):
    stream_html = render_template('income_streams.html',
        streams=[stream], stream_kinds=STREAM_KINDS, counts={stream.id: 1},
        create_values={'name': '', 'kind': '', 'expected_gross': '', 'expected_gross_period': 'yearly', 'expected_gross_currency': 'GBP', 'unpaid_holiday': '0', 'unpaid_holiday_unit': 'weeks', 'forecast_tax_year': '2026-27', 'forecast_starts_on': '', 'forecast_ends_on': ''},
        forecast_periods=FORECAST_PERIODS, forecast_currencies=FORECAST_CURRENCIES, tax_years=TAX_YEARS, active_days=active_days,
        format_amount=format_amount, display_amount=display_amount, annual_gross=annual_gross, holiday_amount=holiday_amount,
        editing_id=None, editing_values={}, error=None, selected_name=stream.name, selected_stream=stream.id, rows=[dict(source='Example employer with a very long source name',
        kind='Employment', date='06 Oct 2026', amount='GBP 25.00', needs_review=True, edit_url='/income')],
        page=SimpleNamespace(pages=1), current_user=SimpleNamespace(is_authenticated=True))

with sync_playwright() as playwright:
    browser = playwright.chromium.launch(headless=True)
    for width in (320, 390, 768, 1280):
        page = browser.new_page(viewport={'width': width, 'height': 850})
        def respond(route):
            path = urlparse(route.request.url).path
            if path.startswith('/static/'):
                route.fulfill(path=str(ROOT / path.lstrip('/')))
            else:
                route.fulfill(body=html, content_type='text/html')
        page.route('http://toms.test/**', respond)
        page.goto('http://toms.test/dashboard/income')
        assert page.evaluate('document.documentElement.scrollWidth <= innerWidth'), width
        for selector in ('#income-stream', '#source-name', '#tax-treatment', '#gross', '#tax-deducted', '#adjustment', '#adjustment-notes'):
            box = page.locator(selector).bounding_box()
            assert box['x'] >= 0 and box['x'] + box['width'] <= width, (width, selector)
        page.locator('#income-stream').select_option(stream.id)
        page.locator('#tax-treatment').select_option('paye')
        page.locator('#gross').fill('30.00')
        page.locator('#tax-deducted').fill('5.00')
        assert page.locator('form').evaluate('(form) => form.checkValidity()')
        assert page.locator('input[name=version]').input_value() == 'a' * 64
        assert page.get_by_role('link', name='Cancel').get_attribute('href').endswith('page=2')
        page.close()
    for width in (320, 390, 1280):
        page = browser.new_page(viewport={'width': width, 'height': 850})
        def respond_stream(route):
            path = urlparse(route.request.url).path
            if path.startswith('/static/'):
                route.fulfill(path=str(ROOT / path.lstrip('/')))
            else:
                route.fulfill(body=stream_html, content_type='text/html')
        page.route('http://toms.test/**', respond_stream)
        page.goto('http://toms.test/dashboard/income-streams')
        assert page.evaluate('document.documentElement.scrollWidth <= innerWidth'), width
        assert page.locator('.stream-manage').count() == 1
        page.locator('#stream-name').fill('New business')
        page.locator('#stream-kind').select_option('cis')
        page.locator('#stream-gross').fill('1234.56')
        page.locator('#stream-holiday').fill('2.5')
        page.locator('#stream-holiday-unit').select_option('days')
        page.locator('#stream-starts').fill('2026-10-06')
        page.locator('#stream-ends').fill('2027-03-31')
        for period in ('weekly', 'monthly', 'yearly'):
            page.locator('#stream-period').select_option(period)
        page.locator('#stream-currency').select_option('USD')
        assert page.locator('.stream-create form').evaluate('(form) => form.checkValidity()')
        page.locator('.stream-manage summary').click()
        assert page.get_by_role('button', name='Save changes', exact=True).is_visible()
        assert page.get_by_role('button', name='Archive', exact=True).is_visible()
        if width < 640:
            page.get_by_role('button', name='Toggle navigation').click()
        assert page.get_by_role('link', name='Income streams', exact=True).is_visible()
        assert page.get_by_role('link', name='Edit income details').is_visible()
        assert page.locator('#forecast-' + stream.id + '-gross').input_value() == '1000.50'
        assert page.locator('#forecast-' + stream.id + '-period').input_value() == 'monthly'
        assert 'GBP 12,006.00 for 2026-27' in page.locator('main').inner_text()
        with page.expect_request(lambda request: request.method == 'POST') as submitted:
            page.get_by_role('button', name='Create stream', exact=True).click()
        payload = parse_qs(submitted.value.post_data)
        assert payload['expected_gross'] == ['1234.56']
        assert payload['expected_gross_period'] == ['yearly']
        assert payload['expected_gross_currency'] == ['USD']
        assert payload['unpaid_holiday'] == ['2.5']
        assert payload['unpaid_holiday_unit'] == ['days']
        assert payload['forecast_tax_year'] == ['2026-27']
        assert payload['forecast_starts_on'] == ['2026-10-06']
        assert payload['forecast_ends_on'] == ['2027-03-31']
        page.close()
    browser.close()
print('Income form and streams browser checks passed at mobile and desktop widths.')

from services.tax.rules import reviewed_rules
with app.test_request_context('/dashboard/tax-rules'):
    tax_html = render_template('tax_rules.html', rules=reviewed_rules(),
        status={'message': 'Published rules match the reviewed values.', 'checked_at': '2026-10-06T13:00:00+00:00', 'verified_at': '2026-10-06T13:00:00+00:00'},
        format_amount=format_amount, current_user=SimpleNamespace(is_authenticated=True))
with sync_playwright() as playwright:
    browser = playwright.chromium.launch(headless=True)
    for width in (320, 390, 768, 1280):
        page = browser.new_page(viewport={'width': width, 'height': 850})
        def respond_tax(route):
            path = urlparse(route.request.url).path
            if path.startswith('/static/'):
                route.fulfill(path=str(ROOT / path.lstrip('/')))
            else:
                route.fulfill(body=tax_html, content_type='text/html')
        page.route('http://toms.test/**', respond_tax)
        page.goto('http://toms.test/dashboard/tax-rules')
        assert page.evaluate('document.documentElement.scrollWidth <= innerWidth'), width
        assert page.get_by_role('heading', name='Tax rules', exact=True).is_visible()
        assert page.get_by_role('button', name='Check GOV.UK now').is_visible()
        assert page.get_by_role('link', name='HMRC published rates').get_attribute('href') == 'https://www.gov.uk/income-tax-rates'
        page.close()
    browser.close()
print('Tax rules page browser checks passed at mobile and desktop widths.')
