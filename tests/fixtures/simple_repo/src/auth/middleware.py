from auth.session import create_session

def require_auth(user_id: str):
    return create_session(user_id)
