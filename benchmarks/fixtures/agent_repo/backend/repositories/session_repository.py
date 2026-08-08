class SessionRepository:
    def extend_expiry(self, user_id: str):
        query = "UPDATE sessions SET expires_at = ? WHERE user_id = ?"
        return {"user_id": user_id, "extended": True, "query": query}
