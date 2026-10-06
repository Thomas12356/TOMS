"""Check pinned UK rules against GOV.UK; remote changes never replace them."""
import hashlib
import json
import os
import re
import time
from datetime import datetime, timedelta, timezone
from html.parser import HTMLParser
from pathlib import Path
from tempfile import NamedTemporaryFile
from threading import Lock, Thread

import httpx

RULE_FILE = Path(__file__).resolve().parents[2] / 'data/tax_rules/uk-ewni-2026-27.json'
CONTENT_URL = 'https://www.gov.uk/api/content/income-tax-rates'
MAX_BYTES = 512 * 1024
MAX_FETCH_SECONDS = 20
_lock = Lock()
_start_lock = Lock()


def reviewed_rules():
    return json.loads(RULE_FILE.read_text())


class OfficialContent(HTMLParser):
    """Extract table cells and visible text; do not execute or render fetched HTML."""
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.rows, self.text, self.cells = [], [], []
        self.cell = None
        self.ignored_depth = 0

    def handle_starttag(self, tag, attrs):
        if tag in ('script', 'style', 'template'):
            self.ignored_depth += 1
        if self.ignored_depth:
            return
        if tag == 'tr':
            self.cells = []
        elif tag in ('td', 'th'):
            self.cell = []

    def handle_data(self, data):
        if self.ignored_depth:
            return
        self.text.append(data)
        if self.cell is not None:
            self.cell.append(data)

    def handle_endtag(self, tag):
        if tag in ('script', 'style', 'template') and self.ignored_depth:
            self.ignored_depth -= 1
            return
        if self.ignored_depth:
            return
        if tag in ('td', 'th') and self.cell is not None:
            self.cells.append(' '.join(''.join(self.cell).split()))
            self.cell = None
        elif tag == 'tr' and self.cells:
            self.rows.append(self.cells)


def validate_publication(payload, rules):
    if not isinstance(payload, dict) or payload.get('base_path') != '/income-tax-rates' or payload.get('withdrawn_notice'):
        raise ValueError('The official publication is missing or withdrawn.')
    details = payload.get('details')
    if not isinstance(details, dict) or not isinstance(details.get('parts'), list) or any(not isinstance(part, dict) for part in details['parts']):
        raise ValueError('The official publication format changed.')
    parts = details['parts']
    matches = [part for part in parts if part.get('slug') == 'current-rates-and-allowances']
    if len(matches) != 1 or not isinstance(matches[0].get('body'), str):
        raise ValueError('The official publication format changed.')
    parser = OfficialContent()
    parser.feed(matches[0]['body'])
    text = ' '.join(' '.join(parser.text).split())
    if 'The current tax year is from 6 April 2026 to 5 April 2027.' not in text:
        raise ValueError('The official page covers a different tax year. New rules need review.')
    if parser.rows != [['Band', 'Taxable income', 'Tax rate']] + rules['published_bands']:
        raise ValueError('Published tax bands differ from the reviewed rules.')
    # The calculation bands must match the published ranges too. Otherwise a
    # correct display table could mask an accidentally edited calculator value.
    try:
        published = rules['published_bands']
        basic_end = int(re.findall(r'£([0-9,]+)', published[1][1])[-1].replace(',', '')) * 100
        higher_end = int(re.findall(r'£([0-9,]+)', published[2][1])[-1].replace(',', '')) * 100
        rates = [int(row[2].removesuffix('%')) for row in published[1:]]
        expected = [dict(upper_minor=basic_end - rules['personal_allowance_minor'], rate_percent=rates[0]),
                    dict(upper_minor=higher_end, rate_percent=rates[1]),
                    dict(upper_minor=None, rate_percent=rates[2])]
    except (KeyError, IndexError, TypeError, ValueError):
        raise ValueError('Reviewed calculation bands need validation.') from None
    if rules['taxable_bands'] != expected:
        raise ValueError('Reviewed calculation bands do not match the published ranges.')

    def money_pattern(field):
        value = rules[field]
        if type(value) is not int or value < 0 or value % 100:
            raise ValueError('Reviewed allowance values need validation.')
        return re.escape(f'£{value // 100:,}')
    if (rules['tax_year'], rules['starts_on'], rules['ends_on']) != ('2026-27', '2026-04-06', '2027-04-05'):
        raise ValueError('Reviewed tax-year dates need validation.')
    if rules['allowance_taper_reduction'] != '1 pound for every 2 pounds above the threshold':
        raise ValueError('Reviewed taper formula needs validation.')
    for pattern in (
        r'The standard Personal Allowance is ' + money_pattern('personal_allowance_minor') + ',',
        r'allowance goes down by £1 for every £2 .* is above ' + money_pattern('allowance_taper_starts_minor') + r'\.',
        r'allowance is zero if your income is ' + money_pattern('allowance_zero_at_minor') + r' or above\.',
    ):
        if not re.search(pattern, text, flags=re.IGNORECASE):
            raise ValueError('Published allowance rules differ or need review.')
    return hashlib.sha256(matches[0]['body'].encode()).hexdigest()


