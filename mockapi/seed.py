"""Seed a referentially-consistent, spec-shaped dataset into the mock database.

Generates a realistic amount of related data (properties, units, leases,
transactions, tenants, owners, applicants, vendors, tasks, bills, bank accounts,
files, GL accounts, and work orders) so users can run the MCP tools against the
mock and see meaningful, connected output.
"""

from __future__ import annotations

from datetime import date, timedelta

from sqlalchemy.orm import Session

from .db import Entity, SessionLocal, reset_db
from .store import create_doc

# Dataset sizing (kept modest so seeding is fast in CI containers).
NUM_PROPERTIES = 10
UNITS_PER_PROPERTY = 3
NUM_ASSOCIATIONS = 4
NUM_LEASES = 25
NUM_APPLICANTS = 12
NUM_VENDORS = 8
NUM_TASKS = 15
NUM_BILLS = 12
NUM_BANK_ACCOUNTS = 3
NUM_WORK_ORDERS = 10

_TODAY = date(2026, 1, 15)


def _addr(i: int) -> dict:
    return {
        "AddressLine1": f"{100 + i} Market Street",
        "AddressLine2": f"Suite {i}" if i % 3 == 0 else None,
        "City": "Springfield",
        "State": "IL",
        "PostalCode": f"627{i:02d}",
        "Country": "UnitedStates",
    }


def _seed_properties(session: Session) -> list[dict]:
    props = []
    for i in range(1, NUM_PROPERTIES + 1):
        doc = {
            "Id": i,
            "Name": f"Rental Property {i}",
            "StructureDescription": "Single family home" if i % 2 else "Duplex",
            "NumberUnits": UNITS_PER_PROPERTY,
            "IsActive": True,
            "OperatingBankAccountId": 1 + (i % NUM_BANK_ACCOUNTS),
            "Reserve": 500.0 * i,
            "Address": _addr(i),
            "YearBuilt": 1980 + i,
            "RentalType": "Residential",
            "RentalSubType": "SingleFamily" if i % 2 else "MultiFamily",
            "RentalManager": {"Id": 1, "FirstName": "Dana", "LastName": "Rivera"},
        }
        create_doc(session, "rentals", doc, entity_id=i, status="Active")
        props.append(doc)
    return props


def _seed_units(session: Session) -> list[dict]:
    units = []
    unit_id = 1
    for pid in range(1, NUM_PROPERTIES + 1):
        for u in range(1, UNITS_PER_PROPERTY + 1):
            occupied = (unit_id % 3) != 0
            doc = {
                "Id": unit_id,
                "PropertyId": pid,
                "BuildingName": f"Building {pid}",
                "UnitNumber": f"{u}0{pid}",
                "Description": f"Unit {u} at property {pid}",
                "MarketRent": 1200.0 + 50 * unit_id,
                "Address": _addr(pid),
                "UnitBedrooms": "TwoBed",
                "UnitBathrooms": "OneBath",
                "UnitSize": 800 + 10 * u,
                "IsUnitListed": not occupied,
                "IsUnitOccupied": occupied,
            }
            create_doc(
                session,
                "units",
                doc,
                entity_id=unit_id,
                property_id=pid,
                unit_id=unit_id,
                status="Active",
            )
            units.append(doc)
            unit_id += 1
    return units


def _seed_unit_listings(session: Session, units: list[dict]) -> None:
    listing_id = 1
    for unit in units:
        if not unit["IsUnitListed"]:
            continue
        doc = {
            "Id": listing_id,
            "UnitId": unit["Id"],
            "PropertyId": unit["PropertyId"],
            "IsActive": True,
            "AvailableDate": (_TODAY + timedelta(days=14)).isoformat(),
            "ContactName": "Leasing Office",
            "ContactPhoneNumber": {"Number": "555-0100", "Type": "Office"},
            "ContactEmail": "leasing@example.com",
            "Rent": unit["MarketRent"],
        }
        create_doc(
            session,
            "listings",
            doc,
            entity_id=listing_id,
            property_id=unit["PropertyId"],
            unit_id=unit["Id"],
        )
        listing_id += 1


