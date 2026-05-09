"""Payment provider abstraction.

To add a real provider:
1. Create a new class subclassing `PaymentProvider`.
2. Implement `create_invoice()`, `parse_webhook()` and (optionally) `verify_webhook()`.
3. Register in `PROVIDERS` dict (auto-registers below if env keys present).
4. Set PAYMENT_PROVIDER_DEFAULT or pass `provider=` to /api/create-payment-link.

The rest of the app talks only to the abstract interface.
"""
from __future__ import annotations

import base64
import hashlib
import json
import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Optional
from uuid import uuid4

import aiohttp

from app.config import settings

logger = logging.getLogger(__name__)


@dataclass
class InvoiceResult:
    invoice_id: str
    payment_url: str


@dataclass
class ParsedWebhook:
    """Result of parsing a provider webhook body.

    `invoice_id` is matched against `Payment.invoice_id` to locate the row.
    `status` is the normalized status: "paid" | "failed" | "cancelled" | "pending".
    """
    invoice_id: str
    status: str
    raw_status: Optional[str] = None


class PaymentProvider(ABC):
    """Abstract base class for payment providers."""

    name: str = "abstract"

    @abstractmethod
    async def create_invoice(
        self,
        *,
        payment_id: int,
        amount: float,
        currency: str,
        description: str,
        return_url: Optional[str] = None,
        webhook_url: Optional[str] = None,
    ) -> InvoiceResult:
        """Create an invoice on provider side and return invoice_id + payment_url."""

    async def verify_webhook(self, payload: Any, headers: dict) -> bool:  # noqa: D401
        """Verify webhook authenticity. Real providers SHOULD override."""
        return True

    async def parse_webhook(self, payload: Any, headers: dict) -> Optional[ParsedWebhook]:
        """Extract (invoice_id, normalized status) from a provider webhook body."""
        raise NotImplementedError


# ---------------------------------------------------------------------------
# Manual / Mock
# ---------------------------------------------------------------------------

class ManualProvider(PaymentProvider):
    """Manual / out-of-band payment. Admin confirms via Telegram."""

    name = "manual"

    async def create_invoice(
        self, *, payment_id: int, amount: float, currency: str,
        description: str, return_url: Optional[str] = None,
        webhook_url: Optional[str] = None,
    ) -> InvoiceResult:
        return InvoiceResult(
            invoice_id=f"manual-{payment_id}-{uuid4().hex[:8]}",
            payment_url=f"/payment/{payment_id}",
        )

    async def parse_webhook(self, payload: Any, headers: dict) -> Optional[ParsedWebhook]:
        if not isinstance(payload, dict):
            return None
        inv = payload.get("invoice_id") or payload.get("invoiceId")
        st = payload.get("status", "paid")
        if not inv:
            return None
        return ParsedWebhook(invoice_id=str(inv), status=str(st), raw_status=str(st))


class MockProvider(PaymentProvider):
    """Mock provider for local testing. payment_url points to internal page."""

    name = "mock"

    async def create_invoice(
        self, *, payment_id: int, amount: float, currency: str,
        description: str, return_url: Optional[str] = None,
        webhook_url: Optional[str] = None,
    ) -> InvoiceResult:
        return InvoiceResult(
            invoice_id=f"mock-{payment_id}-{uuid4().hex[:8]}",
            payment_url=f"/payment/{payment_id}",
        )

    async def parse_webhook(self, payload: Any, headers: dict) -> Optional[ParsedWebhook]:
        if not isinstance(payload, dict):
            return None
        # The mock webhook can match by either invoice_id OR payment_id.
        inv = payload.get("invoice_id") or payload.get("invoiceId")
        if not inv:
            pid = payload.get("payment_id") or payload.get("paymentId")
            if pid:
                inv = f"__mock_pid__:{pid}"
        if not inv:
            return None
        st = str(payload.get("status", "paid"))
        return ParsedWebhook(invoice_id=str(inv), status=st, raw_status=st)


# ---------------------------------------------------------------------------
# Monobank Acquiring
# https://api.monobank.ua/docs/acquiring.html
# ---------------------------------------------------------------------------

# Minimal ISO-4217 numeric codes used by Mono ("ccy" field).
_CCY_MAP = {"UAH": 980, "USD": 840, "EUR": 978}


