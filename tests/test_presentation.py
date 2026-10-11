from datetime import datetime, timezone
from unittest import TestCase

from services.web.presentation import date_display, format_amount


class PresentationTests(TestCase):
    def test_money_preserves_unknown_and_integer_precision(self):
        self.assertEqual(format_amount(None, 'GBP'), 'Unavailable')
        self.assertEqual(format_amount(-123456789012345678, 'GBP'), '−GBP 1,234,567,890,123,456.78')
        self.assertEqual(format_amount(123, 'JPY'), 'JPY 123 minor units')
        self.assertEqual(format_amount(0, 'EUR'), 'EUR 0.00')

    def test_summer_midnight_and_today_use_london(self):
        result = date_display(datetime(2026, 7, 1, 23, 30, tzinfo=timezone.utc),
                              now=datetime(2026, 7, 2, 0, 5, tzinfo=timezone.utc))
        self.assertEqual(result, dict(date='02 Jul 2026', day='Today'))

    def test_dst_change_and_winter_dates(self):
        for hour in (0, 1, 2):
            self.assertEqual(date_display(datetime(2026, 10, 25, hour, 30, tzinfo=timezone.utc),
                                         now=datetime(2026, 10, 26, tzinfo=timezone.utc))['date'], '25 Oct 2026')
        self.assertEqual(date_display(datetime(2026, 12, 1, 23, 30, tzinfo=timezone.utc),
                                     now=datetime(2026, 12, 2, tzinfo=timezone.utc))['day'], 'Tuesday')

    def test_naive_timestamp_is_rejected(self):
        with self.assertRaises(ValueError):
            date_display(datetime(2026, 7, 1))
