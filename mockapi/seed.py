"""Seed a referentially-consistent, spec-shaped dataset into the mock database.

Generates a realistic amount of related data (properties, units, leases,
transactions, tenants, owners, applicants, vendors, tasks, bills, bank accounts,
files, GL accounts, and work orders) so users can run the MCP tools against the
mock and see meaningful, connected output.

The values are intentionally realistic (real-sounding names, streets, cities and
company names) while every field required by the Buildium OpenAPI schema stays
populated so the data round-trips through the generated SDK models.
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

# --- Realistic data pools ---------------------------------------------------
# Deterministically indexed so referential integrity (by Id) is preserved.

# Real-sounding people, used for tenants, owners, board members, managers, etc.
_PEOPLE: list[tuple[str, str]] = [
    ("Michael", "Thompson"),
    ("Jennifer", "Martinez"),
    ("David", "Nguyen"),
    ("Sarah", "Patel"),
    ("James", "Robinson"),
    ("Emily", "Carter"),
    ("Robert", "Kim"),
    ("Jessica", "Alvarez"),
    ("William", "O'Brien"),
    ("Ashley", "Johnson"),
    ("Christopher", "Reyes"),
    ("Amanda", "Foster"),
    ("Daniel", "Washington"),
    ("Rachel", "Goldberg"),
    ("Matthew", "Sullivan"),
    ("Lauren", "Bennett"),
    ("Andrew", "Torres"),
    ("Megan", "Fisher"),
    ("Joshua", "Coleman"),
    ("Nicole", "Rivera"),
    ("Brandon", "Murphy"),
    ("Stephanie", "Hughes"),
    ("Kevin", "Brooks"),
    ("Olivia", "Sanders"),
    ("Ryan", "Powell"),
    ("Hannah", "Barnes"),
    ("Justin", "Ross"),
    ("Victoria", "Perry"),
    ("Tyler", "Butler"),
    ("Samantha", "Long"),
]

# Real US cities paired with their state and a plausible ZIP + area code.
_LOCATIONS: list[tuple[str, str, str, str]] = [
    ("Austin", "TX", "78701", "512"),
    ("Denver", "CO", "80202", "303"),
    ("Portland", "OR", "97205", "503"),
    ("Seattle", "WA", "98104", "206"),
    ("Nashville", "TN", "37203", "615"),
    ("Columbus", "OH", "43215", "614"),
    ("Raleigh", "NC", "27601", "919"),
    ("Charlotte", "NC", "28202", "704"),
    ("Atlanta", "GA", "30303", "404"),
    ("Phoenix", "AZ", "85004", "602"),
]

_STREETS: list[str] = [
    "Maple Avenue",
    "Oak Street",
    "Cedar Lane",
    "Elm Street",
    "Sunset Boulevard",
    "Riverside Drive",
    "Park Avenue",
    "Highland Road",
    "Washington Street",
    "Lincoln Avenue",
    "Birchwood Court",
    "Magnolia Drive",
]

# Community-style names for rental properties.
_PROPERTY_NAMES: list[str] = [
    "Maplewood Apartments",
    "Cedar Ridge Townhomes",
    "Sunset Villas",
    "Riverside Commons",
    "Highland Park Residences",
    "Oakmont Terrace",
    "Willow Creek Flats",
    "Birchwood Court Homes",
    "Magnolia Gardens",
    "Lincoln Square Lofts",
]


def _person(i: int) -> tuple[str, str]:
    return _PEOPLE[(i - 1) % len(_PEOPLE)]


def _email(first: str, last: str, i: int) -> str:
    handle = f"{first}.{last}".lower().replace("'", "").replace(" ", "")
    return f"{handle}{i}@example.com"


def _phone(i: int) -> str:
    _, _, _, area = _LOCATIONS[(i - 1) % len(_LOCATIONS)]
    # 555-01xx is the range reserved for fictional use.
    return f"({area}) 555-{100 + (i % 100):04d}"


def _addr(i: int) -> dict:
    city, state, base_zip, _ = _LOCATIONS[(i - 1) % len(_LOCATIONS)]
    street = _STREETS[(i - 1) % len(_STREETS)]
    return {
        "AddressLine1": f"{100 + i * 7} {street}",
        "AddressLine2": f"Suite {i}" if i % 3 == 0 else None,
        "City": city,
        "State": state,
        "PostalCode": base_zip,
        "Country": "UnitedStates",
    }


def _seed_properties(session: Session) -> list[dict]:
    props = []
    for i in range(1, NUM_PROPERTIES + 1):
        mgr_first, mgr_last = _person(i + 20)
        doc = {
            "Id": i,
            "Name": _PROPERTY_NAMES[(i - 1) % len(_PROPERTY_NAMES)],
            "StructureDescription": "Single family home" if i % 2 else "Duplex",
            "NumberUnits": UNITS_PER_PROPERTY,
            "IsActive": True,
            "OperatingBankAccountId": 1 + (i % NUM_BANK_ACCOUNTS),
            "Reserve": 500.0 * i,
            "Address": _addr(i),
            "YearBuilt": 1980 + i,
            "RentalType": "Residential",
            "RentalSubType": "SingleFamily" if i % 2 else "MultiFamily",
            "RentalManager": {"Id": 1, "FirstName": mgr_first, "LastName": mgr_last},
        }
        create_doc(session, "rentals", doc, entity_id=i, status="Active")
        props.append(doc)
    return props


def _seed_units(session: Session) -> list[dict]:
    units = []
    unit_id = 1
    for pid in range(1, NUM_PROPERTIES + 1):
        building = _PROPERTY_NAMES[(pid - 1) % len(_PROPERTY_NAMES)]
        for u in range(1, UNITS_PER_PROPERTY + 1):
            occupied = (unit_id % 3) != 0
            doc = {
                "Id": unit_id,
                "PropertyId": pid,
                "BuildingName": building,
                "UnitNumber": f"{u}0{pid}",
                "Description": (
                    f"{['Studio', 'One-bedroom', 'Two-bedroom'][u % 3]} unit at {building}"
                ),
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
        building = unit["BuildingName"]
        doc = {
            "Id": listing_id,
            "UnitId": unit["Id"],
            "PropertyId": unit["PropertyId"],
            "IsActive": True,
            "AvailableDate": (_TODAY + timedelta(days=14)).isoformat(),
            "ContactName": f"{building} Leasing Office",
            "ContactPhoneNumber": {"Number": _phone(listing_id), "Type": "Office"},
            "ContactEmail": "leasing@rockford-pm.com",
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
        first, last = _person(i)
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
            "CurrentTenants": [{"Id": i, "FirstName": first, "LastName": last}],
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
        is_charge = bool(t % 2)
        doc = {
            "Id": tx_id,
            "Date": (_TODAY - timedelta(days=30 * t)).isoformat(),
            "TransactionType": "Charge" if is_charge else "Payment",
            "TotalAmount": 1200.0 if is_charge else -1200.0,
            "CheckNumber": None if is_charge else f"{1000 + tx_id}",
            "LeaseId": lease_id,
            "Memo": "Monthly rent charge" if is_charge else "Rent payment received",
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
        first, last = _person(tid)
        doc = {
            "Id": tid,
            "FirstName": first,
            "LastName": last,
            "Email": _email(first, last, tid),
            "PhoneNumbers": [{"Number": _phone(tid), "Type": "Cell"}],
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


# Community-style names for homeowners associations.
_HOA_NAMES: list[str] = [
    "Willow Creek Homeowners Association",
    "Stonebridge Community Association",
    "Lakeside Villas HOA",
    "Prairie Ridge Homeowners Association",
]


def _seed_associations(session: Session) -> list[dict]:
    assocs = []
    for i in range(1, NUM_ASSOCIATIONS + 1):
        name = _HOA_NAMES[(i - 1) % len(_HOA_NAMES)]
        doc = {
            "Id": i,
            "Name": name,
            "IsActive": True,
            "Reserve": 10000.0 * i,
            "Description": f"{name} managing {2 + i} residential buildings",
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
            "UnitNumber": f"{chr(64 + association_id)}-{100 + u}",
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
        first, last = _person(bid + 10)
        doc = {
            "Id": bid,
            "AssociationId": association_id,
            "FirstName": first,
            "LastName": last,
            "Email": _email(first, last, bid),
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
    at_first, at_last = _person(tid + 4)
    create_doc(
        session,
        "association_tenants",
        {
            "Id": tid,
            "AssociationId": association_id,
            "FirstName": at_first,
            "LastName": at_last,
            "Email": _email(at_first, at_last, tid),
            "PhoneNumbers": [{"Number": _phone(tid + 4), "Type": "Cell"}],
            "PrimaryAddress": _addr(tid),
        },
        entity_id=tid,
        association_id=association_id,
    )
    # Association owners
    ao_first, ao_last = _person(association_id + 8)
    create_doc(
        session,
        "association_owners",
        {
            "Id": association_id,
            "AssociationId": association_id,
            "FirstName": ao_first,
            "LastName": ao_last,
            "Email": _email(ao_first, ao_last, association_id),
            "PrimaryAddress": _addr(association_id),
        },
        entity_id=association_id,
        association_id=association_id,
    )


def _seed_rental_owners(session: Session) -> None:
    for i in range(1, NUM_PROPERTIES + 1):
        first, last = _person(i + 12)
        is_company = i % 4 == 0
        doc = {
            "Id": i,
            "FirstName": first,
            "LastName": last,
            "Email": _email(first, last, i + 12),
            "IsCompany": is_company,
            "PropertyIds": [i],
            "Address": _addr(i + 12),
        }
        if is_company:
            doc["CompanyName"] = f"{last} Property Holdings LLC"
        create_doc(session, "rental_owners", doc, entity_id=i, property_id=i)


def _seed_applicants(session: Session) -> None:
    for i in range(1, NUM_APPLICANTS + 1):
        first, last = _person(i + 15)
        doc = {
            "Id": i,
            "FirstName": first,
            "LastName": last,
            "Email": _email(first, last, i + 15),
            "PhoneNumber": _phone(i + 15),
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
                "Name": f"{_person(g)[1]} Household",
                "PropertyId": g,
                "ApplicantIds": [g, g + 3],
            },
            entity_id=g,
        )


# Trade categories and matching company names for vendors.
_VENDOR_CATEGORIES: list[str] = ["Plumbing", "Electrical", "Landscaping"]
_VENDOR_COMPANIES: list[str] = [
    "Precision Plumbing Services",
    "BrightSpark Electric Co.",
    "Evergreen Landscaping LLC",
    "Reliable Roofing & Repair",
    "Summit HVAC Solutions",
    "ClearView Window Cleaning",
    "Apex General Contractors",
    "Metro Pest Control Inc.",
]


def _seed_vendors(session: Session) -> None:
    for c in range(1, 4):
        create_doc(
            session,
            "vendor_categories",
            {"Id": c, "Name": _VENDOR_CATEGORIES[(c - 1) % len(_VENDOR_CATEGORIES)]},
            entity_id=c,
        )
    for i in range(1, NUM_VENDORS + 1):
        status = "Active" if i % 5 else "Inactive"
        cat_id = 1 + (i % 3)
        company = _VENDOR_COMPANIES[(i - 1) % len(_VENDOR_COMPANIES)]
        handle = (
            company.lower().replace(" ", "").replace("&", "and").replace(".", "").replace(",", "")
        )
        doc = {
            "Id": i,
            "IsCompany": True,
            "CompanyName": company,
            "FirstName": None,
            "LastName": None,
            "Email": f"contact@{handle}.com",
            "Category": {
                "Id": cat_id,
                "Name": _VENDOR_CATEGORIES[(cat_id - 1) % len(_VENDOR_CATEGORIES)],
            },
            "IsActive": status == "Active",
            "Address": _addr(i),
        }
        create_doc(session, "vendors", doc, entity_id=i, vendor_id=i, status=status)


# Task categories and realistic maintenance/inspection task descriptions.
_TASK_CATEGORIES: list[str] = ["Maintenance", "Inspection", "Administrative"]
_TASK_TITLES: list[str] = [
    "Repair leaking kitchen faucet",
    "Replace HVAC air filter",
    "Inspect smoke and CO detectors",
    "Fix loose stair railing",
    "Service garbage disposal",
    "Repaint hallway walls",
    "Unclog bathroom drain",
    "Replace broken window screen",
    "Test water heater pressure valve",
    "Repair garage door opener",
    "Clean and inspect gutters",
    "Replace worn carpet in living room",
    "Fix flickering porch light",
    "Reseal bathroom grout",
    "Trim overgrown landscaping",
]


def _seed_tasks(session: Session) -> None:
    for c in range(1, 4):
        create_doc(
            session,
            "task_categories",
            {"Id": c, "Name": _TASK_CATEGORIES[(c - 1) % len(_TASK_CATEGORIES)]},
            entity_id=c,
        )
    statuses = ["New", "InProgress", "Completed", "Deferred", "Closed"]
    for i in range(1, NUM_TASKS + 1):
        status = statuses[i % len(statuses)]
        cat_id = 1 + (i % 3)
        title = _TASK_TITLES[(i - 1) % len(_TASK_TITLES)]
        doc = {
            "Id": i,
            "Title": title,
            "Description": f"{title} reported by resident; schedule with maintenance team.",
            "Category": {
                "Id": cat_id,
                "Name": _TASK_CATEGORIES[(cat_id - 1) % len(_TASK_CATEGORIES)],
            },
            "TaskStatus": status,
            "Priority": ["Low", "Normal", "High"][i % 3],
            "PropertyId": 1 + (i % NUM_PROPERTIES),
            "AssignedToUserId": 1,
            "DueDate": (_TODAY + timedelta(days=i)).isoformat(),
        }
        create_doc(session, "tasks", doc, entity_id=i, status=status)


_BANK_ACCOUNT_NAMES: list[str] = [
    "Operating Account - First National Bank",
    "Security Deposit Trust - Community Bank",
    "Reserve Account - Heartland Credit Union",
]


def _seed_bank_accounts(session: Session) -> None:
    for i in range(1, NUM_BANK_ACCOUNTS + 1):
        status = "Active"
        doc = {
            "Id": i,
            "Name": _BANK_ACCOUNT_NAMES[(i - 1) % len(_BANK_ACCOUNT_NAMES)],
            "BankAccountType": "Checking",
            "Country": "UnitedStates",
            "AccountNumberUnmasked": f"1000{i:04d}",
            "AccountNumber": f"****{i:04d}",
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
            is_deposit = bool(t % 2)
            create_doc(
                session,
                "bank_transactions",
                {
                    "Id": tx_id,
                    "BankAccountId": i,
                    "Date": (_TODAY - timedelta(days=t * 7)).isoformat(),
                    "TransactionType": "Deposit" if is_deposit else "Withdrawal",
                    "TotalAmount": 500.0 * t if is_deposit else -250.0 * t,
                    "Memo": "Tenant rent deposit" if is_deposit else "Vendor payment",
                },
                entity_id=tx_id,
                parent_type="bank_account",
                parent_id=i,
            )


# GL accounts with realistic names paired to their account type.
_GL_ACCOUNTS: list[tuple[str, str]] = [
    ("Cash - Operating", "Asset"),
    ("Accounts Receivable", "Asset"),
    ("Security Deposits Held", "Liability"),
    ("Accounts Payable", "Liability"),
    ("Owner Equity", "Equity"),
    ("Rental Income", "Income"),
    ("Late Fee Income", "Income"),
    ("Repairs and Maintenance", "Expense"),
    ("Property Insurance", "Expense"),
    ("Landscaping Expense", "Expense"),
]


def _seed_bills(session: Session) -> None:
    for i in range(1, NUM_BILLS + 1):
        vendor_id = 1 + (i % NUM_VENDORS)
        paid = "Paid" if i % 2 else "Unpaid"
        # Bill expenses map onto the expense GL accounts (indices 8-10 above).
        gl_id = 5008 + ((i - 1) % 3)
        gl_name = _GL_ACCOUNTS[7 + ((i - 1) % 3)][0]
        doc = {
            "Id": i,
            "Date": (_TODAY - timedelta(days=i * 3)).isoformat(),
            "DueDate": (_TODAY + timedelta(days=i)).isoformat(),
            "VendorId": vendor_id,
            "ReferenceNumber": f"INV-2026{i:04d}",
            "Memo": f"Invoice from {_VENDOR_COMPANIES[(vendor_id - 1) % len(_VENDOR_COMPANIES)]}",
            "ApprovalStatus": "Approved",
            "Lines": [
                {
                    "Id": i * 100 + 1,
                    "AccountingEntity": {
                        "Id": 1 + (i % NUM_PROPERTIES),
                        "AccountingEntityType": "Rental",
                    },
                    "GLAccount": {"Id": gl_id, "Name": gl_name},
                    "Amount": 200.0 * i,
                    "Memo": f"{gl_name} charge",
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


_FILE_CATEGORIES: list[str] = ["Leases", "Inspections", "Insurance"]
_FILE_TITLES: list[str] = [
    "Signed Lease Agreement",
    "Move-in Inspection Report",
    "Certificate of Insurance",
    "Property Condition Report",
    "Rent Ledger Statement",
    "Maintenance Work Summary",
    "Renewal Offer Letter",
    "Move-out Inspection Report",
]


def _seed_files(session: Session) -> None:
    for c in range(1, 4):
        create_doc(
            session,
            "file_categories",
            {"Id": c, "Name": _FILE_CATEGORIES[(c - 1) % len(_FILE_CATEGORIES)]},
            entity_id=c,
        )
    for i in range(1, 9):
        title = _FILE_TITLES[(i - 1) % len(_FILE_TITLES)]
        slug = title.lower().replace(" ", "_")
        doc = {
            "Id": i,
            "Title": f"{title}.pdf",
            "PhysicalFileName": f"{slug}_{i}.pdf",
            "CategoryId": 1 + (i % 3),
            "Description": f"{title} for property {1 + (i % NUM_PROPERTIES)}",
            "ContentType": "application/pdf",
            "Size": 1024 * i,
            "EntityType": "Rental",
            "EntityId": 1 + (i % NUM_PROPERTIES),
        }
        create_doc(session, "files", doc, entity_id=i, property_id=1 + (i % NUM_PROPERTIES))


def _seed_general_ledger(session: Session) -> None:
    for i in range(1, 11):
        acct_id = 5000 + i
        name, acct_type = _GL_ACCOUNTS[(i - 1) % len(_GL_ACCOUNTS)]
        doc = {
            "Id": acct_id,
            "AccountNumber": f"{1000 + i}",
            "Name": name,
            "Type": acct_type,
            "IsDefaultGLAccount": False,
            "IsActive": True,
        }
        create_doc(session, "gl_accounts", doc, entity_id=acct_id, status=acct_type)
    for i in range(1, 21):
        tx_id = 9000 + i
        doc = {
            "Id": tx_id,
            "Date": (_TODAY - timedelta(days=i)).isoformat(),
            "TransactionType": "Bill" if i % 2 else "Payment",
            "TotalAmount": 150.0 * i,
            "Memo": "Vendor bill posted" if i % 2 else "Owner distribution",
            "Journal": {"Lines": [{"GLAccountId": 5001 + (i % 10), "Amount": 150.0 * i}]},
        }
        create_doc(
            session,
            "gl_transactions",
            doc,
            entity_id=tx_id,
        )


_WORK_ORDER_TITLES: list[str] = [
    "Repair burst pipe under kitchen sink",
    "Restore power to bedroom outlets",
    "Replace damaged roof shingles",
    "Service non-heating furnace",
    "Fix jammed garage door",
    "Replace cracked bathroom tile",
    "Repair broken exterior gate lock",
    "Clear blocked sewer line",
    "Replace failed water heater",
    "Patch and paint water-damaged ceiling",
]


def _seed_work_orders(session: Session) -> None:
    statuses = ["New", "InProgress", "Completed", "Deferred", "Closed"]
    for i in range(1, NUM_WORK_ORDERS + 1):
        status = statuses[i % len(statuses)]
        doc = {
            "Id": i,
            "Title": _WORK_ORDER_TITLES[(i - 1) % len(_WORK_ORDER_TITLES)],
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
