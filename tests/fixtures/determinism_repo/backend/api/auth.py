"""Authentication entry points."""

from backend.services.session_service import renew_login


class BaseHandler:
    def dispatch(self):
        raise NotImplementedError


class AuthHandler(BaseHandler):
    """Handles login and refresh."""

    def dispatch(self):
        return refresh_session()


def refresh_session():
    return renew_login()
