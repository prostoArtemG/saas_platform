"""Payment provider abstraction.

To add a real provider (mono / liqpay / stripe):
1. Create a new class subclassing `PaymentProvider`.
2. Implement `create_invoice()` and (optionally) `verify_webhook()`.
3. Register in `PROVIDERS` dict below.
4. Set `payment_provider` in settings or pass `provider=` to /api/create-payment-link.

The rest of the app talks only to the abstract interface.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Optional
from uuid import uuid4


@dataclass
class InvoiceResult:
    invoice_id: str
    payment_url: str


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
    ) -> InvoiceResult:
        """Create an invoice on provider side and return invoice_id + payment_url."""

    async def verify_webhook(self, payload: dict, headers: dict) -> bool:  # noqa: D401
        """Verify webhook authenticity. Real providers should override."""
        return True


class ManualProvider(PaymentProvider):
    """Manual / out-of-band payment. Admin confirms via Telegram."""

    name = "manual"

    async def create_invoice(
        self,
        *,
        payment_id: int,
        amount: float,
        currency: str,
        description: str,
        return_url: Optional[str] = None,
    ) -> InvoiceResult:
        return InvoiceResult(
            invoice_id=f"manual-{payment_id}-{uuid4().hex[:8]}",
            payment_url=f"/pay/manual/{payment_id}",
        )


class MockProvider(PaymentProvider):
    """Mock provider for local testing. payment_url points to mock webhook hint."""

    name = "mock"

    async def create_invoice(
        self,
        *,
        payment_id: int,
        amount: float,
        currency: str,
        description: str,
        return_url: Optional[str] = None,
    ) -> InvoiceResult:
        return InvoiceResult(
            invoice_id=f"mock-{payment_id}-{uuid4().hex[:8]}",
            payment_url=f"/pay/mock/{payment_id}",
        )


# Registry. Real providers (LiqpayProvider, MonoProvider, StripeProvider, ...)
# go here once implemented.
PROVIDERS: dict[str, PaymentProvider] = {
    "manual": ManualProvider(),
    "mock": MockProvider(),
}


def get_provider(name: Optional[str]) -> PaymentProvider:
    """Resolve provider by name, fall back to manual."""
    if not name:
        return PROVIDERS["manual"]
    return PROVIDERS.get(name, PROVIDERS["manual"])
