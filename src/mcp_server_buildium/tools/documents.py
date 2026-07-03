"""Document-intake helper tools for the assistant.

These tools support the "upload a document, extract fields, create the object"
workflow:

* :func:`describe_create_schema` returns the field checklist for a creatable
  Buildium object (required/optional fields, types, descriptions) derived from
  the generated SDK POST models. The assistant uses it to know exactly which
  fields to pull out of an uploaded document and to verify completeness before
  creating.
* :func:`save_uploaded_document` saves a document the user attached to the chat
  turn to Buildium and links it to an entity (e.g. the newly created lease),
  using the standard two-step Buildium upload flow (request an upload ticket,
  then POST the bytes to the returned storage URL).

No new object-creation logic lives here: extraction feeds the existing
``create_*`` tools, which already validate input and return friendly
``validation_error`` envelopes listing anything missing.
"""

from __future__ import annotations

from typing import Any, get_args, get_origin

from fastmcp import FastMCP

from ..buildium_client import BuildiumClient
from ..llm.attachments import get_current_attachment, list_current_attachment_names
from . import _common as c

# Map a friendly object type -> the SDK POST model backing its ``create_*`` tool
# and the ``EntityType`` used when attaching a saved file to that object. Only
# object types that make sense as a "create from a document" target are listed.
#
#   key: (post_model_module, PostModelClass, create_tool_name, file_entity_type)
_CREATE_SCHEMAS: dict[str, tuple[str, str, str, str | None]] = {
    "lease": ("lease_post_message", "LeasePostMessage", "create_lease", "Lease"),
    "rental_property": (
        "rental_property_post_message",
        "RentalPropertyPostMessage",
        "create_rental",
        "Rental",
    ),
    "rental_unit": (
        "rental_unit_post_message",
        "RentalUnitPostMessage",
        "create_rental_unit",
        "RentalUnit",
    ),
    "rental_tenant": (
        "rental_tenant_post_message",
        "RentalTenantPostMessage",
        "create_rental_tenant",
        "Tenant",
    ),
    "rental_owner": (
        "rental_owner_post_message",
        "RentalOwnerPostMessage",
        "create_rental_owner",
        "RentalOwner",
    ),
    "association": (
        "association_post_message",
        "AssociationPostMessage",
        "create_association",
        "Association",
    ),
    "association_unit": (
        "association_unit_post_message",
        "AssociationUnitPostMessage",
        "create_association_unit",
        "AssociationUnit",
    ),
    "association_tenant": (
        "association_tenant_post_message",
        "AssociationTenantPostMessage",
        "create_association_tenant",
        "Tenant",
    ),
    "vendor": ("vendor_post_message", "VendorPostMessage", "create_vendor", "Vendor"),
    "bill": ("bill_post_message", "BillPostMessage", "create_bill", None),
    "applicant": (
        "applicant_post_message",
        "ApplicantPostMessage",
        "create_applicant",
        None,
    ),
    "work_order": (
        "work_order_post_message",
        "WorkOrderPostMessage",
        "create_work_order",
        None,
    ),
    "bank_account": (
        "bank_account_post_message",
        "BankAccountPostMessage",
        "create_bank_account",
        "Account",
    ),
    "task_category": (
        "task_category_post_message",
        "TaskCategoryPostMessage",
        "create_task_category",
        None,
    ),
}

# Entity types the Buildium file-upload API accepts (from FileUploadPostMessage).
_FILE_ENTITY_TYPES = frozenset(
    {
        "Account",
        "Association",
        "AssociationOwner",
        "AssociationUnit",
        "Lease",
        "OwnershipAccount",
        "PublicAsset",
        "Rental",
        "RentalOwner",
        "RentalUnit",
        "Tenant",
        "Vendor",
    }
)


def _type_label(annotation: Any) -> str:
    """Return a concise, human-readable label for a pydantic field annotation."""
    origin = get_origin(annotation)
    if origin is None:
        name = getattr(annotation, "__name__", None)
        return name or str(annotation)
    args = [a for a in get_args(annotation) if a is not type(None)]
    if not args:
        return "any"
    # Optional[X] / Union collapses to the first non-None member for readability.
    return " | ".join(_type_label(a) for a in args)


def _describe_model_fields(model_cls: Any) -> list[dict[str, Any]]:
    """Describe a pydantic model's fields for the extraction checklist.

    Reports the JSON alias (the key the ``create_*`` tools expect), whether the
    field is required, a readable type label, and the field description.
    """
    fields: list[dict[str, Any]] = []
    for field_name, info in model_cls.model_fields.items():
        alias = info.alias or field_name
        fields.append(
            {
                "name": alias,
                "required": info.is_required(),
                "type": _type_label(info.annotation),
                "description": info.description or "",
            }
        )
    # Required fields first so the model prioritises them when extracting.
    fields.sort(key=lambda f: (not f["required"], f["name"]))
    return fields


