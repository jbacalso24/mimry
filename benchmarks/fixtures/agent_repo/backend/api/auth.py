from backend.services.session_service import renew_login


def refresh_session(user_id: str):
    return renew_login(user_id)
