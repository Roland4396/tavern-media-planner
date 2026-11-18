"""Private sequence planning: normalize chat -> Stream, validate scenes locally."""
from __future__ import annotations
import asyncio
import fcntl
import json
import logging
import os
from pathlib import Path
import stat
from typing import Any
from aiohttp import ClientError, ClientSession, ClientTimeout, DummyCookieJar, web
from multidict import CIMultiDict
from sequence_planner import PlanValidationError, normalize_plan

LOG = logging.getLogger("media-planner")
SOCKET = "/run/media-planner/planner.sock"

class Planner:
    def __init__(self, url, api_key, timeout, max_input):
        self.url, self.api_key, self.timeout, self.max_input = url, api_key, timeout, max_input
        self.session = None
        self.in_flight = 0

    async def lifetime(self, app):
        async with ClientSession(cookie_jar=DummyCookieJar(), trust_env=False) as self.session:
            yield
        self.session = None

    @web.middleware
    async def activity(self, request, handler):
        if request.path == "/health":
            return await handler(request)
        self.in_flight += 1
        try:
            return await handler(request)
        finally:
            self.in_flight -= 1

    async def health(self, request):
        return web.json_response({"ok": self.session is not None, "active": self.in_flight})

    async def stream_chat_completion(self, request: web.Request) -> web.StreamResponse:
        if request.content_length is not None and request.content_length > self.max_input:
            return web.json_response({"error": "planner request is too large"}, status=413)
        try:
            payload = await request.json()
        except (json.JSONDecodeError, ValueError, TypeError):
            return web.json_response({"error": "request body must be a JSON object"}, status=400)
        if not isinstance(payload, dict):
            return web.json_response({"error": "request body must be a JSON object"}, status=400)

        model = str(payload.get("model") or "").strip()
        messages = payload.get("messages")
        if not model or len(model) > 256:
            return web.json_response({"error": "model must be a non-empty string"}, status=400)
        if not isinstance(messages, list) or not 1 <= len(messages) <= 64:
            return web.json_response({"error": "messages must contain 1 to 64 items"}, status=400)
        total_characters = 0
        normalized_messages: list[dict[str, str]] = []
        for index, message in enumerate(messages):
            if not isinstance(message, dict):
                return web.json_response({"error": f"messages[{index}] must be an object"}, status=400)
            role = str(message.get("role") or "").strip()
            content = message.get("content")
            if role not in {"system", "user", "assistant"} or not isinstance(content, str):
                return web.json_response(
                    {"error": f"messages[{index}] requires a valid role and string content"},
                    status=400,
                )
            total_characters += len(content)
            normalized_messages.append({"role": role, "content": content})
        if total_characters > self.max_input:
            return web.json_response({"error": "planner messages are too large"}, status=413)

        upstream_payload: dict[str, Any] = {
            "model": model,
            "messages": normalized_messages,
            "stream": False,
        }
        for field in ("temperature", "top_p", "max_tokens"):
            if payload.get(field) is not None:
                upstream_payload[field] = payload[field]
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        assert self.session is not None
        try:
            async with self.session.post(
                self.url,
                json=upstream_payload,
                headers=headers,
                timeout=ClientTimeout(total=self.timeout),
            ) as upstream:
                body = await upstream.read()
                response_headers = CIMultiDict()
                content_type = upstream.headers.get("Content-Type")
                if content_type:
                    response_headers["Content-Type"] = content_type
                return web.Response(
                    status=upstream.status,
                    reason=upstream.reason,
                    headers=response_headers,
                    body=body,
                )
        except (asyncio.TimeoutError, ClientError, OSError, ConnectionError) as exc:
            LOG.warning("planner_stream_failed model=%s error=%r", model, exc)
            return web.json_response({"error": "Stream planner upstream unavailable"}, status=503)

    async def validate_sequence_plan(self, request: web.Request) -> web.StreamResponse:
        try:
            payload = await request.json()
        except (json.JSONDecodeError, ValueError, TypeError):
            return web.json_response({"error": "request body must be a JSON object"}, status=400)
        if not isinstance(payload, dict):
            return web.json_response({"error": "request body must be a JSON object"}, status=400)
        try:
            plan = normalize_plan(
                payload.get("response_text") or payload.get("plan") or "",
                asset_catalog=payload.get("asset_catalog"),
                min_clip_seconds=float(payload.get("min_clip_seconds", 5)),
                max_clip_seconds=float(payload.get("max_clip_seconds", 15)),
            )
        except PlanValidationError as exc:
            return web.json_response(
                {"error": "invalid sequence plan", "details": exc.errors}, status=422
            )
        except (TypeError, ValueError):
            return web.json_response({"error": "invalid clip duration limits"}, status=400)
        return web.json_response({"plan": plan})


def build_app(url=None, api_key=None):
    planner = Planner(
        os.environ.get("STREAM_CHAT_URL", "http://stream-proxy:3002/v1/chat/completions").strip() if url is None else url,
        os.environ.get("STREAM_CHAT_API_KEY", "").strip() if api_key is None else api_key,
        float(os.environ.get("STREAM_CHAT_TIMEOUT_SECONDS", "1800")),
        int(os.environ.get("PLANNER_MAX_INPUT_BYTES", str(8 * 1024 * 1024))),
    )
    app = web.Application(client_max_size=1024**4, middlewares=[planner.activity])
    app.cleanup_ctx.append(planner.lifetime)
    app.router.add_get("/health", planner.health)
    app.router.add_post("/planner/v1/chat/completions", planner.stream_chat_completion)
    app.router.add_post("/planner/v1/validate", planner.validate_sequence_plan)
    return app


def main():
    os.umask(0o177)
    path = Path(os.environ.get("MEDIA_PLANNER_SOCKET", SOCKET))
    st = path.parent.lstat()
    if not stat.S_ISDIR(st.st_mode) or st.st_uid != os.getuid() or st.st_mode & 0o077:
        raise RuntimeError("expected a private, owned socket directory")
    # A second instance must never unlink the active instance's socket.
    with os.fdopen(os.open(path.parent / "service.lock", os.O_CREAT|os.O_RDWR|os.O_NOFOLLOW, 0o600), "rb") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        try:
            if not stat.S_ISSOCK(path.lstat().st_mode):
                raise RuntimeError("refusing to replace non-socket")
        except FileNotFoundError:
            pass
        web.run_app(build_app(), path=str(path), access_log=None, handler_cancellation=True)

if __name__ == "__main__":
    main()
