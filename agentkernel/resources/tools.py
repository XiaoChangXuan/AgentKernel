"""Model-facing Resource tools backed only by ResourceService."""

from __future__ import annotations

from collections.abc import Mapping

from ..protocol import ErrorCode, JsonValue, ToolSchema
from ..tools import ToolDefinition, ToolExecutionContext, ToolExecutionError
from .model import ResourceOwner
from .service import (
    ResourceAccessDenied,
    ResourceError,
    ResourceInvalid,
    ResourceService,
    ResourceUnknown,
)


def resource_tool_definitions(
    resources: ResourceService,
    *,
    required_capability: str | None = "resource.read",
) -> tuple[ToolDefinition, ToolDefinition]:
    """Create stat/read definitions without exposing the ResourceStore driver."""

    async def stat(
        arguments: Mapping[str, JsonValue], context: ToolExecutionContext
    ) -> JsonValue:
        uri = arguments.get("uri")
        if not isinstance(uri, str):
            raise ToolExecutionError(ErrorCode.EINVAL, "uri must be a string")
        try:
            return resources.stat(
                uri,
                owner=_owner(context),
                capability_evaluator=context.capability_evaluator,
            ).as_dict()
        except ResourceError as error:
            raise _tool_error(error) from error

    async def read(
        arguments: Mapping[str, JsonValue], context: ToolExecutionContext
    ) -> JsonValue:
        uri = arguments.get("uri")
        offset = arguments.get("offset", 0)
        limit = arguments.get("limit")
        if not isinstance(uri, str):
            raise ToolExecutionError(ErrorCode.EINVAL, "uri must be a string")
        if isinstance(offset, bool) or not isinstance(offset, int):
            raise ToolExecutionError(ErrorCode.EINVAL, "offset must be an integer")
        if limit is not None and (isinstance(limit, bool) or not isinstance(limit, int)):
            raise ToolExecutionError(ErrorCode.EINVAL, "limit must be an integer")
        try:
            result = resources.read(
                uri,
                owner=_owner(context),
                offset=offset,
                limit=limit,
                capability_evaluator=context.capability_evaluator,
            )
        except ResourceError as error:
            raise _tool_error(error) from error
        return {
            "uri": result.handle.uri,
            "offset": result.offset,
            "next_offset": result.next_offset,
            "returned_bytes": len(result.data),
            "total_bytes": result.handle.size_bytes,
            "has_more": result.has_more,
            "content": result.data.decode("utf-8", errors="replace"),
        }

    common_properties: dict[str, JsonValue] = {
        "uri": {
            "type": "string",
            "description": "Opaque artifact:// resource handle.",
        }
    }
    return (
        ToolDefinition(
            schema=ToolSchema(
                "resource_stat",
                "Return safe metadata for an owned artifact handle.",
                {
                    "type": "object",
                    "properties": common_properties,
                    "required": ["uri"],
                    "additionalProperties": False,
                },
            ),
            handler=stat,
            required_capability=required_capability,
        ),
        ToolDefinition(
            schema=ToolSchema(
                "resource_read",
                "Read a bounded byte range from an owned artifact handle.",
                {
                    "type": "object",
                    "properties": {
                        **common_properties,
                        "offset": {"type": "integer", "minimum": 0},
                        "limit": {"type": "integer", "minimum": 1},
                    },
                    "required": ["uri"],
                    "additionalProperties": False,
                },
            ),
            handler=read,
            required_capability=required_capability,
        ),
    )


def _owner(context: ToolExecutionContext) -> ResourceOwner:
    return ResourceOwner(context.agent_id, context.session_id)


def _tool_error(error: ResourceError) -> ToolExecutionError:
    if isinstance(error, ResourceInvalid):
        return ToolExecutionError(ErrorCode.EINVAL, str(error))
    if isinstance(error, ResourceAccessDenied):
        return ToolExecutionError(ErrorCode.EACCES, str(error))
    if isinstance(error, ResourceUnknown):
        return ToolExecutionError(ErrorCode.ENOENT, str(error))
    return ToolExecutionError(ErrorCode.EIO, str(error))
