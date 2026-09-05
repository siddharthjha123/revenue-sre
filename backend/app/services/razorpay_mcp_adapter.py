"""Narrow, server-side adapter for Razorpay's official Payment Link MCP tool."""

import base64
import json
from dataclasses import dataclass
from typing import Any, Protocol

import httpx2
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client


class RazorpayMCPError(RuntimeError):
    """Provider failure with a deliberately non-sensitive public message."""


@dataclass(frozen=True, slots=True)
class PaymentLinkResult:
    payment_link_id: str
    short_url: str
    reference_id: str


class PaymentLinkAdapter(Protocol):
    async def create_payment_link(self, arguments: dict[str, Any]) -> PaymentLinkResult: ...


class RazorpayMCPPaymentLinkAdapter:
    """Expose exactly one write tool; credentials and arbitrary tools stay private."""

    TOOL_NAME = "create_payment_link"

    def __init__(self, *, url: str, key_id: str, key_secret: str, timeout_seconds: float) -> None:
        token = base64.b64encode(f"{key_id}:{key_secret}".encode()).decode()
        self._url = url
        self._authorization = f"Basic {token}"
        self._timeout_seconds = timeout_seconds

    async def create_payment_link(self, arguments: dict[str, Any]) -> PaymentLinkResult:
        http_client = httpx2.AsyncClient(
            headers={"Authorization": self._authorization},
            timeout=self._timeout_seconds,
        )
        try:
            async with http_client:
                async with streamable_http_client(
                    self._url,
                    http_client=http_client,
                ) as streams:
                    async with ClientSession(streams[0], streams[1]) as session:
                        await session.initialize()
                        result = await session.call_tool(self.TOOL_NAME, arguments)
        except Exception as error:
            raise RazorpayMCPError("Razorpay MCP could not create the payment link") from error

        if result.is_error:
            raise RazorpayMCPError("Razorpay MCP rejected the payment-link request")
        payload = _result_payload(result)
        link_id = payload.get("id")
        short_url = payload.get("short_url")
        reference_id = payload.get("reference_id")
        if not all(
            isinstance(value, str) and value for value in (link_id, short_url, reference_id)
        ):
            raise RazorpayMCPError("Razorpay MCP returned an incomplete payment-link result")
        return PaymentLinkResult(link_id, short_url, reference_id)


def _result_payload(result: Any) -> dict[str, Any]:
    if isinstance(result.structured_content, dict):
        payload = result.structured_content
        if isinstance(payload.get("result"), dict):
            return payload["result"]
        return payload
    for item in result.content:
        text = getattr(item, "text", None)
        if not isinstance(text, str):
            continue
        try:
            payload = json.loads(text)
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict):
            return payload
    raise RazorpayMCPError("Razorpay MCP returned an unreadable payment-link result")
