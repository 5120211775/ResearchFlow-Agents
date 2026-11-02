"""Session-scoped API client. No database, model or graph imports belong here."""

from __future__ import annotations

import json
from collections.abc import Iterator

import httpx


class APIError(Exception):
    def __init__(self, message: str, status: int | None = None, uncertain: bool = False):
        super().__init__(message)
        self.status = status
        self.uncertain = uncertain


class APIClient:
    """Create once per Streamlit session, never in a shared resource cache.

    Automatic replay is limited to a rejected 401 after refresh. Transport failures
    never replay writes: a timeout may happen after the server committed a task.
    """

    def __init__(self, origin: str, transport: httpx.BaseTransport | None = None):
        self.http = httpx.Client(
            base_url=origin.rstrip("/"),
            timeout=httpx.Timeout(150, connect=10),
            transport=transport,
            trust_env=False,
            follow_redirects=False,
        )

    def close(self):
        self.http.close()

    def health(self):
        try:
            response = self.http.get("/health/ready")
            self._check(response)
            return response.json()
        except httpx.TransportError as exc:
            raise APIError("API 未连接，请检查服务是否启动。") from exc

    @staticmethod
    def _check(response: httpx.Response):
        if response.is_success:
            return
        try:
            detail = response.json().get("detail", response.json().get("error", "请求失败"))
            if isinstance(detail, list):
                detail = "; ".join(str(item.get("msg", "参数错误")) for item in detail)
        except ValueError, AttributeError:
            detail = "服务暂时不可用，请检查 API 日志。"
        raise APIError(str(detail)[:1200], response.status_code)

    def request(self, method: str, path: str, **kwargs) -> httpx.Response:
        try:
            response = self.http.request(method, "/api/v1" + path, **kwargs)
            if response.status_code == 401 and path not in {
                "/auth/login",
                "/auth/register",
                "/auth/refresh",
                "/auth/logout",
            }:
                refresh = self.http.post("/api/v1/auth/refresh")
                if refresh.is_success:
                    response = self.http.request(method, "/api/v1" + path, **kwargs)
                else:
                    self.http.cookies.clear()
            self._check(response)
            return response
        except httpx.TransportError as exc:
            uncertain = method.upper() not in {"GET", "HEAD", "OPTIONS"}
            message = "无法连接 API，请检查服务状态。"
            if uncertain:
                message = "请求结果未知：服务可能已经处理，请先检查任务或记录，不要直接重复提交。"
            raise APIError(message, uncertain=uncertain) from exc

    def get(self, path: str, **params):
        return self.request("GET", path, params=params).json()

    def post(self, path: str, **body):
        return self.request("POST", path, json=body).json()

    def login(self, email: str, password: str):
        self.post("/auth/login", email=email, password=password)
        return self.get("/auth/me")

    def logout(self):
        try:
            self.post("/auth/logout")
        finally:
            self.http.cookies.clear()

    def upload(self, project_id: str, filename: str, content: bytes):
        return self.request(
            "POST", f"/projects/{project_id}/corpus/documents", files={"file": (filename, content)}
        ).json()

    def download(self, run_id: str, format_: str):
        suffix = {"md": "export.md", "pdf": "export.pdf", "json": "bundle.json"}[format_]
        return self.request("GET", f"/runs/{run_id}/{suffix}").content

    def chat(self, thread_id: str, message: str, scope: str) -> Iterator[dict]:
        """Consume the POST SSE answer once; never replay a partially streamed answer."""
        # Refresh before opening a stream, so only this explicit action generates text.
        self.get("/auth/me")
        try:
            with self.http.stream(
                "POST",
                f"/api/v1/threads/{thread_id}/messages",
                json={"message": message, "scope": scope},
            ) as response:
                if not response.is_success:
                    response.read()
                    self._check(response)
                for line in response.iter_lines():
                    if line.startswith("data: "):
                        event = json.loads(line[6:])
                        if event.get("type") == "error":
                            raise APIError(event.get("detail", "回答生成失败"))
                        yield event
        except httpx.TransportError as exc:
            raise APIError(
                "回答连接中断，请先查看对话历史；不会自动重复生成。", uncertain=True
            ) from exc
