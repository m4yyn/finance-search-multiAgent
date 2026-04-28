import hashlib
import json
from typing import Any

import httpx

from app.config.settings import get_settings
from app.core.redis_client import RedisCache
from app.schemas.search import WebSearchResponse, WebSearchResult


BOCHA_WEB_SEARCH_URL = "https://api.bochaai.com/v1/web-search"
WEB_SEARCH_CACHE_PREFIX = "web_search:bocha:"
WEB_SEARCH_CACHE_TTL_SECONDS = 3600
DEFAULT_WEB_SEARCH_COUNT = 5
DEFAULT_WEB_SEARCH_FRESHNESS = "noLimit"


class BochaSearchError(RuntimeError):
    """Raised when Bocha web search cannot return a usable response."""


def build_web_search_cache_key(
    query: str,
    count: int = DEFAULT_WEB_SEARCH_COUNT,
    freshness: str = DEFAULT_WEB_SEARCH_FRESHNESS,
    summary: bool = True,
) -> str:
    payload = json.dumps(
        {
            "query": query.strip(),
            "count": count,
            "freshness": freshness.strip(),
            "summary": summary,
        },
        ensure_ascii=False,
        sort_keys=True,
    )
    return WEB_SEARCH_CACHE_PREFIX + hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _read_api_key() -> str:
    settings = get_settings()
    if settings.bocha_api_key is None:
        raise BochaSearchError("BOCHA_API_KEY is not configured.")
    api_key = settings.bocha_api_key.get_secret_value()
    if not api_key:
        raise BochaSearchError("BOCHA_API_KEY is not configured.")
    return api_key


def _extract_items(payload: dict[str, Any]) -> list[dict[str, Any]]:
    data = payload.get("data") if isinstance(payload.get("data"), dict) else payload
    web_pages = data.get("webPages") or data.get("webpages") or data.get("web_pages")
    if isinstance(web_pages, dict) and isinstance(web_pages.get("value"), list):
        return web_pages["value"]
    for key in ("results", "value", "items"):
        value = data.get(key)
        if isinstance(value, list):
            return value
    return []


def _normalize_result(item: dict[str, Any], index: int) -> WebSearchResult:
    title = item.get("name") or item.get("title") or item.get("siteName") or "未命名结果"
    url = item.get("url") or item.get("link") or item.get("displayUrl") or ""
    snippet = item.get("snippet") or item.get("description") or ""
    summary = item.get("summary") or item.get("summaryText") or ""
    return WebSearchResult(
        index=index,
        title=str(title),
        url=str(url),
        snippet=str(snippet),
        summary=str(summary),
        site_name=item.get("siteName") or item.get("site_name"),
        site_icon=item.get("siteIcon") or item.get("site_icon"),
        date_published=(
            item.get("datePublished")
            or item.get("date_published")
            or item.get("dateLastCrawled")
        ),
        display_url=item.get("displayUrl") or item.get("display_url"),
    )


def _parse_bocha_response(
    payload: dict[str, Any],
    query: str,
    count: int,
    freshness: str,
    summary: bool,
) -> WebSearchResponse:
    code = payload.get("code")
    if code not in (None, 0, 200, "0", "200"):
        message = payload.get("msg") or payload.get("message") or "Bocha search failed."
        raise BochaSearchError(str(message))

    results = [
        _normalize_result(item, index)
        for index, item in enumerate(_extract_items(payload)[:count], start=1)
        if isinstance(item, dict)
    ]
    return WebSearchResponse(
        query=query,
        count=count,
        freshness=freshness,
        summary=summary,
        cached=False,
        results=results,
    )


class SearchService:
    """Bocha web search service with isolated Redis caching."""

    def __init__(
        self,
        redis_cache: RedisCache,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        self.redis_cache = redis_cache
        self.http_client = http_client

    async def search_web(
        self,
        query: str,
        count: int = DEFAULT_WEB_SEARCH_COUNT,
        freshness: str = DEFAULT_WEB_SEARCH_FRESHNESS,
        summary: bool = True,
    ) -> WebSearchResponse:
        query = query.strip()
        freshness = freshness.strip()
        if not query:
            raise ValueError("Search query cannot be blank.")
        if count < 1:
            raise ValueError("Search count must be greater than 0.")

        cache_key = build_web_search_cache_key(query, count, freshness, summary)
        cached_payload = await self.redis_cache.get(cache_key)
        if isinstance(cached_payload, dict):
            return WebSearchResponse(**cached_payload).model_copy(update={"cached": True})

        response = await self._post_to_bocha(query, count, freshness, summary)
        await self.redis_cache.set(
            cache_key,
            response.model_dump(mode="json"),
            expire_seconds=WEB_SEARCH_CACHE_TTL_SECONDS,
        )
        return response

    async def _post_to_bocha(
        self,
        query: str,
        count: int,
        freshness: str,
        summary: bool,
    ) -> WebSearchResponse:
        request_payload = {
            "query": query,
            "summary": summary,
            "freshness": freshness,
            "count": count,
        }
        headers = {
            "Authorization": f"Bearer {_read_api_key()}",
            "Content-Type": "application/json",
        }

        try:
            if self.http_client is not None:
                response = await self.http_client.post(
                    BOCHA_WEB_SEARCH_URL,
                    json=request_payload,
                    headers=headers,
                    timeout=30.0,
                )
            else:
                async with httpx.AsyncClient() as client:
                    response = await client.post(
                        BOCHA_WEB_SEARCH_URL,
                        json=request_payload,
                        headers=headers,
                        timeout=30.0,
                    )
        except httpx.HTTPError as exc:
            raise BochaSearchError(f"Bocha search request failed: {exc}") from exc

        if response.status_code >= 400:
            raise BochaSearchError(f"Bocha search returned HTTP {response.status_code}.")
        try:
            payload = response.json()
        except ValueError as exc:
            raise BochaSearchError("Bocha search returned invalid JSON.") from exc
        return _parse_bocha_response(payload, query, count, freshness, summary)


async def search_web(
    redis_cache: RedisCache,
    query: str,
    count: int = DEFAULT_WEB_SEARCH_COUNT,
    freshness: str = DEFAULT_WEB_SEARCH_FRESHNESS,
    summary: bool = True,
) -> WebSearchResponse:
    return await SearchService(redis_cache).search_web(query, count, freshness, summary)


def format_web_references_for_prompt(results: list[WebSearchResult]) -> str:
    if not results:
        return "未检索到可用联网搜索结果。"

    formatted_results: list[str] = []
    for result in results:
        content = result.summary or result.snippet or "无摘要。"
        source = result.site_name or result.display_url or result.url
        formatted_results.append(
            "\n".join(
                [
                    f"[{result.index}] {result.title} | 来源={source} | URL={result.url}",
                    content,
                ]
            )
        )
    return "\n\n".join(formatted_results)
