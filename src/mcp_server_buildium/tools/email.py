"""Email dispatch tools backed by AWS Simple Email Service (SES).

Credentials (AWS key / secret) stay entirely on the server — the browser
extension never sees them.  The extension user simply asks the LLM assistant to
send an email, the agent calls one of the tools below, and the server dispatches
via SES.

Configuration (all via env vars / admin UI):

    BUILDIUM_AWS_SES_SENDER=noreply@yourdomain.com   # required to enable
    BUILDIUM_AWS_REGION=us-east-1                     # default
    BUILDIUM_AWS_ACCESS_KEY_ID=AKIA...               # optional (IAM role fallback)
    BUILDIUM_AWS_SECRET_ACCESS_KEY=...               # optional
    BUILDIUM_AWS_SES_ENDPOINT_URL=http://localhost:4566  # optional (LocalStack)

Tools:

* ``send_email``             — send to any address(es).
* ``send_email_to_tenant``   — look up a rental tenant by ID and send to their email.
* ``send_email_to_owner``    — look up a rental owner by ID and send to their email.
"""

from __future__ import annotations

import asyncio
from typing import Any

from fastmcp import FastMCP

from ..buildium_client import BuildiumClient
from ..config import BuildiumConfig
from . import _common as c


def _build_ses_client(config: BuildiumConfig) -> Any:
    """Create a boto3 SES client from the server config."""
    import boto3  # imported lazily so the module loads without boto3 for testing

    kwargs: dict[str, Any] = {"region_name": config.aws_region}
    if config.aws_access_key_id:
        kwargs["aws_access_key_id"] = config.aws_access_key_id
    if config.aws_secret_access_key:
        kwargs["aws_secret_access_key"] = config.aws_secret_access_key
    if config.aws_ses_endpoint_url:
        kwargs["endpoint_url"] = config.aws_ses_endpoint_url
    return boto3.client("ses", **kwargs)


def _extract_email(record: Any, contact_type: str) -> str | None:
    """Pull the primary email address out of a Buildium contact record dict."""
    if not isinstance(record, dict):
        return None
    # The Buildium API returns email under the key "Email" (a plain string).
    email = record.get("Email")
    if email and isinstance(email, str):
        return email.strip() or None
    return None


def _ses_not_configured_error() -> dict[str, Any]:
    return c.failure(
        "AWS SES is not configured. Set BUILDIUM_AWS_SES_SENDER to enable email dispatch.",
        code="not_configured",
        hint="Add BUILDIUM_AWS_SES_SENDER=noreply@yourdomain.com to your server config.",
    )


