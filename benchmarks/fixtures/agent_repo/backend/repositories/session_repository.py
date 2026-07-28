class SessionRepository:
    def extend_expiry(self, user_id: str):
        return {"user_id": user_id, "extended": True}
