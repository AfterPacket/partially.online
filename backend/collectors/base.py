import logging
import httpx

log = logging.getLogger(__name__)


class BaseCollector:
    name: str = "base"

    def __init__(self):
        self._client = httpx.AsyncClient(timeout=30.0, follow_redirects=True)

    async def close(self):
        await self._client.aclose()

    async def _get(self, url: str, **kw) -> dict:
        try:
            r = await self._client.get(url, **kw)
            r.raise_for_status()
            return r.json()
        except Exception as exc:
            log.warning(f"[{self.name}] GET {url}: {exc}")
            return {}

    async def collect(self) -> list:
        raise NotImplementedError
