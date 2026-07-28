from backend.services.session_service import renew_login


def test_renew_login():
    assert renew_login("u")["extended"]
