"""Fetch official UK mileage publications daily; never silently replace reviewed rates."""
import hashlib
import json
import os
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from tempfile import NamedTemporaryFile
from threading import Lock

import httpx
from services.tax.rules import OfficialContent, fetch_publication, rule_hash

RULE_FILE = Path(__file__).resolve().parents[2] / 'data/tax_rules/uk-mileage-2026-27.json'
SOURCES = (('/simpler-income-tax-simplified-expenses', 'vehicles'),
           ('/tax-relief-for-employees', 'vehicles-you-use-for-work'))
_lock = Lock()


def reviewed_rules():
    return json.loads(RULE_FILE.read_text())


def validate_publication(payload, rules, employee=False):
    path, slug = SOURCES[int(employee)]
    if not isinstance(payload, dict) or payload.get('base_path') != path or payload.get('withdrawn_notice'):
        raise ValueError('Mileage publication is missing or withdrawn.')
    parts = payload.get('details', {}).get('parts', [])
    matches = [part for part in parts if isinstance(part, dict) and part.get('slug') == slug]
    if len(matches) != 1 or not isinstance(matches[0].get('body'), str):
        raise ValueError('Mileage publication format changed.')
    parser = OfficialContent()
    parser.feed(matches[0]['body'])
    rows = parser.rows
    if not rows or len(rows[0]) != 3 or not re.search(r'2026\s*(?:to|[-–])\s*2027', rows[0][1]):
        raise ValueError('Mileage publication covers a different year.')
    expected = [['Cars and goods vehicles first 10,000 miles', f"{rules['car_van_first_pence']}p", '45p'],
                ['Cars and goods vehicles after 10,000 miles', f"{rules['car_van_after_pence']}p", '25p'],
                ['Motorcycles', f"{rules['motorcycle_pence']}p", '24p']]
    if employee:
        expected.append(['Bicycles', f"{rules['employee_bicycle_pence']}p", '20p'])
    if rows[1:] != expected or rules['threshold_miles'] != 10000 or (rules['starts_on'], rules['ends_on'], rules['tax_year']) != ('2026-04-06', '2027-04-05', '2026-27'):
        raise ValueError('Published mileage rates differ from reviewed rules.')
    return hashlib.sha256(matches[0]['body'].encode()).hexdigest()


def cached_status(instance_path):
    try:
        with (Path(instance_path) / 'mileage-rule-check.json').open('rb') as file:
            body = file.read(8193)
        if len(body) > 8192:
            return {}
        result = json.loads(body)
        if not isinstance(result, dict) or result.get('rules_sha256') != rule_hash(reviewed_rules()):
            return {}
        checked = datetime.fromisoformat(result['checked_at'])
        if checked.tzinfo is None or checked > datetime.now(timezone.utc) + timedelta(minutes=5):
            return {}
        if result.get('status') not in ('verified', 'needs_review', 'unavailable'):
            return {}
        if not isinstance(result.get('message'), str) or len(result['message']) > 512:
            return {}
        return result
    except (OSError, KeyError, TypeError, ValueError):
        return {}


def refresh_rules(instance_path, *, force=False):
    with _lock:
        previous = cached_status(instance_path)
        if previous and not force and timedelta(0) <= datetime.now(timezone.utc) - datetime.fromisoformat(previous['checked_at']) < timedelta(days=1):
            return previous
        rules = reviewed_rules()
        result = dict(rules_sha256=rule_hash(rules), checked_at=datetime.now(timezone.utc).isoformat(), status='unavailable')
        try:
            hashes = [validate_publication(fetch_publication('https://www.gov.uk/api/content' + path), rules, bool(index))
                      for index, (path, _) in enumerate(SOURCES)]
        except httpx.HTTPError:
            result['message'] = 'HMRC is unavailable; reviewed mileage rules are retained.'
        except (ValueError, TypeError, AttributeError, KeyError):
            result.update(status='needs_review', message='Published mileage rules changed or could not be validated. Review required.')
        else:
            result.update(status='verified', message='Both HMRC mileage publications match the reviewed UK 2026–27 rates.', content_sha256=hashes)
        directory = Path(instance_path)
        directory.mkdir(parents=True, exist_ok=True)
        with NamedTemporaryFile(mode='w', dir=directory, delete=False) as file:
            json.dump(result, file)
            name = file.name
        try:
            os.replace(name, directory / 'mileage-rule-check.json')
        finally:
            if os.path.exists(name):
                os.unlink(name)
        return result
