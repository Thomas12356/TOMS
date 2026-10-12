"""Clock regressions never disable signature, age, CSRF or referrer checks."""
from datetime import timedelta
from unittest.mock import patch
import unittest

from flask import Flask, jsonify, session
from flask_wtf.csrf import generate_csrf
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer

from services.web.signing import ClockTolerantCSRFProtect, ClockTolerantSessionInterface, ClockTolerantSigner


class SigningTests(unittest.TestCase):
    def setUp(self):
        self.app = Flask(__name__)
        self.app.config.update(SECRET_KEY='synthetic-test-key-only', WTF_CSRF_TIME_LIMIT=30,
                               PERMANENT_SESSION_LIFETIME=timedelta(seconds=60))
        self.app.session_interface = ClockTolerantSessionInterface()
        self.csrf = ClockTolerantCSRFProtect(self.app)

        @self.app.route('/', methods=['GET', 'POST'])
        def form():
            if session.get('mode') is None:
                session['mode'] = 'real'
            return jsonify(token=generate_csrf(), mode=session['mode'])

        self.client = self.app.test_client()

    def open(self, at=1000):
        with patch('itsdangerous.timed.time.time', return_value=at):
            return self.client.get('/').get_json()['token']

    def submit(self, token, at, **kwargs):
        with patch('itsdangerous.timed.time.time', return_value=at):
            return self.client.post('/', data={'csrf_token':token}, **kwargs)

    def test_small_clock_regression_preserves_cookie_and_form(self):
        token = self.open()
        response = self.submit(token, 998)
        self.assertEqual(response.status_code,200)
        self.assertEqual(response.get_json()['mode'],'real')

    def test_farther_future_cookie_is_rejected(self):
        token = self.open()
        self.assertEqual(self.submit(token,997).status_code,400)

    def test_future_form_rejected_with_valid_cookie(self):
        token = self.open()
        signer = self.app.session_interface.get_signing_serializer(self.app)
        values = signer.loads(self.client.get_cookie('session').value)
        with patch('itsdangerous.timed.time.time', return_value=995):
            self.client.set_cookie('session',signer.dumps(values))
        self.assertEqual(self.submit(token,997).status_code,400)

    def test_expired_tampered_missing_and_wrong_session_form_rejected(self):
        token = self.open()
        self.assertEqual(self.submit(token,1031).status_code,400)
        self.assertEqual(self.submit(token+'tamper',1001).status_code,400)
        self.assertEqual(self.submit('',1001).status_code,400)
        other = self.app.test_client()
        with patch('itsdangerous.timed.time.time', return_value=1000):
            other.get('/')
            self.assertEqual(other.post('/',data={'csrf_token':token}).status_code,400)

    def test_original_signatures_are_compatible_and_age_limits_unchanged(self):
        original = URLSafeTimedSerializer('synthetic-test-key-only')
        tolerant = URLSafeTimedSerializer('synthetic-test-key-only',signer=ClockTolerantSigner)
        with patch('itsdangerous.timed.time.time', return_value=1000):
            signed = original.dumps({'mode':'sample'})
        with patch('itsdangerous.timed.time.time', return_value=998):
            with self.assertRaises(SignatureExpired):
                original.loads(signed,max_age=30)
            self.assertEqual(tolerant.loads(signed,max_age=30),{'mode':'sample'})
            with self.assertRaises(BadSignature):
                tolerant.loads(signed+'tamper',max_age=30)
        for at in (997,1031):
            with patch('itsdangerous.timed.time.time', return_value=at):
                with self.assertRaises(SignatureExpired):
                    tolerant.loads(signed,max_age=30)

    def test_https_referrer_checks_survive_clock_tolerance(self):
        token = self.open()
        cookie = self.client.get_cookie('session')
        self.client.set_cookie('session',cookie.value,domain='localhost')
        self.assertEqual(self.submit(token,998,base_url='https://localhost').status_code,400)
        self.assertEqual(self.submit(token,998,base_url='https://localhost',headers={'Referer':'https://elsewhere.example/'}).status_code,400)
        self.assertEqual(self.submit(token,998,base_url='https://localhost',headers={'Referer':'https://localhost/'}).status_code,200)
