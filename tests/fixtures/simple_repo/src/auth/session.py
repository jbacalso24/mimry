def create_session(user_id: str) -> dict:
    return {"user_id": user_id}

class SessionRepository:
    def save(self, session: dict) -> None:
        pass
