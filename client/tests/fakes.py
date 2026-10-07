"""An offline stand-in for ``httpx`` so command routing can be tested without a server.

Every CLI command is a literal ``httpx.<verb>(url, ...)`` call, so replacing the
four module-level verbs is enough to observe exactly which endpoint a command
hits and with which body. Login is answered automatically; anything not
registered with :meth:`FakeHttp.on` is an assertion failure, so a command that
reaches for an endpoint the test did not expect fails loudly.
"""
from __future__ import annotations

import functools
import json
from dataclasses import dataclass
from typing import Any, Callable

from click.testing import CliRunner, Result

from club_client.cli import main


class FakeResp:
    def __init__(
        self, status_code: int = 200, payload: Any = None, text: str | None = None,
        headers: dict[str, str] | None = None, content: bytes = b"",
    ):
        self.status_code = status_code
        self.headers = headers or {}
        self.content = content
        self._payload = payload
        self.text = text if text is not None else (
            json.dumps(payload) if payload is not None else ""
        )

    def json(self) -> Any:
        if self._payload is None:
            raise json.JSONDecodeError("no json", "", 0)
        return self._payload


@dataclass
class Call:
    method: str
    path: str
    json: Any
    params: Any
    data: Any = None
    headers: Any = None
    files: Any = None


Responder = Callable[["Call"], FakeResp]


class FakeHttp:
    def __init__(self) -> None:
        self._routes: list[tuple[str, str, FakeResp | Responder]] = []
        self.calls: list[Call] = []
        self.logins = 0

    def on(
        self,
        method: str,
        path: str,
        payload: Any = None,
        *,
        status: int = 200,
        responder: Responder | None = None,
        headers: dict[str, str] | None = None,
    ) -> "FakeHttp":
        """Answer ``method path`` with ``payload`` (or compute it per call)."""
        self._routes.append((method.upper(), path, responder or FakeResp(status, payload, headers=headers)))
        return self

    def install(self, monkeypatch) -> "FakeHttp":
        for verb in ("get", "head", "post", "patch", "put", "delete"):
            monkeypatch.setattr(f"httpx.{verb}", functools.partial(self._call, verb.upper()))
        return self

    def _call(self, method: str, url: str, **kwargs: Any) -> FakeResp:
        path = "/" + url.split("/", 3)[3]
        if path == "/v1/auth/login":
            self.logins += 1
            return FakeResp(200, {"accessToken": "tok", "refreshToken": "rtok"})
        call = Call(
            method, path, kwargs.get("json"), kwargs.get("params"),
            kwargs.get("data"), kwargs.get("headers"), kwargs.get("files"),
        )
        self.calls.append(call)
        for m, p, resp in self._routes:
            if m == method and p == path:
                return resp(call) if callable(resp) else resp
        raise AssertionError(f"unexpected {method} {path} json={kwargs.get('json')}")

    def calls_to(self, method: str, path: str) -> list[Call]:
        return [c for c in self.calls if c.method == method.upper() and c.path == path]


def run(*args: str) -> Result:
    """Invoke the CLI as an authenticated user against the fake."""
    return CliRunner().invoke(main, ["--user", "sudo", "--pw", "x", *args])
