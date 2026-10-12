"""Bind copied fixture sessions to the browser, as actual login does."""
from app import app
from services.web.sessions import login_manager


def install_owner_cookie(context, fixture, origin):
    created=not context.pages
    probe=context.new_page() if created else context.pages[0]
    try:
        user_agent=probe.evaluate('navigator.userAgent')
    finally:
        if created:
            probe.close()
    cookie=fixture.client.get_cookie(app.config['SESSION_COOKIE_NAME'])
    signer=app.session_interface.get_signing_serializer(app)
    values=signer.loads(cookie.value)
    with app.test_request_context('/',environ_base={'REMOTE_ADDR':'127.0.0.1'},headers={'User-Agent':user_agent}):
        values['_id']=login_manager._session_identifier_generator()
    values['_fresh']=True
    context.add_cookies([dict(name=cookie.key,value=signer.dumps(values),url=origin)])