class MonoProvider(PaymentProvider):
    """Monobank Acquiring provider."""

    name = "mono"
    API_BASE = "https://api.monobank.ua"

    def __init__(self, token: str) -> None:
        if not token:
            raise ValueError("MONO_TOKEN is required for MonoProvider")
        self._token = token

    async def create_invoice(
        self, *, payment_id: int, amount: float, currency: str,
        description: str, return_url: Optional[str] = None,
        webhook_url: Optional[str] = None,
    ) -> InvoiceResult:
        ccy = _CCY_MAP.get((currency or "UAH").upper(), 980)
        # Mono expects amount in minor units (kopecks)
        amount_minor = int((Decimal(str(amount)) * 100).to_integral_value())
        body: dict[str, Any] = {
            "amount": amount_minor,
            "ccy": ccy,
            "merchantPaymInfo": {
                "reference": str(payment_id),
                "destination": description[:280],
            },
        }
        if return_url:
            body["redirectUrl"] = return_url
        if webhook_url:
            body["webHookUrl"] = webhook_url

        timeout = aiohttp.ClientTimeout(total=15)
        async with aiohttp.ClientSession(timeout=timeout) as http:
            async with http.post(
                f"{self.API_BASE}/api/merchant/invoice/create",
                json=body,
                headers={"X-Token": self._token},
            ) as resp:
                data = await resp.json(content_type=None)
                if resp.status >= 400 or not isinstance(data, dict) or "invoiceId" not in data:
                    raise RuntimeError(f"mono create_invoice failed: {resp.status} {data}")
        return InvoiceResult(invoice_id=data["invoiceId"], payment_url=data["pageUrl"])

    async def _fetch_invoice_status(self, invoice_id: str) -> Optional[dict]:
        timeout = aiohttp.ClientTimeout(total=10)
        try:
            async with aiohttp.ClientSession(timeout=timeout) as http:
                async with http.get(
                    f"{self.API_BASE}/api/merchant/invoice/status",
                    params={"invoiceId": invoice_id},
                    headers={"X-Token": self._token},
                ) as resp:
                    if resp.status >= 400:
                        return None
                    return await resp.json(content_type=None)
        except aiohttp.ClientError as e:
            logger.warning("mono status fetch failed: %s", e)
            return None

    async def verify_webhook(self, payload: Any, headers: dict) -> bool:
        # Trust-but-verify: re-fetch invoice status from Mono using server token.
        if not isinstance(payload, dict):
            return False
        invoice_id = payload.get("invoiceId")
        if not invoice_id:
            return False
        fresh = await self._fetch_invoice_status(invoice_id)
        if not fresh:
            return False
        cb_status = str(payload.get("status", "")).lower()
        live_status = str(fresh.get("status", "")).lower()
        if cb_status == "success" and live_status != "success":
            return False
        return True

    async def parse_webhook(self, payload: Any, headers: dict) -> Optional[ParsedWebhook]:
        if not isinstance(payload, dict):
            return None
        invoice_id = payload.get("invoiceId")
        if not invoice_id:
            return None
        raw = str(payload.get("status", "")).lower()
        status_map = {
            "success": "paid",
            "failure": "failed",
            "expired": "failed",
            "reversed": "cancelled",
            "processing": "pending",
            "created": "pending",
            "hold": "pending",
        }
        return ParsedWebhook(
            invoice_id=str(invoice_id),
            status=status_map.get(raw, "pending"),
            raw_status=raw,
        )


# ---------------------------------------------------------------------------
# LiqPay
# https://www.liqpay.ua/en/documentation/api
# ---------------------------------------------------------------------------