def register_email_tools(mcp: FastMCP, client: BuildiumClient) -> None:
    """Register email dispatch tools with the MCP server."""

    config: BuildiumConfig = client.config  # type: ignore[attr-defined]

    c.register_local_tool("send_email", op_type="write", sensitive=True)
    c.register_local_tool("send_email_to_tenant", op_type="write", sensitive=True)
    c.register_local_tool("send_email_to_owner", op_type="write", sensitive=True)

    @mcp.tool()
    async def send_email(
        to: list[str],
        subject: str,
        body: str,
        html_body: str | None = None,
        cc: list[str] | None = None,
        bcc: list[str] | None = None,
    ) -> dict[str, Any]:
        """Send an email via AWS SES.

        Args:
            to: List of recipient email addresses.
            subject: Email subject line.
            body: Plain-text email body.
            html_body: Optional HTML version of the body (sent alongside plain text).
            cc: Optional list of CC addresses.
            bcc: Optional list of BCC addresses.

        Returns:
            Success envelope containing the SES MessageId on success, or a
            failure envelope describing the error.
        """
        if not config.ses_enabled():
            return _ses_not_configured_error()

        if not to:
            return c.failure("At least one recipient address is required.", code="validation_error")
        if not subject or not subject.strip():
            return c.failure("subject is required.", code="validation_error")
        if not body or not body.strip():
            return c.failure("body is required.", code="validation_error")

        def _send() -> dict[str, Any]:
            ses = _build_ses_client(config)
            body_parts: dict[str, Any] = {"Text": {"Data": body, "Charset": "UTF-8"}}
            if html_body:
                body_parts["Html"] = {"Data": html_body, "Charset": "UTF-8"}

            destination: dict[str, Any] = {"ToAddresses": to}
            if cc:
                destination["CcAddresses"] = cc
            if bcc:
                destination["BccAddresses"] = bcc

            response = ses.send_email(
                Source=config.aws_ses_sender,
                Destination=destination,
                Message={
                    "Subject": {"Data": subject, "Charset": "UTF-8"},
                    "Body": body_parts,
                },
            )
            return {"message_id": response.get("MessageId"), "to": to}

        async def _do_send() -> Any:
            return await asyncio.to_thread(_send)

        return await c.execute("send_email", _do_send)

    @mcp.tool()
    async def send_email_to_tenant(
        tenant_id: int,
        subject: str,
        body: str,
        html_body: str | None = None,
    ) -> dict[str, Any]:
        """Look up a rental tenant by ID and send them an email via AWS SES.

        Args:
            tenant_id: Buildium rental tenant ID.
            subject: Email subject line.
            body: Plain-text email body.
            html_body: Optional HTML version of the body.

        Returns:
            Success envelope containing the SES MessageId and recipient address,
            or a failure envelope if the tenant has no email or SES is not configured.
        """
        if not config.ses_enabled():
            return _ses_not_configured_error()

        async def _do_send() -> Any:
            record = await client.rental_tenants_api.external_api_rental_tenants_get_tenant_by_id(
                tenant_id=tenant_id
            )
            if isinstance(record, list):
                record = record[0] if record else {}
            if hasattr(record, "to_dict"):
                record = record.to_dict()
            email = _extract_email(record, "tenant")
            if not email:
                raise ValueError(f"Rental tenant {tenant_id} has no email address on file.")

            def _send() -> dict[str, Any]:
                ses = _build_ses_client(config)
                body_parts: dict[str, Any] = {"Text": {"Data": body, "Charset": "UTF-8"}}
                if html_body:
                    body_parts["Html"] = {"Data": html_body, "Charset": "UTF-8"}
                response = ses.send_email(
                    Source=config.aws_ses_sender,
                    Destination={"ToAddresses": [email]},
                    Message={
                        "Subject": {"Data": subject, "Charset": "UTF-8"},
                        "Body": body_parts,
                    },
                )
                return {"message_id": response.get("MessageId"), "to": [email], "tenant_id": tenant_id}

            return await asyncio.to_thread(_send)

        return await c.execute("send_email_to_tenant", _do_send)

    @mcp.tool()
    async def send_email_to_owner(
        owner_id: int,
        subject: str,
        body: str,
        html_body: str | None = None,
    ) -> dict[str, Any]:
        """Look up a rental owner by ID and send them an email via AWS SES.

        Args:
            owner_id: Buildium rental owner ID.
            subject: Email subject line.
            body: Plain-text email body.
            html_body: Optional HTML version of the body.

        Returns:
            Success envelope containing the SES MessageId and recipient address,
            or a failure envelope if the owner has no email or SES is not configured.
        """
        if not config.ses_enabled():
            return _ses_not_configured_error()

        async def _do_send() -> Any:
            record = await client.rental_owners_api.external_api_rental_owners_get_rental_owner_by_id(
                owner_id=owner_id
            )
            if isinstance(record, list):
                record = record[0] if record else {}
            if hasattr(record, "to_dict"):
                record = record.to_dict()
            email = _extract_email(record, "owner")
            if not email:
                raise ValueError(f"Rental owner {owner_id} has no email address on file.")

            def _send() -> dict[str, Any]:
                ses = _build_ses_client(config)
                body_parts: dict[str, Any] = {"Text": {"Data": body, "Charset": "UTF-8"}}
                if html_body:
                    body_parts["Html"] = {"Data": html_body, "Charset": "UTF-8"}
                response = ses.send_email(
                    Source=config.aws_ses_sender,
                    Destination={"ToAddresses": [email]},
                    Message={
                        "Subject": {"Data": subject, "Charset": "UTF-8"},
                        "Body": body_parts,
                    },
                )
                return {"message_id": response.get("MessageId"), "to": [email], "owner_id": owner_id}

            return await asyncio.to_thread(_send)

        return await c.execute("send_email_to_owner", _do_send)
