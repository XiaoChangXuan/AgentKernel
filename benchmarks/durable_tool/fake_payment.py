"""Deterministic payment fixture for durable side-effect benchmarks."""

from __future__ import annotations

from dataclasses import dataclass

from agentkernel import ReconcileResult, ReconcileStatus, ToolExecutionContext


@dataclass(frozen=True, slots=True)
class Payment:
    request_id: str
    invoice_id: str
    amount_cents: int
    status: str


class FakePaymentService:
    """In-memory payment service with idempotency by request id."""

    def __init__(self) -> None:
        self._payments: dict[str, Payment] = {}

    def charge(
        self,
        *,
        request_id: str,
        invoice_id: str,
        amount_cents: int,
    ) -> dict[str, object]:
        if request_id not in self._payments:
            self._payments[request_id] = Payment(
                request_id=request_id,
                invoice_id=invoice_id,
                amount_cents=amount_cents,
                status="succeeded",
            )
        payment = self._payments[request_id]
        return {
            "request_id": payment.request_id,
            "invoice_id": payment.invoice_id,
            "amount_cents": payment.amount_cents,
            "status": payment.status,
        }

    async def handler(
        self,
        arguments: object,
        context: ToolExecutionContext,
    ) -> dict[str, object]:
        del arguments
        return self.charge(
            request_id=context.operation_id,
            invoice_id="invoice-001",
            amount_cents=4200,
        )

    async def reconcile(self, context: ToolExecutionContext) -> ReconcileResult:
        payment = self._payments.get(context.operation_id)
        if payment is None:
            return ReconcileResult(
                ReconcileStatus.NOT_FOUND,
                message="payment request not found",
            )
        return ReconcileResult(
            ReconcileStatus.SUCCEEDED,
            output={
                "request_id": payment.request_id,
                "invoice_id": payment.invoice_id,
                "amount_cents": payment.amount_cents,
                "status": payment.status,
            },
            message="payment already succeeded externally",
        )

    def count_invoice_charges(self, invoice_id: str) -> int:
        return sum(1 for payment in self._payments.values() if payment.invoice_id == invoice_id)

    @property
    def operation_count(self) -> int:
        return len(self._payments)
