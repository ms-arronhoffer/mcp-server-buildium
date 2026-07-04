"""FastAPI application implementing the mock Buildium API.

Routes mirror the Buildium v1 endpoints exercised by the MCP server tools.
Responses are bare JSON arrays for list endpoints and JSON objects for single
resources, matching Buildium's contract so the generated SDK deserializes them
unchanged. Query parameters use Buildium's names (e.g. ``propertyids``,
``limit``, ``offset``).
"""

from __future__ import annotations

from typing import Any

from fastapi import Depends, FastAPI, HTTPException, Request
from sqlalchemy.orm import Session

from . import store
from .db import get_session, init_db


def _int_list(request: Request, name: str) -> list[int] | None:
    """Parse a repeated integer query parameter (Buildium multi-value style)."""
    values = request.query_params.getlist(name)
    if not values:
        return None
    out: list[int] = []
    for v in values:
        for part in v.split(","):
            part = part.strip()
            if part:
                out.append(int(part))
    return out or None


def _str_list(request: Request, name: str) -> list[str] | None:
    values = request.query_params.getlist(name)
    if not values:
        return None
    out: list[str] = []
    for v in values:
        out.extend(p.strip() for p in v.split(",") if p.strip())
    return out or None


def _page(request: Request) -> tuple[int, int]:
    limit = request.query_params.get("limit")
    offset = request.query_params.get("offset")
    return (int(limit) if limit else None, int(offset) if offset else None)


def _require(doc: dict[str, Any] | None, what: str) -> dict[str, Any]:
    if doc is None:
        raise HTTPException(status_code=404, detail=f"{what} not found")
    return doc


# Buildium stores phone numbers as a list of ``{Number, Type}`` entries, but the
# create/update messages accept the keyed ``{Home, Work, Mobile, Fax}`` object
# form. The real API normalizes the object back into the list shape it returns;
# mirror that here so PUT/POST responses deserialize as their ``*Message`` model.
# This applies to every phone-carrying entity (tenants, owners, vendors,
# applicants), all of which expose ``PhoneNumbers`` as a list on read.
_PUT_PHONE_KEY_TO_TYPE = {"home": "Home", "work": "Office", "mobile": "Cell", "fax": "Fax"}


def _normalize_phone_numbers(body: dict[str, Any]) -> dict[str, Any]:
    """Convert a keyed ``PhoneNumbers`` object in ``body`` into Buildium's list form.

    A no-op unless ``PhoneNumbers`` is present as the keyed object form, so it is
    safe to apply to any create/update route.
    """
    phones = body.get("PhoneNumbers")
    if not isinstance(phones, dict):
        return body
    converted = [
        {"Number": number, "Type": _PUT_PHONE_KEY_TO_TYPE.get(str(key).lower(), "Other")}
        for key, number in phones.items()
        if number
    ]
    return {**body, "PhoneNumbers": converted}


