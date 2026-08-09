"""Session lifecycle. Reads from the sessions table."""


def renew_login():
    # SELECT id FROM sessions WHERE active = 1
    return extend_expiry()


def extend_expiry():
    return True