def fetch_publication():
    # Separate unauthenticated client: never send bank credentials or follow redirects.
    started = time.monotonic()
    with httpx.Client(timeout=10, follow_redirects=False, trust_env=False) as client:
        with client.stream('GET', CONTENT_URL, headers={'Accept': 'application/json', 'Accept-Encoding': 'identity', 'User-Agent': 'TOMS-tax-rules/1.0'}) as response:
            if response.status_code != 200:
                raise ValueError('GOV.UK could not be checked; reviewed rules are retained.')
            if response.headers.get('Content-Encoding', 'identity').lower() not in ('', 'identity'):
                raise ValueError('Compressed official responses are not accepted.')
            body = bytearray()
            for chunk in response.iter_bytes(chunk_size=16384):
                if time.monotonic() - started > MAX_FETCH_SECONDS:
                    raise ValueError('The official response exceeded the permitted time.')
                if len(body) + len(chunk) > MAX_BYTES:
                    raise ValueError('The official response exceeded the permitted size.')
                body.extend(chunk)
            return json.loads(body)


def rule_hash(rules):
    return hashlib.sha256(json.dumps(rules, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def cached_status(instance_path):
    path = Path(instance_path) / 'tax-rule-check.json'
    try:
        with path.open('rb') as cache:
            body = cache.read(8193)
        if len(body) > 8192:
            return {}
        result = json.loads(body)
        if not isinstance(result, dict) or result.get('status') not in ('verified', 'needs_review', 'unavailable'):
            return {}
        rules = reviewed_rules()
        if result.get('version') != rules['version'] or result.get('rules_sha256') != rule_hash(rules):
            return {}
        if not isinstance(result.get('message'), str) or len(result['message']) > 512:
            return {}
        checked = datetime.fromisoformat(result['checked_at'])
        if checked.tzinfo is None or checked > datetime.now(timezone.utc) + timedelta(minutes=5):
            return {}
        if result.get('verified_at') is not None:
            verified = datetime.fromisoformat(result['verified_at'])
            if verified.tzinfo is None or verified > checked:
                return {}
        if result['status'] == 'verified' and (not result.get('verified_at') or not re.fullmatch(r'[0-9a-f]{64}', result.get('content_sha256', ''))):
            return {}
        return result
    except (OSError, KeyError, TypeError, ValueError):
        return {}


def refresh_rules(instance_path, *, force=False):
    """Persist verification metadata atomically; pinned rule values remain read-only."""
    with _lock:
        previous = cached_status(instance_path)
        rules = reviewed_rules()
        if not force and previous.get('version') == rules['version']:
            try:
                checked = datetime.fromisoformat(previous['checked_at'])
                if timedelta(0) <= datetime.now(timezone.utc) - checked < timedelta(days=1):
                    return previous
            except (KeyError, TypeError, ValueError):
                pass
        result = dict(version=rules['version'], rules_sha256=rule_hash(rules), checked_at=datetime.now(timezone.utc).isoformat(),
                      verified_at=previous.get('verified_at'), status='unavailable')
        try:
            payload = fetch_publication()
            result['content_sha256'] = validate_publication(payload, rules)
        except (httpx.HTTPError, json.JSONDecodeError):
            result['message'] = 'GOV.UK is unavailable. The reviewed rules are retained.'
        except (ValueError, TypeError, AttributeError):
            result.update(status='needs_review', message='The official publication changed or could not be validated. The reviewed rules are retained.')
        else:
            result.update(status='verified', verified_at=result['checked_at'], message='Published bands and allowance rules match the reviewed 2026–27 rules.')
        directory = Path(instance_path)
        directory.mkdir(parents=True, exist_ok=True)
        temporary_path = None
        try:
            with NamedTemporaryFile(mode='w', dir=directory, delete=False) as temporary:
                temporary_path = temporary.name
                json.dump(result, temporary)
            os.replace(temporary_path, directory / 'tax-rule-check.json')
        finally:
            if temporary_path and os.path.exists(temporary_path):
                os.unlink(temporary_path)
        return result


def start_rule_check(instance_path):
    """Keep page loads responsive; refresh checks at most once per day."""
    if not _start_lock.acquire(blocking=False):
        return
    def work():
        try:
            refresh_rules(instance_path)
        except OSError:
            pass  # A read-only cache never prevents viewing the reviewed rules.
        finally:
            _start_lock.release()
    try:
        Thread(target=work, name='tax-rule-check', daemon=True).start()
    except BaseException:
        _start_lock.release()
        raise