def _seed_leases(session: Session, units: list[dict]) -> list[dict]:
    leases = []
    for i in range(1, NUM_LEASES + 1):
        unit = units[(i - 1) % len(units)]
        status = ["Active", "Active", "Past", "Future"][i % 4]
        start = _TODAY - timedelta(days=365 - i * 5)
        doc = {
            "Id": i,
            "PropertyId": unit["PropertyId"],
            "UnitId": unit["Id"],
            "UnitNumber": unit["UnitNumber"],
            "LeaseFromDate": start.isoformat(),
            "LeaseToDate": (start + timedelta(days=365)).isoformat(),
            "LeaseType": "Fixed",
            "LeaseStatus": status,
            "IsEvictionPending": False,
            "TermType": "Standard",
            "RenewalOfferStatus": "NotStarted",
            "CurrentNumberOfOccupants": 1 + (i % 3),
            "AccountDetails": {"Rent": 1200.0 + i * 25, "SecurityDeposit": 1500.0},
            "PaymentDueDay": 1,
            "AutomaticallyMoveOutTenants": False,
            "CreatedDateTime": start.isoformat() + "T00:00:00Z",
            "LastUpdatedDateTime": _TODAY.isoformat() + "T00:00:00Z",
            "CurrentTenants": [{"Id": i, "FirstName": f"Tenant{i}", "LastName": "Doe"}],
        }
        create_doc(
            session,
            "leases",
            doc,
            entity_id=i,
            property_id=unit["PropertyId"],
            unit_id=unit["Id"],
            status=status,
        )
        leases.append(doc)
        _seed_lease_transactions(session, i)
    return leases


def _seed_lease_transactions(session: Session, lease_id: int) -> None:
    for t in range(1, 4):
        tx_id = lease_id * 100 + t
        doc = {
            "Id": tx_id,
            "Date": (_TODAY - timedelta(days=30 * t)).isoformat(),
            "TransactionType": "Charge" if t % 2 else "Payment",
            "TotalAmount": 1200.0 if t % 2 else -1200.0,
            "CheckNumber": None,
            "LeaseId": lease_id,
            "Memo": f"Monthly {'rent charge' if t % 2 else 'payment'}",
        }
        create_doc(
            session,
            "lease_transactions",
            doc,
            entity_id=tx_id,
            parent_type="lease",
            parent_id=lease_id,
        )


def _seed_lease_tenants(session: Session, leases: list[dict]) -> None:
    for lease in leases:
        tid = lease["Id"]
        doc = {
            "Id": tid,
            "FirstName": f"Tenant{tid}",
            "LastName": "Doe",
            "Email": f"tenant{tid}@example.com",
            "PhoneNumbers": [{"Number": f"555-02{tid:02d}", "Type": "Cell"}],
            "PropertyId": lease["PropertyId"],
            "UnitId": lease["UnitId"],
            "Leases": [{"Id": lease["Id"], "LeaseStatus": lease["LeaseStatus"]}],
            "Address": _addr(tid),
        }
        create_doc(
            session,
            "lease_tenants",
            doc,
            entity_id=tid,
            property_id=lease["PropertyId"],
            unit_id=lease["UnitId"],
        )


def _seed_associations(session: Session) -> list[dict]:
    assocs = []
    for i in range(1, NUM_ASSOCIATIONS + 1):
        doc = {
            "Id": i,
            "Name": f"Homeowners Association {i}",
            "IsActive": True,
            "Reserve": 10000.0 * i,
            "Description": f"HOA number {i}",
            "YearBuilt": 1990 + i,
            "OperatingBankAccountId": 1 + (i % NUM_BANK_ACCOUNTS),
            "Address": _addr(i),
            "FiscalYearEndDay": 31,
            "FiscalYearEndMonth": 12,
        }
        create_doc(session, "associations", doc, entity_id=i, association_id=i, status="Active")
        assocs.append(doc)
        _seed_association_children(session, i)
    return assocs


