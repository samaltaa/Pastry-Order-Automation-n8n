"""Shopify Admin GraphQL client.

Gets its own access token (client credentials grant), reuses it until shortly before it
expires, and waits and retries when Shopify throttles or has a temporary failure.
"""

import time

import httpx


class ShopifyError(Exception):
    def __init__(self, errors: list[dict]):
        super().__init__("; ".join(error.get("message", str(error)) for error in errors))
        self.errors = errors


class ShopifyClient:
    max_attempts = 5

    def __init__(
        self,
        store: str,
        client_id: str,
        client_secret: str,
        api_version: str,
        http: httpx.Client | None = None,
        sleep=time.sleep,
        clock=time.monotonic,
    ):
        self.store = store
        self.api_version = api_version
        self._client_id = client_id
        self._client_secret = client_secret
        self._http = http or httpx.Client(timeout=30)
        self._sleep = sleep
        self._clock = clock
        self._token: str | None = None
        self._token_expires_at = 0.0

    def close(self) -> None:
        self._http.close()

    def _access_token(self) -> str:
        # Tokens last 24 hours. Renew a minute early so a request never carries an expired one.
        if self._token and self._clock() < self._token_expires_at - 60:
            return self._token
        response = self._http.post(
            f"https://{self.store}/admin/oauth/access_token",
            data={
                "grant_type": "client_credentials",
                "client_id": self._client_id,
                "client_secret": self._client_secret,
            },
        )
        response.raise_for_status()
        body = response.json()
        self._token = body["access_token"]
        self._token_expires_at = self._clock() + body["expires_in"]
        return self._token

    def graphql(self, query: str, variables: dict | None = None) -> dict:
        """Runs one query or mutation and returns its `data`.

        Mutation problems (userErrors) come back inside `data`; callers check those.
        """
        url = f"https://{self.store}/admin/api/{self.api_version}/graphql.json"
        for attempt in range(1, self.max_attempts + 1):
            response = self._http.post(
                url,
                json={"query": query, "variables": variables or {}},
                headers={"X-Shopify-Access-Token": self._access_token()},
            )
            if response.status_code == 401:
                self._token = None
                continue
            if response.status_code == 429 or response.status_code >= 500:
                self._sleep(min(2**attempt, 30))
                continue
            response.raise_for_status()

            body = response.json()
            errors = body.get("errors") or []
            if any(error.get("extensions", {}).get("code") == "THROTTLED" for error in errors):
                self._sleep(throttle_wait(body))
                continue
            if errors:
                raise ShopifyError(errors)
            return body["data"]

        raise ShopifyError([{"message": f"Shopify request failed after {self.max_attempts} attempts"}])


def throttle_wait(body: dict) -> float:
    """Seconds until the bucket has refilled enough for this query. Shopify recommends at least 1."""
    cost = body.get("extensions", {}).get("cost", {})
    status = cost.get("throttleStatus", {})
    missing = cost.get("requestedQueryCost", 0) - status.get("currentlyAvailable", 0)
    return max(1.0, missing / (status.get("restoreRate") or 50))
