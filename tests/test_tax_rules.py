"""Official publication validation and cache failures never replace reviewed rules."""
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

import httpx
from services.tax import rules


def publication():
    approved = rules.reviewed_rules()
    rows = [['Band', 'Taxable income', 'Tax rate']] + approved['published_bands']
    table = '<table>' + ''.join('<tr>' + ''.join('<td>' + cell + '</td>' for cell in row) + '</tr>' for row in rows) + '</table>'
    body = ('<p>The current tax year is from 6 April 2026 to 5 April 2027.</p>'
            '<p>The standard Personal Allowance is £12,570, an allowance.</p>'
            '<p>Your personal allowance goes down by £1 for every £2 that your adjusted net income is above £100,000.</p>'
            '<p>Your allowance is zero if your income is £125,140 or above.</p>' + table)
    return {'base_path': '/income-tax-rates', 'details': {'parts': [{'slug': 'current-rates-and-allowances', 'body': body}]}}


class TaxRuleTests(unittest.TestCase):
    def test_current_publication_and_pinned_dates(self):
        approved = rules.reviewed_rules()
        self.assertEqual(approved['tax_year'], '2026-27')
        self.assertEqual(approved['starts_on'], '2026-04-06')
        self.assertEqual(len(rules.validate_publication(publication(), approved)), 64)

    def test_changed_values_year_or_shape_are_rejected(self):
        approved = rules.reviewed_rules()
        for before, after in (('2026 to 5 April 2027', '2027 to 5 April 2028'), ('20%', '21%'), ('£12,570', '£13,000'), ('£100,000', '£110,000'), ('£1 for every £2', '£1 for every £3')):
            payload = publication()
            payload['details']['parts'][0]['body'] = payload['details']['parts'][0]['body'].replace(before, after)
            with self.subTest(change=before), self.assertRaises(ValueError):
                rules.validate_publication(payload, approved)
        for payload in ({}, {'base_path': '/income-tax-rates', 'withdrawn_notice': {'explanation': 'Withdrawn'}}, {'base_path': '/different-page'}):
            with self.assertRaises(ValueError):
                rules.validate_publication(payload, approved)

    def test_cache_success_daily_refresh_and_failed_check_retains_verified_rules(self):
        with TemporaryDirectory() as directory, patch.object(rules, 'fetch_publication', return_value=publication()) as fetch:
            first = rules.refresh_rules(directory)
            self.assertEqual(first['status'], 'verified')
            self.assertEqual(rules.refresh_rules(directory), first)
            fetch.assert_called_once()
            fetch.side_effect = httpx.ReadTimeout('timeout')
            failed = rules.refresh_rules(directory, force=True)
            self.assertEqual(failed['status'], 'unavailable')
            self.assertEqual(failed['verified_at'], first['verified_at'])
            self.assertEqual(rules.cached_status(directory), failed)
            self.assertEqual(rules.reviewed_rules()['published_bands'][1][2], '20%')

    def test_changed_publication_is_flagged_and_does_not_replace_rules(self):
        payload = publication()
        payload['details']['parts'][0]['body'] = payload['details']['parts'][0]['body'].replace('20%', '30%')
        original = rules.RULE_FILE.read_bytes()
        with TemporaryDirectory() as directory, patch.object(rules, 'fetch_publication', return_value=payload):
            self.assertEqual(rules.refresh_rules(directory)['status'], 'needs_review')
        self.assertEqual(rules.RULE_FILE.read_bytes(), original)

    def test_fetch_is_fixed_unauthenticated_bounded_and_rejects_redirects(self):
        requests = []
        def handler(request):
            requests.append(request)
            return httpx.Response(200, json=publication())
        client = httpx.Client(transport=httpx.MockTransport(handler))
        with patch.object(rules.httpx, 'Client', return_value=client) as factory:
            self.assertEqual(rules.fetch_publication(), publication())
            self.assertFalse(factory.call_args.kwargs['follow_redirects'])
            self.assertFalse(factory.call_args.kwargs['trust_env'])
        self.assertEqual(str(requests[0].url), rules.CONTENT_URL)
        self.assertNotIn('authorization', requests[0].headers)
        for response in (httpx.Response(302, headers={'Location': 'http://localhost/secret'}), httpx.Response(200, content=b'x' * (rules.MAX_BYTES + 1))):
            client = httpx.Client(transport=httpx.MockTransport(lambda request: response))
            with patch.object(rules.httpx, 'Client', return_value=client), self.assertRaises(ValueError):
                rules.fetch_publication()

    def test_changed_reviewed_allowances_dates_or_formula_cannot_be_verified(self):
        for changes in ({'personal_allowance_minor': 1300000}, {'allowance_taper_starts_minor': 11000000},
                        {'allowance_zero_at_minor': 12600000}, {'ends_on': '2028-04-05'},
                        {'allowance_taper_reduction': 'a different formula'}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                rules.validate_publication(publication(), dict(rules.reviewed_rules(), **changes))

    def test_corrupt_cache_is_ignored(self):
        with TemporaryDirectory() as directory:
            Path(directory, 'tax-rule-check.json').write_text('not JSON')
            self.assertEqual(rules.cached_status(directory), {})

    def test_modified_rule_file_invalidates_old_verification_even_with_same_version(self):
        import json
        with TemporaryDirectory() as directory, patch.object(rules, 'fetch_publication', return_value=publication()) as fetch:
            original = rules.reviewed_rules()
            rules.refresh_rules(directory)
            updated = dict(original, reviewed_on='2026-10-07')
            rule_file = Path(directory, 'rules.json')
            rule_file.write_text(json.dumps(updated))
            with patch.object(rules, 'RULE_FILE', rule_file):
                self.assertEqual(rules.cached_status(directory), {})
                result = rules.refresh_rules(directory)
                self.assertEqual(result['status'], 'verified')
                self.assertEqual(result['rules_sha256'], rules.rule_hash(updated))
            self.assertEqual(fetch.call_count, 2)

    def test_bad_cache_fields_future_dates_and_oversized_cache_are_ignored(self):
        import json
        with TemporaryDirectory() as directory, patch.object(rules, 'fetch_publication', return_value=publication()):
            valid = rules.refresh_rules(directory)
            path = Path(directory, 'tax-rule-check.json')
            for changes in ({'checked_at': []}, {'checked_at': '2999-01-01T00:00:00+00:00'},
                            {'verified_at': '2999-01-01T00:00:00+00:00'}, {'content_sha256': 'bad'},
                            {'rules_sha256': 'bad'}, {'message': []}, {'message': 'x' * 513}):
                with self.subTest(changes=changes):
                    path.write_text(json.dumps(dict(valid, **changes)))
                    self.assertEqual(rules.cached_status(directory), {})
            path.write_bytes(b'x' * 8193)
            self.assertEqual(rules.cached_status(directory), {})

    def test_slow_drip_response_and_invalid_json_are_bounded(self):
        class Drip(httpx.SyncByteStream):
            def __iter__(self):
                yield b'x' * 16384
                yield b'x' * 16384
        client = httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(200, stream=Drip())))
        with patch.object(rules.httpx, 'Client', return_value=client), patch.object(rules.time, 'monotonic', side_effect=[0, 1, 21]), self.assertRaises(ValueError):
            rules.fetch_publication()
        with TemporaryDirectory() as directory, patch.object(rules, 'fetch_publication', side_effect=ValueError('Invalid upstream data')):
            self.assertEqual(rules.refresh_rules(directory)['status'], 'needs_review')

    def test_hidden_templates_cannot_supply_missing_allowance_text(self):
        payload = publication()
        body = payload['details']['parts'][0]['body']
        phrase = '<p>The standard Personal Allowance is £12,570, an allowance.</p>'
        payload['details']['parts'][0]['body'] = body.replace(phrase, '<template>' + phrase + '</template>')
        with self.assertRaises(ValueError):
            rules.validate_publication(payload, rules.reviewed_rules())

    def test_malformed_publication_shapes_fail_safely(self):
        for details in (None, {'parts': None}, {'parts': ['invalid']}, {'parts': [{'slug': 'current-rates-and-allowances', 'body': []}]}):
            with TemporaryDirectory() as directory, patch.object(rules, 'fetch_publication', return_value={'base_path': '/income-tax-rates', 'details': details}):
                self.assertEqual(rules.refresh_rules(directory)['status'], 'needs_review')

    def test_cache_write_failure_keeps_previous_check_and_removes_temporary_file(self):
        with TemporaryDirectory() as directory, patch.object(rules, 'fetch_publication', return_value=publication()):
            original = rules.refresh_rules(directory)
            with patch.object(rules.os, 'replace', side_effect=OSError('disk failure')), self.assertRaises(OSError):
                rules.refresh_rules(directory, force=True)
            self.assertEqual(rules.cached_status(directory), original)
            self.assertEqual([path.name for path in Path(directory).iterdir()], ['tax-rule-check.json'])

    def test_background_check_does_not_queue_duplicates(self):
        from threading import Event
        entered, release, finished = Event(), Event(), Event()
        def refresh(directory):
            entered.set()
            release.wait(5)
            finished.set()
        with patch.object(rules, 'refresh_rules', side_effect=refresh) as check:
            rules.start_rule_check('/unused')
            self.assertTrue(entered.wait(2))
            rules.start_rule_check('/unused')
            check.assert_called_once()
            release.set()
            self.assertTrue(finished.wait(2))

    def test_compressed_responses_are_rejected_before_decoding(self):
        client = httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(
            200, headers={'Content-Encoding': 'gzip'}, stream=httpx.ByteStream(b'not a gzip stream'))))
        with patch.object(rules.httpx, 'Client', return_value=client), self.assertRaisesRegex(ValueError, 'Compressed'):
            rules.fetch_publication()
