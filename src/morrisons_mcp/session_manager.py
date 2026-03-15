import asyncio
import logging
import time

import httpx

logger = logging.getLogger(__name__)

MORRISONS_HOME = "https://groceries.morrisons.com/"
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)
DEFAULT_HEADERS = {
    "accept": "application/json; charset=utf-8",
    "user-agent": USER_AGENT,
    "ecom-request-source": "web",
}
REQUEST_DELAY = 0.5  # seconds between requests


class SessionManager:
    def __init__(self) -> None:
        self._client = httpx.AsyncClient(
            follow_redirects=True,
            timeout=httpx.Timeout(15.0),
            headers={"user-agent": USER_AGENT},
        )
        self._cookies: dict[str, str] = {}
        self._session_lock = asyncio.Lock()  # protects session acquisition + cookies dict
        self._rate_lock = asyncio.Lock()     # serialises the rate-limit window
        self._last_request_time: float = 0.0

    async def _rate_limit(self) -> None:
        """Serialise requests to respect REQUEST_DELAY between them."""
        async with self._rate_lock:
            now = time.monotonic()
            elapsed = now - self._last_request_time
            if elapsed < REQUEST_DELAY:
                await asyncio.sleep(REQUEST_DELAY - elapsed)
            self._last_request_time = time.monotonic()

    async def acquire_session(self) -> None:
        """Acquire fresh Morrisons session cookies (thread-safe)."""
        async with self._session_lock:
            # Double-check: another coroutine may have refreshed while we waited
            if self._cookies:
                return
            logger.info("Acquiring new Morrisons session cookies...")
            await self._rate_limit()
            resp = await self._client.get(MORRISONS_HOME)
            resp.raise_for_status()
            new_cookies: dict[str, str] = {}
            for name in ("global_sid", "AWSALB", "AWSALBCORS", "VISITORID"):
                val = resp.cookies.get(name)
                if val:
                    new_cookies[name] = val
            self._cookies = new_cookies
            logger.info(f"Session acquired. Cookies: {list(self._cookies.keys())}")

    async def get_cookies(self) -> dict[str, str]:
        if not self._cookies:
            await self.acquire_session()
        return dict(self._cookies)

    async def refresh_session(self) -> None:
        """Discard existing cookies and force re-acquisition."""
        async with self._session_lock:
            self._cookies = {}
        await self.acquire_session()

    def _apply_cookies(self) -> None:
        """Write current session cookies onto the shared client cookie jar."""
        self._client.cookies.clear()
        for name, value in self._cookies.items():
            self._client.cookies.set(name, value)

    async def request(
        self,
        method: str,
        url: str,
        *,
        params: dict | None = None,
        json_data: dict | None = None,
        extra_headers: dict | None = None,
    ) -> httpx.Response:
        """Make an authenticated request with auto-retry on session expiry."""
        await self.get_cookies()  # ensure session is initialised
        self._apply_cookies()
        headers = {**DEFAULT_HEADERS, **(extra_headers or {})}

        await self._rate_limit()
        resp = await self._client.request(
            method, url, params=params, json=json_data, headers=headers,
        )

        # If session expired, refresh and retry once
        if resp.status_code in (401, 403, 440):
            logger.warning(f"Session expired (HTTP {resp.status_code}). Refreshing...")
            await self.refresh_session()
            self._apply_cookies()
            await self._rate_limit()
            resp = await self._client.request(
                method, url, params=params, json=json_data, headers=headers,
            )

        return resp

    async def close(self) -> None:
        await self._client.aclose()