def register_document_tools(mcp: FastMCP, client: BuildiumClient) -> None:
    """Register document-intake helper tools with the MCP server."""

    c.register_local_tool("describe_create_schema", op_type="read", sensitive=False)
    c.register_local_tool("list_uploaded_documents", op_type="read", sensitive=False)
    # Saving an uploaded document to Buildium mutates data and issues a file
    # upload, so it is classified as a sensitive write.
    c.register_local_tool("save_uploaded_document", op_type="write", sensitive=True)

    @mcp.tool()
    async def describe_create_schema(object_type: str) -> dict[str, Any]:
        """Describe the fields needed to create a Buildium object.

        Returns the required and optional fields (with their JSON names, types,
        and descriptions) for a creatable object, so you can map an uploaded
        document's contents onto the matching ``create_*`` tool and verify you
        have everything before creating. Pass ``object_type='list'`` to see the
        supported object types.

        Args:
            object_type: One of the supported object types (e.g. ``lease``,
                ``rental_tenant``, ``rental_owner``, ``rental_property``,
                ``rental_unit``, ``vendor``). Use ``list`` to enumerate them.
        """
        key = (object_type or "").strip().lower()
        if key in ("", "list", "all"):
            return c.success(
                {"supported_object_types": sorted(_CREATE_SCHEMAS)},
                meta={"hint": "Call describe_create_schema with one of these types."},
            )
        entry = _CREATE_SCHEMAS.get(key)
        if entry is None:
            return c.failure(
                f"Unknown object type {object_type!r}. "
                f"Supported: {', '.join(sorted(_CREATE_SCHEMAS))}.",
                code="validation_error",
                hint="Call describe_create_schema('list') to see supported types.",
            )
        module, class_name, create_tool, _entity = entry
        try:
            mod = __import__(
                f"mcp_server_buildium.buildium_sdk.models.{module}", fromlist=[class_name]
            )
            model_cls = getattr(mod, class_name)
        except (ImportError, AttributeError):
            return c.failure(
                f"Schema for {object_type!r} is unavailable in this build.",
                code="internal_error",
            )
        fields = _describe_model_fields(model_cls)
        return c.success(
            {
                "object_type": key,
                "create_tool": create_tool,
                "fields": fields,
                "required_fields": [f["name"] for f in fields if f["required"]],
            }
        )

    @mcp.tool()
    async def list_uploaded_documents() -> dict[str, Any]:
        """List documents the user attached to the current message.

        Returns the file names available to :func:`save_uploaded_document`. Use
        this to confirm which uploaded document to save before saving it.
        """
        return c.success({"documents": list_current_attachment_names()})

    @mcp.tool()
    async def save_uploaded_document(
        file_name: str,
        entity_type: str,
        entity_id: int,
        category_id: int,
        title: str | None = None,
        description: str | None = None,
    ) -> dict[str, Any]:
        """Save a document the user uploaded to Buildium and link it to an entity.

        Uses the document the user attached to the current chat message (match by
        ``file_name`` — see :func:`list_uploaded_documents`) and stores it against
        the given entity (for example the newly created lease). Only call this
        after the target entity exists so ``entity_id`` is known.

        Args:
            file_name: Name of the uploaded document to save.
            entity_type: Buildium entity type to attach the file to (e.g.
                ``Lease``, ``Rental``, ``Tenant``, ``RentalOwner``, ``Vendor``).
            entity_id: Id of the entity the file belongs to.
            category_id: Buildium file category id (see ``list_file_categories``).
            title: Optional title for the file (defaults to the file name).
            description: Optional description for the file.
        """
        attachment = get_current_attachment(file_name)
        if attachment is None:
            available = list_current_attachment_names()
            return c.failure(
                f"No uploaded document named {file_name!r} is attached to this message.",
                code="validation_error",
                hint=(
                    f"Available documents: {', '.join(available)}."
                    if available
                    else "Ask the user to attach the document to their message."
                ),
            )
        if entity_type not in _FILE_ENTITY_TYPES:
            return c.failure(
                f"Unsupported entity_type {entity_type!r}. "
                f"Allowed: {', '.join(sorted(_FILE_ENTITY_TYPES))}.",
                code="validation_error",
            )

        upload_request = {
            "EntityType": entity_type,
            "EntityId": entity_id,
            "FileName": attachment.name,
            "Title": title or attachment.name,
            "CategoryId": category_id,
        }
        if description:
            upload_request["Description"] = description

        async def _run() -> Any:
            message = c.build_model(
                "file_upload_post_message",
                "FileUploadPostMessage",
                upload_request,
                resource="save_uploaded_document",
            )
            ticket = await client.files_api.external_api_files_uploads_create_upload_file_request_async(
                file_upload_post_message=message
            )
            bucket_url = getattr(ticket, "bucket_url", None)
            form_data = getattr(ticket, "form_data", None) or {}
            if not bucket_url:
                raise ValueError(
                    "Buildium did not return an upload URL for this file."
                )
            await _post_file_to_storage(bucket_url, form_data, attachment)
            return {
                "saved": True,
                "file_name": attachment.name,
                "entity_type": entity_type,
                "entity_id": entity_id,
                "category_id": category_id,
            }

        return await c.execute("save_uploaded_document", _run)


async def _post_file_to_storage(bucket_url: str, form_data: dict[str, Any], attachment: Any) -> None:
    """POST the file bytes to Buildium's returned storage URL (S3 presigned POST).

    The upload ticket returns a bucket URL plus form fields that must be sent as
    a multipart form together with the file, which is the final part.
    """
    import httpx

    fields: list[tuple[str, tuple[Any, Any, Any]]] = [
        (key, (None, value, None)) for key, value in form_data.items() if value is not None
    ]
    fields.append(
        ("file", (attachment.name, attachment.data, attachment.media_type))
    )
    async with httpx.AsyncClient(timeout=60.0) as http:
        resp = await http.post(bucket_url, files=fields)
    if resp.status_code >= 400:
        raise ValueError(
            f"Uploading the file to storage failed ({resp.status_code})."
        )
