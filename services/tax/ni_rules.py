"""Check the official NI publication against pinned, reviewed figures."""
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

from services.tax.national_insurance import reviewed_rules

_lock = Lock()


def validate_publication(payload, rules):
    if not isinstance(payload, dict) or payload.get('base_path') != '/self-employed-national-insurance-rates' or payload.get('withdrawn_notice'):
        raise ValueError('National Insurance publication is missing or withdrawn.')
    body = payload.get('details', {}).get('body')
    if not isinstance(body, str):
        raise ValueError('National Insurance publication format changed.')
    parser = OfficialContent()
    parser.feed(body)
    text = ' '.join(' '.join(parser.text).split())
    money = lambda key: f"£{rules[key] // 100:,}"
    expected = (
        'For tax year 2026 to 2027',
        f"{rules['main_percent']}% on profits over {money('lower_profits_minor')} up to {money('upper_profits_minor')}",
        f"{rules['upper_percent']}% on profits over {money('upper_profits_minor')}",
        f"If your profits are {money('small_profits_minor')} or more a year",
        'you do not have to pay Class 2 contributions',
        f"£{rules['voluntary_class2_weekly_minor'] / 100:.2f} a week",
    )
    if any(not re.search(r'(?<![0-9])' + re.escape(value) + r'(?![0-9])', text) for value in expected) or (rules['starts_on'], rules['ends_on'], rules['tax_year']) != ('2026-04-06', '2027-04-05', '2026-27'):
        raise ValueError('Published NI rules differ from reviewed rules.')
    return hashlib.sha256(body.encode()).hexdigest()


def cached_status(instance_path):
    try:
        with (Path(instance_path) / 'ni-rule-check.json').open('rb') as file:
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
        result = dict(rules_sha256=rule_hash(rules), checked_at=datetime.now(timezone.utc).isoformat(), status='needs_review' if previous.get('status') == 'needs_review' else 'unavailable')
        try:
            hashes = validate_publication(fetch_publication('https://www.gov.uk/api/content/self-employed-national-insurance-rates'), rules)
        except httpx.HTTPError:
            result['message'] = ('HMRC is unavailable. A previously detected rule change still needs review.'
                                 if result['status'] == 'needs_review' else 'HMRC is unavailable; reviewed NI rules are retained.')
        except (ValueError, TypeError, AttributeError, KeyError):
            result.update(status='needs_review', message='Published NI rules changed or could not be validated. Review required.')
        else:
            result.update(status='verified', message='HMRC Class 4 and Class 2 figures match the reviewed UK 2026–27 rates.', content_sha256=hashes)
        directory = Path(instance_path)
        directory.mkdir(parents=True, exist_ok=True)
        with NamedTemporaryFile(mode='w', dir=directory, delete=False) as file:
            json.dump(result, file)
            name = file.name
        try:
            os.replace(name, directory / 'ni-rule-check.json')
        finally:
            if os.path.exists(name):
                os.unlink(name)
        return result
