"""Reference-data tools and MCP resources for Buildium.

Exposes the controlled vocabularies Buildium expects for common enum fields
(lease/task statuses, property types, task priorities, ...) so the assistant can
ground field values *without* a round-trip and avoid ``validation_error``
responses. The same data is published both as an MCP **resource** (for clients
that browse resources) and as a local **tool** (so any MCP/LLM client can fetch
it on demand).
"""

from typing import Any

from fastmcp import FastMCP

from ..buildium_client import BuildiumClient
from . import _common as c

#: Controlled vocabularies for common Buildium enum fields. Keep in sync with the
#: enum sets validated in the individual tool modules.
REFERENCE_DATA: dict[str, dict[str, Any]] = {
    "lease_statuses": {
        "description": "Valid lease status values (used by list_leases, "
        "list_lease_outstanding_balances).",
        "values": ["Active", "Past", "Future"],
    },
    "task_statuses": {
        "description": "Valid task status values (used by list_tasks).",
        "values": ["New", "InProgress", "Completed", "Deferred", "Closed"],
    },
    "task_priorities": {
        "description": "Valid task priority values.",
        "values": ["Low", "Normal", "High"],
    },
    "property_types": {
        "description": "Rental property types.",
        "values": ["Rental", "Association", "Commercial"],
    },
    "work_order_statuses": {
        "description": "Valid work order status values (used by list_work_orders).",
        "values": ["New", "InProgress", "Completed", "Deferred", "Closed"],
    },
    "bill_approval_statuses": {
        "description": "Valid bill approval status values.",
        "values": ["NotApplicable", "Pending", "Approved", "Rejected"],
    },
}


def register_reference_tools(mcp: FastMCP, client: BuildiumClient) -> None:
    """Register reference-data resources and the reference lookup tool.

    ``client`` is accepted for a uniform registrar signature but is unused: the
    reference vocabularies are static and require no Buildium API call.
    """

    # Publish each vocabulary as an individually addressable MCP resource so
    # resource-aware clients can browse them.
    for topic, payload in REFERENCE_DATA.items():
        uri = f"buildium://reference/{topic.replace('_', '-')}"

        def _make(payload: dict[str, Any] = payload):
            def _resource() -> dict[str, Any]:
                return payload

            return _resource

        mcp.resource(uri, name=f"reference:{topic}", description=payload["description"])(_make())

    c.register_local_tool("get_reference_data", op_type="read", sensitive=False)

    @mcp.tool()
    async def get_reference_data(topic: str | None = None) -> dict[str, Any]:
        """Return Buildium reference vocabularies for enum fields.

        Args:
            topic: Optional single topic to return (e.g. ``lease_statuses``,
                ``task_statuses``, ``property_types``). When omitted, all
                available topics are returned. Unknown topics yield a
                ``validation_error`` listing the valid topics.
        """
        if topic is None:
            return c.success(REFERENCE_DATA)
        key = topic.strip().lower()
        if key not in REFERENCE_DATA:
            return c.failure(
                f"Unknown reference topic: {topic!r}.",
                code="validation_error",
                hint=f"Valid topics: {sorted(REFERENCE_DATA)}.",
            )
        return c.success(REFERENCE_DATA[key])
