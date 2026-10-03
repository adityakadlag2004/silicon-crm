from django.contrib.auth import SESSION_KEY
from django.contrib.auth.signals import user_logged_in
from django.dispatch import receiver
from django.utils import timezone

TOUCH_KEY = "_touched"


def _today():
    return timezone.localdate().isoformat()


class SessionRefreshMiddleware:
    """Roll the 30-day session window forward once a day, not on every request.

    SESSION_SAVE_EVERY_REQUEST did it with a database write and commit on every
    page and every app API call — to push an expiry 30 days out by a few
    seconds. A daily touch keeps the same promise: anyone active (the Android
    app especially) is never signed out; only a session idle for 30 days ends.

    Must sit after SessionMiddleware: it marks the session modified on the way
    out, and SessionMiddleware then saves it and re-issues the cookie.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)
        session = getattr(request, "session", None)
        if session is not None and session.get(SESSION_KEY) and session.get(TOUCH_KEY) != _today():
            session[TOUCH_KEY] = _today()
        return response


@receiver(user_logged_in)
def _touched_at_login(sender, request, user, **kwargs):
    """Logging in saves the session anyway; stamp the day so the first request
    after it doesn't write the session a second time."""
    if request is not None and hasattr(request, "session"):
        request.session[TOUCH_KEY] = _today()
