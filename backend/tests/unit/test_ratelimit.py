from starlette.requests import Request

from app.core.ratelimit import LIMITS, RateLimiter, client_key
from app.core.security import create_token


def _request(auth: str | None = None, ip: str = "172.30.0.10") -> Request:
    headers = [(b"authorization", auth.encode())] if auth else []
    return Request({"type": "http", "headers": headers, "client": (ip, 1234)})


def test_signed_in_requests_are_keyed_per_user_not_per_ip() -> None:
    a = client_key(_request(f"Bearer {create_token('clinician', 'clinician', 'access')}"))
    b = client_key(_request(f"Bearer {create_token('viewer', 'viewer', 'access')}"))
    assert (a, b) == ("user:clinician", "user:viewer")


def test_anonymous_invalid_or_refresh_tokens_fall_back_to_the_ip() -> None:
    assert client_key(_request()) == "ip:172.30.0.10"
    assert client_key(_request("Bearer not-a-jwt", ip="10.0.0.7")) == "ip:10.0.0.7"
    refresh = create_token("viewer", "viewer", "refresh")
    assert client_key(_request(f"Bearer {refresh}")) == "ip:172.30.0.10"


def test_users_behind_one_proxy_have_separate_budgets() -> None:
    limiter = RateLimiter()
    limit = LIMITS["heavy"][0]
    for _ in range(limit):
        assert limiter.hit("heavy", "user:admin") is None
    assert limiter.hit("heavy", "user:admin") is not None  # admin is now limited ...
    assert limiter.hit("heavy", "user:viewer") is None  # ... but another user on the same IP is not
