"""Deterministic money math shared by the financial automation & reporting tools.

These helpers are intentionally pure (no I/O, no SDK/network) so the numbers that
back a "close the books" run, an aged-receivables report, or a P&L are fully
testable and *traceable to source transactions*. Free-text LLM arithmetic is not
trustworthy for financials; every figure produced here is derived by explicit,
reviewable code.

The two central primitives are:

* :func:`apply_fifo` — apply payments/credits to the oldest open charges first
  (the same rule Buildium and every ledger uses), returning both the remaining
  open charges and an application trail linking each dollar to a source charge.
* :func:`age_receivables` — bucket the *unpaid* remainder of each charge by its
  age relative to an as-of date into the standard 0-30/31-60/61-90/90+ buckets,
  with a reconciliation check that the buckets sum back to the ledger balance.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any

# Cent precision everywhere: money is rounded to 2 dp so summed buckets reconcile
# exactly against the ledger balance (floats otherwise drift by fractions of a
# cent across thousands of transactions).
CENTS = 2

AGING_BUCKETS = ("current", "days_31_60", "days_61_90", "days_over_90")
AGING_BUCKET_LABELS = {
    "current": "0-30 days",
    "days_31_60": "31-60 days",
    "days_61_90": "61-90 days",
    "days_over_90": "90+ days",
}


def to_float(value: Any, default: float = 0.0) -> float:
    """Best-effort numeric coercion that never raises (bad data -> ``default``)."""
    if value is None:
        return default
    if isinstance(value, bool):  # guard: bool is an int subclass
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def money(value: Any) -> float:
    """Coerce and round a value to cents."""
    return round(to_float(value), CENTS)


def parse_date(value: Any) -> date | None:
    """Parse a Buildium date/datetime string (or ``date``) into a ``date``.

    Buildium returns ISO 8601 dates and datetimes (sometimes with a trailing
    ``Z``). Returns ``None`` for anything unparseable so callers can skip rather
    than crash on dirty data.
    """
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = str(value).strip()
    if not text:
        return None
    # Normalise a trailing Z (UTC) which datetime.fromisoformat rejects pre-3.11
    # for some forms, and drop a time component if present.
    text = text.replace("Z", "+00:00")
    try:
        return datetime.fromisoformat(text).date()
    except ValueError:
        pass
    # Fall back to the leading date portion (YYYY-MM-DD).
    head = text[:10]
    try:
        return datetime.strptime(head, "%Y-%m-%d").date()
    except ValueError:
        return None


def days_between(start: date | None, end: date | None) -> int | None:
    """Whole days from ``start`` to ``end`` (``None`` if either is missing)."""
    if start is None or end is None:
        return None
    return (end - start).days


@dataclass
class OpenCharge:
    """A charge with its remaining (unpaid) balance after applying payments."""

    charge_id: Any
    charge_date: date | None
    original: float
    remaining: float
    memo: str | None = None


@dataclass
class Application:
    """A single payment-to-charge application (for an audit trail)."""

    charge_id: Any
    amount: float


@dataclass
class FifoResult:
    """Outcome of applying payments/credits to charges oldest-first."""

    open_charges: list[OpenCharge] = field(default_factory=list)
    applications: list[Application] = field(default_factory=list)
    unapplied_credit: float = 0.0

    @property
    def total_open(self) -> float:
        return round(sum(c.remaining for c in self.open_charges), CENTS)


def _charge_sort_key(charge: dict[str, Any]) -> tuple[int, str]:
    """Sort charges oldest-first; undated charges sort last but stably."""
    d = parse_date(_charge_date(charge))
    if d is None:
        return (1, "")
    return (0, d.isoformat())


def _charge_date(charge: dict[str, Any]) -> Any:
    for key in ("Date", "TransactionDate", "PostDate", "EntryDate"):
        if charge.get(key):
            return charge[key]
    return None


def _charge_amount(charge: dict[str, Any]) -> float:
    for key in ("Amount", "TotalAmount", "Total"):
        if charge.get(key) is not None:
            return to_float(charge[key])
    return 0.0


def _charge_id(charge: dict[str, Any]) -> Any:
    for key in ("Id", "TransactionId", "ChargeId"):
        if charge.get(key) is not None:
            return charge[key]
    return None


def _charge_memo(charge: dict[str, Any]) -> str | None:
    for key in ("Memo", "Description", "TransactionType"):
        value = charge.get(key)
        if value:
            return str(value)
    return None


def apply_fifo(charges: list[dict[str, Any]], payments_total: float) -> FifoResult:
    """Apply ``payments_total`` to ``charges`` oldest-first (FIFO).

    This is the deterministic core behind "auto-apply received payments to the
    oldest balances": payments and credits knock down the oldest open charge
    first, then the next, so the remaining open charges (and their ages) reflect
    what an owner or auditor would expect.

    Args:
        charges: Charge records (dicts). Date/amount/id are read defensively from
            the common Buildium field names.
        payments_total: Total of payments + credits available to apply
            (a non-negative number).

    Returns:
        A :class:`FifoResult` with the remaining open charges, the
        payment-to-charge application trail, and any leftover unapplied credit.
    """
    remaining_credit = max(0.0, round(to_float(payments_total), CENTS))
    ordered = sorted(charges, key=_charge_sort_key)
    open_charges: list[OpenCharge] = []
    applications: list[Application] = []
    for charge in ordered:
        original = _charge_amount(charge)
        cid = _charge_id(charge)
        remaining = original
        if remaining_credit > 0 and remaining > 0:
            applied = min(remaining_credit, remaining)
            remaining = round(remaining - applied, CENTS)
            remaining_credit = round(remaining_credit - applied, CENTS)
            if applied > 0:
                applications.append(Application(charge_id=cid, amount=round(applied, CENTS)))
        if remaining > 0:
            open_charges.append(
                OpenCharge(
                    charge_id=cid,
                    charge_date=parse_date(_charge_date(charge)),
                    original=round(original, CENTS),
                    remaining=round(remaining, CENTS),
                    memo=_charge_memo(charge),
                )
            )
    return FifoResult(
        open_charges=open_charges,
        applications=applications,
        unapplied_credit=round(remaining_credit, CENTS),
    )


def _bucket_for_age(days: int | None) -> str:
    """Map an age in days to a standard aging bucket key."""
    if days is None or days <= 30:
        return "current"
    if days <= 60:
        return "days_31_60"
    if days <= 90:
        return "days_61_90"
    return "days_over_90"


@dataclass
class AgingResult:
    """Aged-receivables breakdown for one ledger."""

    buckets: dict[str, float]
    total: float
    open_charges: list[OpenCharge]

    def as_dict(self) -> dict[str, Any]:
        return {"buckets": dict(self.buckets), "total": self.total}


def age_receivables(
    charges: list[dict[str, Any]],
    payments_total: float,
    as_of: date,
) -> AgingResult:
    """Age the unpaid remainder of ``charges`` as of ``as_of`` into buckets.

    Payments are applied oldest-first (see :func:`apply_fifo`); each remaining
    open charge is then placed in a bucket by the age of its charge date. The
    bucket totals are guaranteed to sum to the ledger's open balance, giving a
    reconciled, defensible aged-receivables figure.
    """
    fifo = apply_fifo(charges, payments_total)
    buckets = {key: 0.0 for key in AGING_BUCKETS}
    for open_charge in fifo.open_charges:
        age = days_between(open_charge.charge_date, as_of)
        bucket = _bucket_for_age(age)
        buckets[bucket] = round(buckets[bucket] + open_charge.remaining, CENTS)
    total = round(sum(buckets.values()), CENTS)
    return AgingResult(buckets=buckets, total=total, open_charges=fifo.open_charges)


def sum_aging(results: list[AgingResult]) -> dict[str, float]:
    """Combine per-ledger aging into portfolio-level bucket totals."""
    combined = {key: 0.0 for key in AGING_BUCKETS}
    for result in results:
        for key in AGING_BUCKETS:
            combined[key] = round(combined[key] + result.buckets.get(key, 0.0), CENTS)
    combined["total"] = round(sum(result.total for result in results), CENTS)
    return combined


# ---------------------------------------------------------------------------
# General-ledger parsing for income statements / owner statements
# ---------------------------------------------------------------------------
def iter_gl_lines(transaction: dict[str, Any]):
    """Yield ``(account_id, account_name, account_type, signed_amount)`` lines.

    Buildium GL transactions carry their journal lines under a few possible
    shapes across endpoints; this reads them defensively. The amount is signed
    by posting type (Credit positive, Debit negative) so income (credit-normal)
    and expense (debit-normal) accounts aggregate correctly.
    """
    lines = (
        _first_list(transaction, ("Lines", "JournalLines"))
        or _first_list(transaction.get("Journal") or {}, ("Lines", "JournalLines"))
        or []
    )
    for line in lines:
        if not isinstance(line, dict):
            continue
        account = line.get("GLAccount") or line.get("GlAccount") or {}
        if not isinstance(account, dict):
            account = {}
        account_id = account.get("Id") or line.get("GLAccountId") or line.get("AccountId")
        account_name = account.get("Name") or line.get("AccountName") or ""
        account_type = account.get("Type") or account.get("AccountType") or line.get("Type") or ""
        amount = to_float(line.get("Amount"))
        posting = str(line.get("PostingType") or "").strip().lower()
        if posting == "debit":
            amount = -amount
        yield account_id, str(account_name), str(account_type), round(amount, CENTS)


def _first_list(obj: dict[str, Any], keys: tuple[str, ...]) -> list[Any] | None:
    for key in keys:
        value = obj.get(key)
        if isinstance(value, list):
            return value
    return None


# Buildium GL account types treated as income / expense for a P&L.
INCOME_TYPES = {"income", "revenue"}
EXPENSE_TYPES = {"expense", "operatingexpense", "cost of goods sold", "costofgoodssold"}


@dataclass
class IncomeStatement:
    """A reconciled profit-and-loss summary derived from GL lines."""

    income_accounts: list[dict[str, Any]]
    expense_accounts: list[dict[str, Any]]
    total_income: float
    total_expense: float
    net_income: float
    transaction_ids: list[Any]

    def reconciles(self) -> bool:
        acct_income = round(sum(a["amount"] for a in self.income_accounts), CENTS)
        acct_expense = round(sum(a["amount"] for a in self.expense_accounts), CENTS)
        return (
            acct_income == self.total_income
            and acct_expense == self.total_expense
            and round(self.total_income - self.total_expense, CENTS) == self.net_income
        )


def build_income_statement(transactions: list[dict[str, Any]]) -> IncomeStatement:
    """Aggregate GL transactions into a reconciled income statement.

    Income accounts (credit-normal) are reported as positive revenue and expense
    accounts (debit-normal) as positive costs; net income is revenue minus
    expense. The set of contributing transaction ids is retained so every figure
    traces back to its source entries.
    """
    income: dict[Any, dict[str, Any]] = {}
    expense: dict[Any, dict[str, Any]] = {}
    txn_ids: list[Any] = []
    for txn in transactions:
        if not isinstance(txn, dict):
            continue
        tid = txn.get("Id") or txn.get("TransactionId")
        contributed = False
        for account_id, name, acct_type, amount in iter_gl_lines(txn):
            key = acct_type.strip().lower()
            if key in INCOME_TYPES:
                bucket = income.setdefault(
                    account_id, {"account_id": account_id, "name": name, "amount": 0.0}
                )
                # Credit-normal income: a credit (positive signed amount) increases revenue.
                bucket["amount"] = round(bucket["amount"] + amount, CENTS)
                contributed = True
            elif key in EXPENSE_TYPES:
                bucket = expense.setdefault(
                    account_id, {"account_id": account_id, "name": name, "amount": 0.0}
                )
                # Debit-normal expense: a debit (negative signed amount) increases cost.
                bucket["amount"] = round(bucket["amount"] - amount, CENTS)
                contributed = True
        if contributed and tid is not None:
            txn_ids.append(tid)

    income_accounts = sorted(income.values(), key=lambda a: a["amount"], reverse=True)
    expense_accounts = sorted(expense.values(), key=lambda a: a["amount"], reverse=True)
    total_income = round(sum(a["amount"] for a in income_accounts), CENTS)
    total_expense = round(sum(a["amount"] for a in expense_accounts), CENTS)
    net_income = round(total_income - total_expense, CENTS)
    return IncomeStatement(
        income_accounts=income_accounts,
        expense_accounts=expense_accounts,
        total_income=total_income,
        total_expense=total_expense,
        net_income=net_income,
        transaction_ids=txn_ids,
    )
