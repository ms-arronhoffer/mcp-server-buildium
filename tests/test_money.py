"""Unit tests for the deterministic money helpers in ``tools._money``.

These verify the pure math that backs the close/reporting tools: FIFO payment
application, aged-receivables bucketing (with reconciliation), and GL-driven
income-statement aggregation.
"""

from __future__ import annotations

from datetime import date

from mcp_server_buildium.tools import _money as m


def test_apply_fifo_applies_oldest_first() -> None:
    charges = [
        {"Id": 1, "Date": "2026-01-01", "Amount": 100.0},
        {"Id": 2, "Date": "2026-02-01", "Amount": 100.0},
        {"Id": 3, "Date": "2026-03-01", "Amount": 100.0},
    ]
    result = m.apply_fifo(charges, payments_total=150.0)
    # First charge fully paid, second half paid, third untouched.
    assert [c.charge_id for c in result.open_charges] == [2, 3]
    assert result.open_charges[0].remaining == 50.0
    assert result.open_charges[1].remaining == 100.0
    assert result.total_open == 150.0
    assert result.unapplied_credit == 0.0
    # Application trail is traceable to source charges.
    assert result.applications[0].charge_id == 1
    assert result.applications[0].amount == 100.0


def test_apply_fifo_leaves_unapplied_credit_when_overpaid() -> None:
    charges = [{"Id": 1, "Date": "2026-01-01", "Amount": 40.0}]
    result = m.apply_fifo(charges, payments_total=100.0)
    assert result.open_charges == []
    assert result.total_open == 0.0
    assert result.unapplied_credit == 60.0


def test_apply_fifo_handles_undated_charges_last() -> None:
    charges = [
        {"Id": 2, "Amount": 50.0},  # undated -> sorts last
        {"Id": 1, "Date": "2026-01-01", "Amount": 50.0},
    ]
    result = m.apply_fifo(charges, payments_total=50.0)
    # The dated charge is paid first.
    assert [c.charge_id for c in result.open_charges] == [2]


def test_age_receivables_buckets_and_reconciles() -> None:
    as_of = date(2026, 4, 1)
    charges = [
        {"Id": 1, "Date": "2026-03-20", "Amount": 100.0},  # ~12 days -> current
        {"Id": 2, "Date": "2026-02-15", "Amount": 100.0},  # ~45 days -> 31-60
        {"Id": 3, "Date": "2026-01-01", "Amount": 100.0},  # ~90 days -> 61-90
        {"Id": 4, "Date": "2025-11-01", "Amount": 100.0},  # >90 -> over 90
    ]
    aging = m.age_receivables(charges, payments_total=0.0, as_of=as_of)
    assert aging.buckets["current"] == 100.0
    assert aging.buckets["days_31_60"] == 100.0
    assert aging.buckets["days_61_90"] == 100.0
    assert aging.buckets["days_over_90"] == 100.0
    assert aging.total == 400.0
    # Reconciliation: buckets sum to the total.
    assert round(sum(aging.buckets.values()), 2) == aging.total


def test_age_receivables_payment_reduces_oldest_bucket() -> None:
    as_of = date(2026, 4, 1)
    charges = [
        {"Id": 1, "Date": "2025-11-01", "Amount": 100.0},  # over 90 (oldest)
        {"Id": 2, "Date": "2026-03-20", "Amount": 100.0},  # current
    ]
    aging = m.age_receivables(charges, payments_total=100.0, as_of=as_of)
    # The oldest (over-90) charge is fully paid; only the current remains.
    assert aging.buckets["days_over_90"] == 0.0
    assert aging.buckets["current"] == 100.0
    assert aging.total == 100.0


def test_sum_aging_combines_ledgers() -> None:
    as_of = date(2026, 4, 1)
    a = m.age_receivables([{"Id": 1, "Date": "2026-03-25", "Amount": 50.0}], 0.0, as_of)
    b = m.age_receivables([{"Id": 2, "Date": "2025-10-01", "Amount": 75.0}], 0.0, as_of)
    combined = m.sum_aging([a, b])
    assert combined["current"] == 50.0
    assert combined["days_over_90"] == 75.0
    assert combined["total"] == 125.0


def test_build_income_statement_signs_and_reconciles() -> None:
    transactions = [
        {
            "Id": 10,
            "Journal": {
                "Lines": [
                    {
                        "GLAccount": {"Id": 1, "Name": "Rent Income", "Type": "Income"},
                        "Amount": 1000.0,
                        "PostingType": "Credit",
                    },
                    {
                        "GLAccount": {"Id": 2, "Name": "Cash", "Type": "Asset"},
                        "Amount": 1000.0,
                        "PostingType": "Debit",
                    },
                ]
            },
        },
        {
            "Id": 11,
            "Journal": {
                "Lines": [
                    {
                        "GLAccount": {"Id": 3, "Name": "Repairs", "Type": "Expense"},
                        "Amount": 250.0,
                        "PostingType": "Debit",
                    },
                    {
                        "GLAccount": {"Id": 2, "Name": "Cash", "Type": "Asset"},
                        "Amount": 250.0,
                        "PostingType": "Credit",
                    },
                ]
            },
        },
    ]
    statement = m.build_income_statement(transactions)
    assert statement.total_income == 1000.0
    assert statement.total_expense == 250.0
    assert statement.net_income == 750.0
    assert statement.reconciles()
    assert set(statement.transaction_ids) == {10, 11}


def test_parse_date_handles_datetime_and_z() -> None:
    assert m.parse_date("2026-01-15T00:00:00Z") == date(2026, 1, 15)
    assert m.parse_date("2026-01-15") == date(2026, 1, 15)
    assert m.parse_date("not-a-date") is None
    assert m.parse_date(None) is None
