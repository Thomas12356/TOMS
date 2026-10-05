"""Bound JSON body reads, including streams without Content-Length."""

from flask import Request
from werkzeug.exceptions import RequestEntityTooLarge
from werkzeug.utils import cached_property
from werkzeug.wsgi import get_input_stream


class BoundedRequest(Request):
    @cached_property
    def stream(self):
        limit = self.max_content_length
        if limit is not None and not self.environ.get("CONTENT_LENGTH") and self.environ.get("wsgi.input_terminated"):
            # Read one extra byte to distinguish an exact-size body from truncation.
            # Keep Werkzeug's safe fallback for servers without terminated streams.
            return get_input_stream(self.environ, max_content_length=limit + 1)
        return super().stream

    def get_data(self, cache=True, as_text=False, parse_form_data=False):
        data = super().get_data(cache=cache, as_text=False, parse_form_data=parse_form_data)
        if self.max_content_length is not None and len(data) > self.max_content_length:
            raise RequestEntityTooLarge()
        return data.decode(errors="replace") if as_text else data