def _seed_association_children(session: Session, association_id: int) -> None:
    # Association units
    for u in range(1, 3):
        uid = association_id * 10 + u
        doc = {
            "Id": uid,
            "AssociationId": association_id,
            "UnitNumber": f"A{uid}",
            "Address": _addr(uid),
            "UnitBedrooms": "TwoBed",
            "UnitBathrooms": "TwoBath",
        }
        create_doc(
            session,
            "association_units",
            doc,
            entity_id=uid,
            association_id=association_id,
        )
    # Board members
    for b in range(1, 3):
        bid = association_id * 10 + b
        doc = {
            "Id": bid,
            "AssociationId": association_id,
            "FirstName": f"Board{bid}",
            "LastName": "Member",
            "Email": f"board{bid}@example.com",
            "Title": "President" if b == 1 else "Treasurer",
            "IsActive": True,
        }
        create_doc(
            session,
            "board_members",
            doc,
            entity_id=bid,
            association_id=association_id,
        )
    # Ownership accounts
    oid = association_id
    create_doc(
        session,
        "ownership_accounts",
        {
            "Id": oid,
            "AssociationId": association_id,
            "UnitId": association_id * 10 + 1,
            "Status": "Active",
        },
        entity_id=oid,
        association_id=association_id,
        status="Active",
    )
    # Association tenants
    tid = association_id
    create_doc(
        session,
        "association_tenants",
        {
            "Id": tid,
            "AssociationId": association_id,
            "FirstName": f"AssocTenant{tid}",
            "LastName": "Smith",
            "Email": f"assoctenant{tid}@example.com",
            "PhoneNumbers": [{"Number": f"555-03{tid:02d}", "Type": "Cell"}],
            "PrimaryAddress": _addr(tid),
        },
        entity_id=tid,
        association_id=association_id,
    )
    # Association owners
    create_doc(
        session,
        "association_owners",
        {
            "Id": association_id,
            "AssociationId": association_id,
            "FirstName": f"AssocOwner{association_id}",
            "LastName": "Jones",
            "Email": f"assocowner{association_id}@example.com",
            "PrimaryAddress": _addr(association_id),
        },
        entity_id=association_id,
        association_id=association_id,
    )


def _seed_rental_owners(session: Session) -> None:
    for i in range(1, NUM_PROPERTIES + 1):
        doc = {
            "Id": i,
            "FirstName": f"Owner{i}",
            "LastName": "Property",
            "Email": f"owner{i}@example.com",
            "IsCompany": i % 4 == 0,
            "PropertyIds": [i],
            "Address": _addr(i),
        }
        create_doc(session, "rental_owners", doc, entity_id=i, property_id=i)


def _seed_applicants(session: Session) -> None:
    for i in range(1, NUM_APPLICANTS + 1):
        doc = {
            "Id": i,
            "FirstName": f"Applicant{i}",
            "LastName": "Prospect",
            "Email": f"applicant{i}@example.com",
            "PhoneNumber": f"555-03{i:02d}",
            "Status": ["Undecided", "Approved", "Rejected"][i % 3],
        }
        create_doc(session, "applicants", doc, entity_id=i)
        # One application per applicant
        app_id = i
        create_doc(
            session,
            "applications",
            {
                "Id": app_id,
                "ApplicantId": i,
                "Status": doc["Status"],
                "PropertyId": 1 + (i % NUM_PROPERTIES),
            },
            entity_id=app_id,
            parent_type="applicant",
            parent_id=i,
        )
    # Applicant groups
    for g in range(1, 4):
        create_doc(
            session,
            "applicant_groups",
            {
                "Id": g,
                "Name": f"Applicant Group {g}",
                "PropertyId": g,
                "ApplicantIds": [g, g + 3],
            },
            entity_id=g,
        )


def _seed_vendors(session: Session) -> None:
    for c in range(1, 4):
        create_doc(
            session,
            "vendor_categories",
            {"Id": c, "Name": f"Vendor Category {c}"},
            entity_id=c,
        )
    for i in range(1, NUM_VENDORS + 1):
        status = "Active" if i % 5 else "Inactive"
        doc = {
            "Id": i,
            "IsCompany": True,
            "CompanyName": f"Vendor Co {i}",
            "FirstName": None,
            "LastName": None,
            "Email": f"vendor{i}@example.com",
            "Category": {"Id": 1 + (i % 3), "Name": f"Vendor Category {1 + (i % 3)}"},
            "IsActive": status == "Active",
            "Address": _addr(i),
        }
        create_doc(session, "vendors", doc, entity_id=i, vendor_id=i, status=status)