class LiqpayProvider(PaymentProvider):
    """LiqPay provider (checkout link + signed webhook)."""

    name = "liqpay"
    CHECKOUT_URL = "https://www.liqpay.ua/api/3/checkout"

    def __init__(self, public_key: str, private_key: str) -> None:
        if not public_key or not private_key:
            raise ValueError("LIQPAY_PUBLIC_KEY and LIQPAY_PRIVATE_KEY are required")
        self._public_key = public_key
        self._private_key = private_key

    def _sign(self, data_b64: str) -> str:
        raw = (self._private_key + data_b64 + self._private_key).encode("utf-8")
        return base64.b64encode(hashlib.sha1(raw).digest()).decode("ascii")

    async def create_invoice(
        self, *, payment_id: int, amount: float, currency: str,
        description: str, return_url: Optional[str] = None,
        webhook_url: Optional[str] = None,
    ) -> InvoiceResult:
        order_id = f"saas-{payment_id}-{uuid4().hex[:6]}"
        data: dict[str, Any] = {
            "public_key": self._public_key,
            "version": "3",
            "action": "pay",
            "amount": float(amount),
            "currency": (currency or "UAH").upper(),
            "description": description[:200],
            "order_id": order_id,
        }
        if return_url:
            data["result_url"] = return_url
        if webhook_url:
            data["server_url"] = webhook_url

        data_b64 = base64.b64encode(
            json.dumps(data, ensure_ascii=False).encode("utf-8")
        ).decode("ascii")
        signature = self._sign(data_b64)
        url = f"{self.CHECKOUT_URL}?data={data_b64}&signature={signature}"
        return InvoiceResult(invoice_id=order_id, payment_url=url)

    async def verify_webhook(self, payload: Any, headers: dict) -> bool:
        if not isinstance(payload, dict):
            return False
        data_b64 = payload.get("data")
        signature = payload.get("signature")
        if not data_b64 or not signature:
            return False
        return self._sign(str(data_b64)) == signature

    async def parse_webhook(self, payload: Any, headers: dict) -> Optional[ParsedWebhook]:
        if not isinstance(payload, dict):
            return None
        data_b64 = payload.get("data")
        if not data_b64:
            return None
        try:
            decoded = json.loads(base64.b64decode(str(data_b64)).decode("utf-8"))
        except Exception:  # noqa: BLE001
            return None
        order_id = decoded.get("order_id")
        if not order_id:
            return None
        raw = str(decoded.get("status", "")).lower()
        status_map = {
            "success": "paid",
            "sandbox": "paid",
            "wait_accept": "pending",
            "processing": "pending",
            "wait_secure": "pending",
            "failure": "failed",
            "error": "failed",
            "reversed": "cancelled",
        }
        return ParsedWebhook(
            invoice_id=str(order_id),
            status=status_map.get(raw, "pending"),
            raw_status=raw,
        )


# ---------------------------------------------------------------------------
# Registry — manual + mock are always available; real providers auto-register
# only if the corresponding ENV keys are set.
# ---------------------------------------------------------------------------

PROVIDERS: dict[str, PaymentProvider] = {
    "manual": ManualProvider(),
    "mock": MockProvider(),
}

if settings.mono_token:
    try:
        PROVIDERS["mono"] = MonoProvider(settings.mono_token)
        logger.info("Payment provider registered: mono")
    except Exception as exc:  # noqa: BLE001
        logger.warning("Failed to register MonoProvider: %s", exc)

if settings.liqpay_public_key and settings.liqpay_private_key:
    try:
        PROVIDERS["liqpay"] = LiqpayProvider(
            settings.liqpay_public_key, settings.liqpay_private_key
        )
        logger.info("Payment provider registered: liqpay")
    except Exception as exc:  # noqa: BLE001
        logger.warning("Failed to register LiqpayProvider: %s", exc)


def get_provider(name: Optional[str]) -> PaymentProvider:
    """Resolve provider by name; fall back to the configured default, then manual.

    Special case: if the caller explicitly asks for "mono" or "liqpay" but the
    provider is not registered (no ENV keys), fall back to the mock provider so
    that a development/staging deploy without real keys still works end-to-end.
    """
    if name and name in PROVIDERS:
        return PROVIDERS[name]
    if name in ("mono", "liqpay"):
        logger.warning(
            "Provider '%s' requested but not registered (missing ENV keys); "
            "falling back to 'mock'.", name,
        )
        return PROVIDERS["mock"]
    default = settings.payment_provider_default or "manual"
    return PROVIDERS.get(default, PROVIDERS["manual"])


def get_provider_strict(name: str) -> Optional[PaymentProvider]:
    """Resolve provider by name; return None if not registered."""
    return PROVIDERS.get(name)
