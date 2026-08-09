# Auth design

Login flows live in [auth handler](../backend/api/auth.py) and
[session service](../backend/services/session_service.py).

## Sessions

Sessions are stored in the `sessions` table.
