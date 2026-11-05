import httpx
import pytest

from app.shopify import ShopifyClient, ShopifyError, throttle_wait

STORE = "demo.myshopify.com"
OK = {"data": {"shop": {"name": "demo"}}}
THROTTLED = {
    "errors": [{"message": "Throttled", "extensions": {"code": "THROTTLED"}}],
    "extensions": {
        "cost": {
            "requestedQueryCost": 300,
            "throttleStatus": {"currentlyAvailable": 100, "restoreRate": 100.0},
        }
    },
}


class FakeShopify:
    """Answers token requests and plays back GraphQL responses in order."""

    def __init__(self, *responses: tuple[int, dict]):
        self.responses = list(responses)
        self.token_requests = 0
        self.graphql_requests = 0

    def __call__(self, request: httpx.Request) -> httpx.Response:
        if request.url.path == "/admin/oauth/access_token":
            self.token_requests += 1
            return httpx.Response(
                200, json={"access_token": f"token-{self.token_requests}", "expires_in": 86399}
            )
        self.graphql_requests += 1
        status, body = self.responses.pop(0)
        return httpx.Response(status, json=body)


def make_client(fake: FakeShopify, clock=lambda: 0.0):
    sleeps: list[float] = []
    client = ShopifyClient(
        STORE,
        "id",
        "secret",
        "2026-10",
        http=httpx.Client(transport=httpx.MockTransport(fake)),
        sleep=sleeps.append,
        clock=clock,
    )
    return client, sleeps


def test_reuses_token_until_it_nearly_expires():
    now = [0.0]
    fake = FakeShopify((200, OK), (200, OK), (200, OK))
    client, _ = make_client(fake, clock=lambda: now[0])

    client.graphql("{ shop { name } }")
    client.graphql("{ shop { name } }")
    assert fake.token_requests == 1

    now[0] = 86399 - 30
    client.graphql("{ shop { name } }")
    assert fake.token_requests == 2


def test_waits_and_retries_when_throttled():
    fake = FakeShopify((200, THROTTLED), (200, OK))
    client, sleeps = make_client(fake)

    assert client.graphql("{ shop { name } }") == OK["data"]
    assert sleeps == [2.0]


def test_backs_off_on_server_errors_and_429():
    fake = FakeShopify((503, {}), (429, {}), (200, OK))
    client, sleeps = make_client(fake)

    assert client.graphql("{ shop { name } }") == OK["data"]
    assert sleeps == [2, 4]


def test_gets_a_new_token_after_401():
    fake = FakeShopify((401, {}), (200, OK))
    client, _ = make_client(fake)

    client.graphql("{ shop { name } }")
    assert fake.token_requests == 2


def test_raises_on_query_errors():
    fake = FakeShopify((200, {"errors": [{"message": "Field 'nope' doesn't exist"}]}))
    client, _ = make_client(fake)

    with pytest.raises(ShopifyError, match="nope"):
        client.graphql("{ nope }")


def test_gives_up_after_max_attempts():
    fake = FakeShopify(*[(503, {})] * ShopifyClient.max_attempts)
    client, _ = make_client(fake)

    with pytest.raises(ShopifyError, match="after 5 attempts"):
        client.graphql("{ shop { name } }")


def test_throttle_wait_is_at_least_one_second():
    assert throttle_wait(THROTTLED) == 2.0
    assert throttle_wait({"extensions": {}}) == 1.0