def create_app() -> FastAPI:
    """Build and return the FastAPI mock application."""
    app = FastAPI(title="Buildium Mock API", version="1.0.0")
    init_db()

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    # ------------------------------------------------------------------ Rentals
    @app.get("/v1/rentals")
    async def list_rentals(request: Request, db: Session = Depends(get_session)):
        limit, offset = _page(request)
        return store.list_docs(
            db,
            "rentals",
            limit=limit,
            offset=offset,
            property_ids=_int_list(request, "propertyids"),
            statuses=_str_list(request, "status"),
        )

    @app.post("/v1/rentals", status_code=201)
    async def create_rental(body: dict, db: Session = Depends(get_session)):
        return store.create_doc(db, "rentals", body, status="Active")

    # ---------------------------------------------------------- Rental units
    @app.get("/v1/rentals/units")
    async def list_units(request: Request, db: Session = Depends(get_session)):
        limit, offset = _page(request)
        return store.list_docs(
            db,
            "units",
            limit=limit,
            offset=offset,
            property_ids=_int_list(request, "propertyids"),
        )

    @app.post("/v1/rentals/units", status_code=201)
    async def create_unit(body: dict, db: Session = Depends(get_session)):
        return store.create_doc(
            db, "units", body, property_id=body.get("PropertyId"), status="Active"
        )

    @app.get("/v1/rentals/units/listings")
    async def list_listings(request: Request, db: Session = Depends(get_session)):
        limit, offset = _page(request)
        return store.list_docs(
            db,
            "listings",
            limit=limit,
            offset=offset,
            property_ids=_int_list(request, "propertyids"),
        )

    @app.get("/v1/rentals/units/{unit_id}")
    async def get_unit(unit_id: int, db: Session = Depends(get_session)):
        return _require(store.get_doc(db, "units", unit_id), "Unit")

    @app.put("/v1/rentals/units/{unit_id}")
    async def update_unit(unit_id: int, body: dict, db: Session = Depends(get_session)):
        return _require(store.update_doc(db, "units", unit_id, body), "Unit")

    # ---------------------------------------------------------- Rental owners
    @app.get("/v1/rentals/owners")
    async def list_rental_owners(request: Request, db: Session = Depends(get_session)):
        limit, offset = _page(request)
        return store.list_docs(
            db,
            "rental_owners",
            limit=limit,
            offset=offset,
            property_ids=_int_list(request, "propertyids"),
        )

    @app.post("/v1/rentals/owners", status_code=201)
    async def create_rental_owner(body: dict, db: Session = Depends(get_session)):
        return store.create_doc(db, "rental_owners", _normalize_phone_numbers(body))

    @app.get("/v1/rentals/owners/{owner_id}")
    async def get_rental_owner(owner_id: int, db: Session = Depends(get_session)):
        return _require(store.get_doc(db, "rental_owners", owner_id), "Rental owner")

    @app.put("/v1/rentals/owners/{owner_id}")
    async def update_rental_owner(owner_id: int, body: dict, db: Session = Depends(get_session)):
        body = _normalize_phone_numbers(body)
        return _require(store.update_doc(db, "rental_owners", owner_id, body), "Rental owner")

    # Dynamic property routes must be registered AFTER the static /rentals/units
    # and /rentals/owners routes so "units"/"owners" are not matched as an id.
    @app.get("/v1/rentals/{property_id}")
    async def get_rental(property_id: int, db: Session = Depends(get_session)):
        return _require(store.get_doc(db, "rentals", property_id), "Property")

    @app.put("/v1/rentals/{property_id}")
    async def update_rental(property_id: int, body: dict, db: Session = Depends(get_session)):
        return _require(store.update_doc(db, "rentals", property_id, body), "Property")

    # --------------------------------------------------------------- Leases
    @app.get("/v1/leases")
    async def list_leases(request: Request, db: Session = Depends(get_session)):
        limit, offset = _page(request)
        return store.list_docs(
            db,
            "leases",
            limit=limit,
            offset=offset,
            property_ids=_int_list(request, "propertyids"),
            statuses=_str_list(request, "leasestatuses"),
        )

    @app.post("/v1/leases", status_code=201)
    async def create_lease(body: dict, db: Session = Depends(get_session)):
        return store.create_doc(
            db,
            "leases",
            body,
            property_id=body.get("PropertyId"),
            unit_id=body.get("UnitId"),
            status=body.get("LeaseStatus", "Active"),
        )

    # Static /leases/tenants routes must precede the dynamic /leases/{lease_id}.
    @app.get("/v1/leases/tenants")
    async def list_lease_tenants(request: Request, db: Session = Depends(get_session)):
        limit, offset = _page(request)
        return store.list_docs(
            db,
            "lease_tenants",
            limit=limit,
            offset=offset,
            property_ids=_int_list(request, "propertyids"),
            unit_ids=_int_list(request, "unitids"),
        )

    @app.post("/v1/leases/tenants", status_code=201)
    async def create_lease_tenant(body: dict, db: Session = Depends(get_session)):
        return store.create_doc(db, "lease_tenants", _normalize_phone_numbers(body))

    @app.get("/v1/leases/tenants/{tenant_id}")
    async def get_lease_tenant(tenant_id: int, db: Session = Depends(get_session)):
        return _require(store.get_doc(db, "lease_tenants", tenant_id), "Tenant")

    @app.put("/v1/leases/tenants/{tenant_id}")
    async def update_lease_tenant(tenant_id: int, body: dict, db: Session = Depends(get_session)):
        body = _normalize_phone_numbers(body)
        return _require(store.update_doc(db, "lease_tenants", tenant_id, body), "Tenant")

    @app.get("/v1/leases/{lease_id}")
    async def get_lease(lease_id: int, db: Session = Depends(get_session)):
        return _require(store.get_doc(db, "leases", lease_id), "Lease")

    @app.put("/v1/leases/{lease_id}")
    async def update_lease(lease_id: int, body: dict, db: Session = Depends(get_session)):
        return _require(store.update_doc(db, "leases", lease_id, body), "Lease")

    @app.get("/v1/leases/{lease_id}/transactions")
    async def list_lease_transactions(
        lease_id: int, request: Request, db: Session = Depends(get_session)
    ):
        limit, offset = _page(request)
        return store.list_docs(
            db,
            "lease_transactions",
            limit=limit,
            offset=offset,
            parent_type="lease",
            parent_id=lease_id,
        )

    @app.get("/v1/leases/{lease_id}/transactions/{transaction_id}")
    async def get_lease_transaction(
        lease_id: int, transaction_id: int, db: Session = Depends(get_session)
    ):
        return _require(
            store.get_doc(
                db,
                "lease_transactions",
                transaction_id,
                parent_type="lease",
                parent_id=lease_id,
            ),
            "Lease transaction",
        )

    # ----------------------------------------------------------- Associations
    @app.get("/v1/associations")
    async def list_associations(request: Request, db: Session = Depends(get_session)):
        limit, offset = _page(request)
        return store.list_docs(db, "associations", limit=limit, offset=offset)

    @app.post("/v1/associations", status_code=201)
    async def create_association(body: dict, db: Session = Depends(get_session)):
        return store.create_doc(db, "associations", body, status="Active")

    @app.get("/v1/associations/ownershipaccounts")
    async def list_ownership_accounts(request: Request, db: Session = Depends(get_session)):
        limit, offset = _page(request)
        return store.list_docs(
            db,
            "ownership_accounts",
            limit=limit,
            offset=offset,
            association_ids=_int_list(request, "associationids"),
        )

    @app.get("/v1/associations/owners")
    async def list_association_owners(request: Request, db: Session = Depends(get_session)):
        limit, offset = _page(request)
        return store.list_docs(
            db,
            "association_owners",
            limit=limit,
            offset=offset,
            association_ids=_int_list(request, "associationids"),
        )

    @app.post("/v1/associations/owners", status_code=201)
    async def create_association_owner(body: dict, db: Session = Depends(get_session)):
        return store.create_doc(db, "association_owners", _normalize_phone_numbers(body))

    @app.get("/v1/associations/owners/{owner_id}")
    async def get_association_owner(owner_id: int, db: Session = Depends(get_session)):
        return _require(store.get_doc(db, "association_owners", owner_id), "Association owner")

    @app.put("/v1/associations/owners/{owner_id}")
    async def update_association_owner(
        owner_id: int, body: dict, db: Session = Depends(get_session)
    ):
        body = _normalize_phone_numbers(body)
        return _require(
            store.update_doc(db, "association_owners", owner_id, body), "Association owner"
        )

    @app.get("/v1/associations/tenants")
    async def list_association_tenants(request: Request, db: Session = Depends(get_session)):
        limit, offset = _page(request)
        return store.list_docs(
            db,
            "association_tenants",
            limit=limit,
            offset=offset,
            association_ids=_int_list(request, "associationids"),
        )

    @app.post("/v1/associations/tenants", status_code=201)
    async def create_association_tenant(body: dict, db: Session = Depends(get_session)):
        return store.create_doc(db, "association_tenants", _normalize_phone_numbers(body))

    @app.get("/v1/associations/tenants/{tenant_id}")
    async def get_association_tenant(tenant_id: int, db: Session = Depends(get_session)):
        return _require(store.get_doc(db, "association_tenants", tenant_id), "Association tenant")

    @app.put("/v1/associations/tenants/{tenant_id}")
    async def update_association_tenant(
        tenant_id: int, body: dict, db: Session = Depends(get_session)
    ):
        body = _normalize_phone_numbers(body)
        return _require(
            store.update_doc(db, "association_tenants", tenant_id, body), "Association tenant"
        )

    @app.get("/v1/associations/units")
    async def list_association_units(request: Request, db: Session = Depends(get_session)):
        limit, offset = _page(request)
        return store.list_docs(
            db,
            "association_units",
            limit=limit,
            offset=offset,
            association_ids=_int_list(request, "associationids"),
        )

    @app.post("/v1/associations/units", status_code=201)
    async def create_association_unit(body: dict, db: Session = Depends(get_session)):
        return store.create_doc(
            db, "association_units", body, association_id=body.get("AssociationId")
        )

    @app.put("/v1/associations/units/{unit_id}")
    async def update_association_unit(unit_id: int, body: dict, db: Session = Depends(get_session)):
        return _require(
            store.update_doc(db, "association_units", unit_id, body), "Association unit"
        )

    @app.get("/v1/associations/{association_id}")
    async def get_association(association_id: int, db: Session = Depends(get_session)):
        return _require(store.get_doc(db, "associations", association_id), "Association")

    @app.put("/v1/associations/{association_id}")
    async def update_association(
        association_id: int, body: dict, db: Session = Depends(get_session)
    ):
        return _require(store.update_doc(db, "associations", association_id, body), "Association")

    @app.get("/v1/associations/{association_id}/boardmembers")
    async def list_board_members(
        association_id: int, request: Request, db: Session = Depends(get_session)
    ):
        limit, offset = _page(request)
        return store.list_docs(
            db, "board_members", limit=limit, offset=offset, association_ids=[association_id]
        )

    # ------------------------------------------------------------- Applicants
    @app.get("/v1/applicants")
    async def list_applicants(request: Request, db: Session = Depends(get_session)):
        limit, offset = _page(request)
        return store.list_docs(db, "applicants", limit=limit, offset=offset)

    @app.post("/v1/applicants", status_code=201)
    async def create_applicant(body: dict, db: Session = Depends(get_session)):
        return store.create_doc(db, "applicants", _normalize_phone_numbers(body))

    @app.get("/v1/applicants/groups")
    async def list_applicant_groups(request: Request, db: Session = Depends(get_session)):
        limit, offset = _page(request)
        return store.list_docs(db, "applicant_groups", limit=limit, offset=offset)

    @app.post("/v1/applicants/groups", status_code=201)
    async def create_applicant_group(body: dict, db: Session = Depends(get_session)):
        return store.create_doc(db, "applicant_groups", body)

    @app.put("/v1/applicants/groups/{group_id}")
    async def update_applicant_group(group_id: int, body: dict, db: Session = Depends(get_session)):
        return _require(store.update_doc(db, "applicant_groups", group_id, body), "Applicant group")

    @app.get("/v1/applicants/{applicant_id}")
    async def get_applicant(applicant_id: int, db: Session = Depends(get_session)):
        return _require(store.get_doc(db, "applicants", applicant_id), "Applicant")

    @app.put("/v1/applicants/{applicant_id}")
    async def update_applicant(applicant_id: int, body: dict, db: Session = Depends(get_session)):
        body = _normalize_phone_numbers(body)
        return _require(store.update_doc(db, "applicants", applicant_id, body), "Applicant")

    @app.get("/v1/applicants/{applicant_id}/applications")
    async def list_applications(
        applicant_id: int, request: Request, db: Session = Depends(get_session)
    ):
        limit, offset = _page(request)
        return store.list_docs(
            db,
            "applications",
            limit=limit,
            offset=offset,
            parent_type="applicant",
            parent_id=applicant_id,
        )

    @app.get("/v1/applicants/{applicant_id}/applications/{application_id}")
    async def get_application(
        applicant_id: int, application_id: int, db: Session = Depends(get_session)
    ):
        return _require(
            store.get_doc(
                db,
                "applications",
                application_id,
                parent_type="applicant",
                parent_id=applicant_id,
            ),
            "Application",
        )

    @app.put("/v1/applicants/{applicant_id}/applications/{application_id}")
    async def update_application(
        applicant_id: int, application_id: int, body: dict, db: Session = Depends(get_session)
    ):
        return _require(store.update_doc(db, "applications", application_id, body), "Application")

    # ---------------------------------------------------------------- Vendors
    @app.get("/v1/vendors")
    async def list_vendors(request: Request, db: Session = Depends(get_session)):
        limit, offset = _page(request)
        return store.list_docs(
            db,
            "vendors",
            limit=limit,
            offset=offset,
            statuses=_str_list(request, "status"),
        )

    @app.post("/v1/vendors", status_code=201)
    async def create_vendor(body: dict, db: Session = Depends(get_session)):
        return store.create_doc(db, "vendors", _normalize_phone_numbers(body), status="Active")

    @app.get("/v1/vendors/categories")
    async def list_vendor_categories(request: Request, db: Session = Depends(get_session)):
        limit, offset = _page(request)
        return store.list_docs(db, "vendor_categories", limit=limit, offset=offset)

    @app.post("/v1/vendors/categories", status_code=201)
    async def create_vendor_category(body: dict, db: Session = Depends(get_session)):
        return store.create_doc(db, "vendor_categories", body)

    @app.put("/v1/vendors/categories/{category_id}")
    async def update_vendor_category(
        category_id: int, body: dict, db: Session = Depends(get_session)
    ):
        return _require(
            store.update_doc(db, "vendor_categories", category_id, body), "Vendor category"
        )

    @app.get("/v1/vendors/{vendor_id}")
    async def get_vendor(vendor_id: int, db: Session = Depends(get_session)):
        return _require(store.get_doc(db, "vendors", vendor_id), "Vendor")

    @app.put("/v1/vendors/{vendor_id}")
    async def update_vendor(vendor_id: int, body: dict, db: Session = Depends(get_session)):
        body = _normalize_phone_numbers(body)
        return _require(store.update_doc(db, "vendors", vendor_id, body), "Vendor")

    # ------------------------------------------------------------------ Tasks
    @app.get("/v1/tasks")
    async def list_tasks(request: Request, db: Session = Depends(get_session)):
        limit, offset = _page(request)
        return store.list_docs(
            db,
            "tasks",
            limit=limit,
            offset=offset,
            statuses=_str_list(request, "statuses"),
        )

    @app.get("/v1/tasks/categories")
    async def list_task_categories(request: Request, db: Session = Depends(get_session)):
        limit, offset = _page(request)
        return store.list_docs(db, "task_categories", limit=limit, offset=offset)

    @app.post("/v1/tasks/categories", status_code=201)
    async def create_task_category(body: dict, db: Session = Depends(get_session)):
        return store.create_doc(db, "task_categories", body)

    @app.put("/v1/tasks/categories/{category_id}")
    async def update_task_category(
        category_id: int, body: dict, db: Session = Depends(get_session)
    ):
        return _require(store.update_doc(db, "task_categories", category_id, body), "Task category")

    @app.get("/v1/tasks/{task_id}")
    async def get_task(task_id: int, db: Session = Depends(get_session)):
        return _require(store.get_doc(db, "tasks", task_id), "Task")

    # ------------------------------------------------------------------ Bills
    @app.get("/v1/bills")
    async def list_bills(request: Request, db: Session = Depends(get_session)):
        limit, offset = _page(request)
        vendor = request.query_params.get("vendorid")
        return store.list_docs(
            db,
            "bills",
            limit=limit,
            offset=offset,
            vendor_id=int(vendor) if vendor else None,
            statuses=_str_list(request, "paidstatus"),
        )

    @app.post("/v1/bills", status_code=201)
    async def create_bill(body: dict, db: Session = Depends(get_session)):
        return store.create_doc(db, "bills", body, vendor_id=body.get("VendorId"))

    @app.get("/v1/bills/{bill_id}")
    async def get_bill(bill_id: int, db: Session = Depends(get_session)):
        return _require(store.get_doc(db, "bills", bill_id), "Bill")

    @app.put("/v1/bills/{bill_id}")
    async def update_bill(bill_id: int, body: dict, db: Session = Depends(get_session)):
        return _require(store.update_doc(db, "bills", bill_id, body), "Bill")

    @app.get("/v1/bills/{bill_id}/payments")
    async def list_bill_payments(
        bill_id: int, request: Request, db: Session = Depends(get_session)
    ):
        limit, offset = _page(request)
        return store.list_docs(
            db,
            "bill_payments",
            limit=limit,
            offset=offset,
            parent_type="bill",
            parent_id=bill_id,
        )

    @app.post("/v1/bills/{bill_id}/payments", status_code=201)
    async def create_bill_payment(bill_id: int, body: dict, db: Session = Depends(get_session)):
        return store.create_doc(
            db,
            "bill_payments",
            {**body, "BillId": bill_id},
            parent_type="bill",
            parent_id=bill_id,
        )

    @app.get("/v1/bills/{bill_id}/payments/{payment_id}")
    async def get_bill_payment(bill_id: int, payment_id: int, db: Session = Depends(get_session)):
        return _require(
            store.get_doc(db, "bill_payments", payment_id, parent_type="bill", parent_id=bill_id),
            "Bill payment",
        )

    # ------------------------------------------------------------- Bank accounts
    @app.get("/v1/bankaccounts")
    async def list_bank_accounts(request: Request, db: Session = Depends(get_session)):
        limit, offset = _page(request)
        return store.list_docs(db, "bank_accounts", limit=limit, offset=offset)

    @app.post("/v1/bankaccounts", status_code=201)
    async def create_bank_account(body: dict, db: Session = Depends(get_session)):
        return store.create_doc(db, "bank_accounts", body, status="Active")

    @app.get("/v1/bankaccounts/{bank_account_id}")
    async def get_bank_account(bank_account_id: int, db: Session = Depends(get_session)):
        return _require(store.get_doc(db, "bank_accounts", bank_account_id), "Bank account")

    @app.put("/v1/bankaccounts/{bank_account_id}")
    async def update_bank_account(
        bank_account_id: int, body: dict, db: Session = Depends(get_session)
    ):
        return _require(
            store.update_doc(db, "bank_accounts", bank_account_id, body), "Bank account"
        )

    @app.get("/v1/bankaccounts/{bank_account_id}/transactions")
    async def list_bank_transactions(
        bank_account_id: int, request: Request, db: Session = Depends(get_session)
    ):
        limit, offset = _page(request)
        return store.list_docs(
            db,
            "bank_transactions",
            limit=limit,
            offset=offset,
            parent_type="bank_account",
            parent_id=bank_account_id,
        )

    @app.get("/v1/bankaccounts/{bank_account_id}/transactions/{transaction_id}")
    async def get_bank_transaction(
        bank_account_id: int, transaction_id: int, db: Session = Depends(get_session)
    ):
        return _require(
            store.get_doc(
                db,
                "bank_transactions",
                transaction_id,
                parent_type="bank_account",
                parent_id=bank_account_id,
            ),
            "Bank transaction",
        )

    # ------------------------------------------------------------------ Files
    @app.get("/v1/files")
    async def list_files(request: Request, db: Session = Depends(get_session)):
        limit, offset = _page(request)
        return store.list_docs(db, "files", limit=limit, offset=offset)

    @app.get("/v1/files/categories")
    async def list_file_categories(request: Request, db: Session = Depends(get_session)):
        limit, offset = _page(request)
        return store.list_docs(db, "file_categories", limit=limit, offset=offset)

    @app.post("/v1/files/categories", status_code=201)
    async def create_file_category(body: dict, db: Session = Depends(get_session)):
        return store.create_doc(db, "file_categories", body)

    @app.put("/v1/files/categories/{category_id}")
    async def update_file_category(
        category_id: int, body: dict, db: Session = Depends(get_session)
    ):
        return _require(store.update_doc(db, "file_categories", category_id, body), "File category")

    @app.post("/v1/files/uploads", status_code=201)
    async def create_file_upload(body: dict, db: Session = Depends(get_session)):
        # Buildium returns an upload target; echo a plausible presigned form.
        return {
            "BucketUrl": "https://mock-uploads.example.com/upload",
            "FormData": {"key": f"files/{body.get('FileName', 'upload.bin')}"},
            "PhysicalFileName": body.get("FileName", "upload.bin"),
        }

    @app.get("/v1/files/{file_id}")
    async def get_file(file_id: int, db: Session = Depends(get_session)):
        return _require(store.get_doc(db, "files", file_id), "File")

    @app.put("/v1/files/{file_id}")
    async def update_file(file_id: int, body: dict, db: Session = Depends(get_session)):
        return _require(store.update_doc(db, "files", file_id, body), "File")

    @app.post("/v1/files/{file_id}/downloadrequest", status_code=201)
    async def create_file_download(file_id: int, db: Session = Depends(get_session)):
        _require(store.get_doc(db, "files", file_id), "File")
        return {"DownloadUrl": f"https://mock-downloads.example.com/files/{file_id}"}

    # ---------------------------------------------------------- General ledger
    @app.get("/v1/glaccounts")
    async def list_gl_accounts(request: Request, db: Session = Depends(get_session)):
        limit, offset = _page(request)
        return store.list_docs(
            db,
            "gl_accounts",
            limit=limit,
            offset=offset,
            statuses=_str_list(request, "accounttypes"),
        )

    @app.get("/v1/glaccounts/{gl_account_id}")
    async def get_gl_account(gl_account_id: int, db: Session = Depends(get_session)):
        return _require(store.get_doc(db, "gl_accounts", gl_account_id), "GL account")

    @app.get("/v1/generalledger/transactions")
    async def list_gl_transactions(request: Request, db: Session = Depends(get_session)):
        limit, offset = _page(request)
        return store.list_docs(db, "gl_transactions", limit=limit, offset=offset)

    @app.get("/v1/generalledger/transactions/{transaction_id}")
    async def get_gl_transaction(transaction_id: int, db: Session = Depends(get_session)):
        return _require(store.get_doc(db, "gl_transactions", transaction_id), "GL transaction")

    # -------------------------------------------------------------- Work orders
    @app.get("/v1/workorders")
    async def list_work_orders(request: Request, db: Session = Depends(get_session)):
        limit, offset = _page(request)
        return store.list_docs(
            db,
            "work_orders",
            limit=limit,
            offset=offset,
            statuses=_str_list(request, "statuses"),
        )

    @app.post("/v1/workorders", status_code=201)
    async def create_work_order(body: dict, db: Session = Depends(get_session)):
        return store.create_doc(db, "work_orders", body, status=body.get("WorkOrderStatus", "New"))

    @app.get("/v1/workorders/{work_order_id}")
    async def get_work_order(work_order_id: int, db: Session = Depends(get_session)):
        return _require(store.get_doc(db, "work_orders", work_order_id), "Work order")

    @app.put("/v1/workorders/{work_order_id}")
    async def update_work_order(work_order_id: int, body: dict, db: Session = Depends(get_session)):
        return _require(store.update_doc(db, "work_orders", work_order_id, body), "Work order")

    return app