def _seed_tasks(session: Session) -> None:
    for c in range(1, 4):
        create_doc(
            session,
            "task_categories",
            {"Id": c, "Name": f"Task Category {c}"},
            entity_id=c,
        )
    statuses = ["New", "InProgress", "Completed", "Deferred", "Closed"]
    for i in range(1, NUM_TASKS + 1):
        status = statuses[i % len(statuses)]
        doc = {
            "Id": i,
            "Title": f"Task {i}",
            "Description": f"Description for task {i}",
            "Category": {"Id": 1 + (i % 3), "Name": f"Task Category {1 + (i % 3)}"},
            "TaskStatus": status,
            "Priority": ["Low", "Normal", "High"][i % 3],
            "PropertyId": 1 + (i % NUM_PROPERTIES),
            "AssignedToUserId": 1,
            "DueDate": (_TODAY + timedelta(days=i)).isoformat(),
        }
        create_doc(session, "tasks", doc, entity_id=i, status=status)


def _seed_bank_accounts(session: Session) -> None:
    for i in range(1, NUM_BANK_ACCOUNTS + 1):
        status = "Active"
        doc = {
            "Id": i,
            "Name": f"Operating Account {i}",
            "BankAccountType": "Checking",
            "Country": "UnitedStates",
            "AccountNumberUnmasked": f"00000{i}",
            "AccountNumber": f"****{i}",
            "RoutingNumber": "021000021",
            "IsActive": True,
            "Balance": 25000.0 * i,
            "CheckPrintingInfo": {
                "EnableRemoteCheckPrinting": False,
                "EnableLocalCheckPrinting": False,
                "CheckLayoutType": "Voucher2StubTopMemo",
            },
        }
        create_doc(session, "bank_accounts", doc, entity_id=i, status=status)
        for t in range(1, 4):
            tx_id = i * 100 + t
            create_doc(
                session,
                "bank_transactions",
                {
                    "Id": tx_id,
                    "BankAccountId": i,
                    "Date": (_TODAY - timedelta(days=t * 7)).isoformat(),
                    "TransactionType": "Deposit" if t % 2 else "Withdrawal",
                    "TotalAmount": 500.0 * t if t % 2 else -250.0 * t,
                    "Memo": f"Transaction {t} for account {i}",
                },
                entity_id=tx_id,
                parent_type="bank_account",
                parent_id=i,
            )


def _seed_bills(session: Session) -> None:
    for i in range(1, NUM_BILLS + 1):
        vendor_id = 1 + (i % NUM_VENDORS)
        paid = "Paid" if i % 2 else "Unpaid"
        gl_index = 1 + (i % 3)
        doc = {
            "Id": i,
            "Date": (_TODAY - timedelta(days=i * 3)).isoformat(),
            "DueDate": (_TODAY + timedelta(days=i)).isoformat(),
            "VendorId": vendor_id,
            "ReferenceNumber": f"BILL-{i:04d}",
            "Memo": f"Bill {i}",
            "ApprovalStatus": "Approved",
            "Lines": [
                {
                    "Id": i * 100 + 1,
                    "AccountingEntity": {
                        "Id": 1 + (i % NUM_PROPERTIES),
                        "AccountingEntityType": "Rental",
                    },
                    "GLAccount": {"Id": 5000 + gl_index, "Name": f"GL Account {gl_index}"},
                    "Amount": 200.0 * i,
                    "Memo": "Service line",
                }
            ],
        }
        create_doc(session, "bills", doc, entity_id=i, vendor_id=vendor_id, status=paid)
        if i % 2 == 0:
            pid = i * 10
            create_doc(
                session,
                "bill_payments",
                {
                    "Id": pid,
                    "BillId": i,
                    "Date": (_TODAY - timedelta(days=i)).isoformat(),
                    "BankAccountId": 1 + (i % NUM_BANK_ACCOUNTS),
                    "TotalAmount": 200.0 * i,
                },
                entity_id=pid,
                parent_type="bill",
                parent_id=i,
            )


