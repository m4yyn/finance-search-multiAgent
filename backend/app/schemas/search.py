from pydantic import BaseModel, Field, field_validator


class WebSearchRequest(BaseModel):
    """Request contract for Bocha web search."""

    query: str = Field(min_length=1)
    count: int = Field(default=5, ge=1, le=20)
    freshness: str = "noLimit"
    summary: bool = True

    @field_validator("query", "freshness")
    @classmethod
    def strip_required_text(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("Value cannot be blank.")
        return normalized


class WebSearchResult(BaseModel):
    """Normalized web search result returned by Bocha."""

    index: int
    title: str
    url: str
    snippet: str = ""
    summary: str = ""
    site_name: str | None = None
    site_icon: str | None = None
    date_published: str | None = None
    display_url: str | None = None


class WebSearchResponse(BaseModel):
    """Structured web search response with cache metadata."""

    query: str
    count: int
    freshness: str
    summary: bool
    cached: bool = False
    results: list[WebSearchResult]
