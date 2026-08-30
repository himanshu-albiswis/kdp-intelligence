"""The contract every signal source implements.

One rule drives this design: a source that cannot answer must say so.
The previous code let a blocked Reddit collapse into an empty list, which
scoring then read as "no demand" — indistinguishable from a genuine
absence of demand. A niche verdict built on that is worse than no verdict,
because it looks the same as a real one.
"""

from dataclasses import dataclass, field
from typing import Any, Optional

# ok            — reached it, parsed it, here is the data
# unavailable   — tried and failed (blocked, rate-limited, network error)
# not_configured— needs a credential the operator has not supplied
# excluded      — deliberately not attempted; see detail for the evidence
STATUSES = ("ok", "unavailable", "not_configured", "excluded")


@dataclass(frozen=True)
class SourceResult:
    name: str
    status: str
    items: list[dict[str, Any]]
    detail: str
    transport: Optional[str] = None
    evidence_url: Optional[str] = None
    meta: dict[str, Any] = field(default_factory=dict)

    @property
    def usable(self) -> bool:
        """Only 'ok' with actual items may influence a score."""
        return self.status == "ok" and bool(self.items)

    def as_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "status": self.status,
            "detail": self.detail,
            "transport": self.transport,
            "evidence_url": self.evidence_url,
            "item_count": len(self.items),
            "usable": self.usable,
            "meta": self.meta,
            "items": self.items,
        }


def ok(name: str, items: list[dict[str, Any]], detail: str = "",
       transport: Optional[str] = None, **kw: Any) -> SourceResult:
    return SourceResult(name=name, status="ok", items=items, detail=detail,
                        transport=transport, **kw)


def unavailable(name: str, detail: str, **kw: Any) -> SourceResult:
    return SourceResult(name=name, status="unavailable", items=[], detail=detail, **kw)


def not_configured(name: str, detail: str, **kw: Any) -> SourceResult:
    return SourceResult(name=name, status="not_configured", items=[], detail=detail, **kw)


def excluded(name: str, detail: str, **kw: Any) -> SourceResult:
    return SourceResult(name=name, status="excluded", items=[], detail=detail, **kw)


def body_text(response: Any) -> str:
    """Response bodies arrive as str or bytes depending on transport."""
    body = getattr(response, "body", "") or ""
    return body if isinstance(body, str) else body.decode("utf-8", "ignore")