def _seed_files(session: Session) -> None:
    for c in range(1, 4):
        create_doc(
            session,
            "file_categories",
            {"Id": c, "Name": f"File Category {c}"},
            entity_id=c,
        )
    for i in range(1, 9):
        doc = {
            "Id": i,
            "Title": f"Document {i}.pdf",
            "PhysicalFileName": f"document_{i}.pdf",
            "CategoryId": 1 + (i % 3),
            "Description": f"Seeded file {i}",
            "ContentType": "application/pdf",
            "Size": 1024 * i,
            "EntityType": "Rental",
            "EntityId": 1 + (i % NUM_PROPERTIES),
        }
        create_doc(session, "files", doc, entity_id=i, property_id=1 + (i % NUM_PROPERTIES))


def _seed_general_ledger(session: Session) -> None:
    types = ["Asset", "Liability", "Equity", "Income", "Expense"]
    for i in range(1, 11):
        acct_id = 5000 + i
        doc = {
            "Id": acct_id,
            "AccountNumber": f"{1000 + i}",
            "Name": f"GL Account {i}",
            "Type": types[i % len(types)],
            "IsDefaultGLAccount": False,
            "IsActive": True,
        }
        create_doc(session, "gl_accounts", doc, entity_id=acct_id, status=types[i % len(types)])
    for i in range(1, 21):
        tx_id = 9000 + i
        doc = {
            "Id": tx_id,
            "Date": (_TODAY - timedelta(days=i)).isoformat(),
            "TransactionType": "Bill" if i % 2 else "Payment",
            "TotalAmount": 150.0 * i,
            "Memo": f"GL transaction {i}",
            "Journal": {"Lines": [{"GLAccountId": 5001 + (i % 10), "Amount": 150.0 * i}]},
        }
        create_doc(
            session,
            "gl_transactions",
            doc,
            entity_id=tx_id,
        )


def _seed_work_orders(session: Session) -> None:
    statuses = ["New", "InProgress", "Completed", "Deferred", "Closed"]
    for i in range(1, NUM_WORK_ORDERS + 1):
        status = statuses[i % len(statuses)]
        doc = {
            "Id": i,
            "Title": f"Work Order {i}",
            "WorkOrderStatus": status,
            "Priority": ["Low", "Normal", "High"][i % 3],
            "EntryAllowed": "Yes",
            "EntityType": "Rental",
            "EntityId": 1 + (i % NUM_PROPERTIES),
            "AssignedToUserId": 1,
            "VendorId": 1 + (i % NUM_VENDORS),
            "DueDate": (_TODAY + timedelta(days=i)).isoformat(),
        }
        create_doc(session, "work_orders", doc, entity_id=i, status=status)


def seed_all(session: Session) -> dict[str, int]:
    """Seed the entire dataset and return per-resource record counts."""
    _seed_properties(session)
    units = _seed_units(session)
    _seed_unit_listings(session, units)
    leases = _seed_leases(session, units)
    _seed_lease_tenants(session, leases)
    _seed_associations(session)
    _seed_rental_owners(session)
    _seed_applicants(session)
    _seed_vendors(session)
    _seed_tasks(session)
    _seed_bank_accounts(session)
    _seed_bills(session)
    _seed_files(session)
    _seed_general_ledger(session)
    _seed_work_orders(session)

    from sqlalchemy import func, select

    rows = session.execute(select(Entity.resource, func.count()).group_by(Entity.resource)).all()
    return {resource: n for resource, n in rows}


def main() -> None:
    """Reset and seed the database, printing a summary."""
    reset_db()
    session = SessionLocal()
    try:
        counts = seed_all(session)
    finally:
        session.close()
    total = sum(counts.values())
    print(f"Seeded {total} records across {len(counts)} resources:")
    for resource in sorted(counts):
        print(f"  {resource:22s} {counts[resource]}")


if __name__ == "__main__":
    main()
