from backend.repositories.session_repository import SessionRepository


def renew_login(user_id: str):
    return SessionRepository().extend_expiry(user_id)
