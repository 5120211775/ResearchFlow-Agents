"""Shared response headers for Server-Sent Events endpoints."""

SSE_HEADERS: dict[str, str] = {
    # no-transform stops proxies gzipping (and therefore buffering) the stream.
    "Cache-Control": "no-cache, no-transform",
    "X-Accel-Buffering": "no",
    "Connection": "keep-alive",
}
