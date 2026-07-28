def validate_bearer_token(header: str) -> bool:
    return header.startswith("Bearer ")
