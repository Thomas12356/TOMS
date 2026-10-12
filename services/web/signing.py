"""Bounded tolerance for small backward wall-clock adjustments on signed forms."""
import hmac

from flask import current_app, g, request, session
from flask.sessions import SecureCookieSessionInterface
from flask_wtf.csrf import CSRFProtect, same_origin, validate_csrf
from itsdangerous import BadData, SignatureExpired, TimestampSigner, URLSafeTimedSerializer
from wtforms import ValidationError

CLOCK_TOLERANCE_SECONDS = 2


class ClockTolerantSigner(TimestampSigner):
    def unsign(self, signed_value, max_age=None, return_timestamp=False):
        try:
            return super().unsign(signed_value, max_age=max_age, return_timestamp=return_timestamp)
        except SignatureExpired as error:
            # The parent has verified the signature before raising on its age.
            # Never relax the past-age limit or accept a farther-future signature.
            ahead = int(error.date_signed.timestamp()) - self.get_timestamp()
            if max_age is not None and 0 < ahead <= CLOCK_TOLERANCE_SECONDS:
                return super().unsign(signed_value, max_age=None, return_timestamp=return_timestamp)
            raise


class ClockTolerantSessionInterface(SecureCookieSessionInterface):
    def get_signing_serializer(self, app):
        serializer = super().get_signing_serializer(app)
        if serializer is not None:
            serializer.signer = ClockTolerantSigner
        return serializer


def validate_form_token(token):
    try:
        validate_csrf(token)
    except ValidationError as error:
        if not isinstance(error.__cause__, SignatureExpired):
            raise
        # Flask-WTF still supplies normal validation/messages. Retry only its
        # age rejection, with signature, maximum age and session matching intact.
        field = current_app.config['WTF_CSRF_FIELD_NAME']
        serializer = URLSafeTimedSerializer(
            current_app.config.get('WTF_CSRF_SECRET_KEY') or current_app.secret_key,
            salt='wtf-csrf-token', signer=ClockTolerantSigner)
        limit = current_app.config['WTF_CSRF_TIME_LIMIT']
        if hasattr(limit, 'total_seconds'):
            limit = limit.total_seconds()
        try:
            value = serializer.loads(token, max_age=limit)
        except BadData:
            raise error from None
        if not hmac.compare_digest(session[field], value):
            raise ValidationError('The CSRF tokens do not match.') from None


class ClockTolerantCSRFProtect(CSRFProtect):
    def protect(self):
        # Preserve Flask-WTF's method, HTTPS referrer and per-request checks.
        if request.method not in current_app.config['WTF_CSRF_METHODS']:
            return
        try:
            validate_form_token(self._get_csrf_token())
        except ValidationError as error:
            self._error_response(error.args[0])
        if request.is_secure and current_app.config['WTF_CSRF_SSL_STRICT']:
            if not request.referrer:
                self._error_response('The referrer header is missing.')
            if not same_origin(request.referrer, f'https://{request.host}/'):
                self._error_response('The referrer does not match the host.')
        g.csrf_valid = True
