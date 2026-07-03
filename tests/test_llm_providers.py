"""Pure-mapping tests for the LLM provider adapters (no network).

Verifies that MCP tools and neutral messages are shaped correctly for each
provider and that provider responses are parsed back into the neutral
:class:`Completion` form.
"""

from __future__ import annotations

from mcp_server_buildium.llm.base import ToolCall
from mcp_server_buildium.llm.providers import (
    anthropic_messages,
    anthropic_tools,
    gemini_contents,
    gemini_tools,
    openai_messages,
    openai_tools,
    parse_anthropic_response,
    parse_gemini_response,
    parse_openai_response,
)

TOOLS = [{"name": "list_leases", "description": "List leases", "inputSchema": {"type": "object"}}]

CONVERSATION = [
    {"role": "system", "content": "sys"},
    {"role": "user", "content": "hi"},
    {
        "role": "assistant",
        "content": None,
        "tool_calls": [ToolCall("c1", "list_leases", {"a": 1})],
    },
    {"role": "tool", "tool_call_id": "c1", "name": "list_leases", "content": '{"count":2}'},
]


# --- OpenAI ---------------------------------------------------------------


def test_openai_tools_shape() -> None:
    assert openai_tools(TOOLS) == [
        {
            "type": "function",
            "function": {
                "name": "list_leases",
                "description": "List leases",
                "parameters": {"type": "object"},
            },
        }
    ]


def test_openai_tools_default_schema() -> None:
    assert openai_tools([{"name": "x"}])[0]["function"]["parameters"] == {
        "type": "object",
        "properties": {},
    }


def test_openai_messages_serializes_tool_calls_and_results() -> None:
    out = openai_messages(CONVERSATION)
    assert out[2]["tool_calls"][0]["function"] == {"name": "list_leases", "arguments": '{"a": 1}'}
    assert out[3] == {"role": "tool", "tool_call_id": "c1", "content": '{"count":2}'}


def test_parse_openai_response() -> None:
    completion = parse_openai_response(
        {
            "choices": [
                {
                    "message": {
                        "content": "hey",
                        "tool_calls": [
                            {"id": "x", "function": {"name": "f", "arguments": '{"a":1}'}}
                        ],
                    }
                }
            ]
        }
    )
    assert completion.content == "hey"
    assert completion.tool_calls == [ToolCall(id="x", name="f", arguments={"a": 1})]


def test_parse_openai_response_tolerates_empty_arguments() -> None:
    completion = parse_openai_response(
        {
            "choices": [
                {
                    "message": {
                        "content": "",
                        "tool_calls": [{"id": "x", "function": {"name": "f", "arguments": ""}}],
                    }
                }
            ]
        }
    )
    assert completion.tool_calls[0].arguments == {}


# --- Anthropic ------------------------------------------------------------


def test_anthropic_tools_shape() -> None:
    assert anthropic_tools(TOOLS) == [
        {
            "name": "list_leases",
            "description": "List leases",
            "input_schema": {"type": "object"},
        }
    ]


def test_anthropic_messages_split_system_and_blocks() -> None:
    system, msgs = anthropic_messages(CONVERSATION)
    assert system == "sys"
    assert msgs[0] == {"role": "user", "content": [{"type": "text", "text": "hi"}]}
    assert msgs[1]["content"][0] == {
        "type": "tool_use",
        "id": "c1",
        "name": "list_leases",
        "input": {"a": 1},
    }
    assert msgs[2]["content"][0]["type"] == "tool_result"


def test_anthropic_merges_consecutive_tool_results() -> None:
    convo = [
        {
            "role": "assistant",
            "content": None,
            "tool_calls": [ToolCall("c1", "a", {}), ToolCall("c2", "b", {})],
        },
        {"role": "tool", "tool_call_id": "c1", "name": "a", "content": "r1"},
        {"role": "tool", "tool_call_id": "c2", "name": "b", "content": "r2"},
    ]
    _system, msgs = anthropic_messages(convo)
    # Both tool results collapse into a single user turn.
    assert msgs[-1]["role"] == "user"
    assert len(msgs[-1]["content"]) == 2


def test_parse_anthropic_response() -> None:
    completion = parse_anthropic_response(
        {
            "content": [
                {"type": "text", "text": "hi"},
                {"type": "tool_use", "id": "t", "name": "f", "input": {"a": 1}},
            ]
        }
    )
    assert completion.content == "hi"
    assert completion.tool_calls == [ToolCall(id="t", name="f", arguments={"a": 1})]


# --- Gemini ---------------------------------------------------------------


def test_gemini_tools_shape() -> None:
    assert gemini_tools(TOOLS) == [
        {
            "function_declarations": [
                {
                    "name": "list_leases",
                    "description": "List leases",
                    "parameters": {"type": "object"},
                }
            ]
        }
    ]
    assert gemini_tools([]) == []


def test_gemini_tools_strips_unsupported_schema_keys() -> None:
    tools = [
        {
            "name": "make_thing",
            "description": "Make a thing",
            "inputSchema": {
                "type": "object",
                "additionalProperties": False,
                "$schema": "https://json-schema.org/draft/2020-12/schema",
                "properties": {
                    "opts": {
                        "type": "object",
                        "additionalProperties": {"type": "string"},
                        "properties": {"name": {"type": "string"}},
                    }
                },
            },
        }
    ]
    params = gemini_tools(tools)[0]["function_declarations"][0]["parameters"]
    assert params == {
        "type": "object",
        "properties": {
            "opts": {
                "type": "object",
                "properties": {"name": {"type": "string"}},
            }
        },
    }


def test_gemini_contents_split_system_and_parts() -> None:
    system, contents = gemini_contents(CONVERSATION)
    assert system == {"parts": [{"text": "sys"}]}
    assert contents[0] == {"role": "user", "parts": [{"text": "hi"}]}
    assert contents[1]["role"] == "model"
    assert contents[1]["parts"][0]["functionCall"] == {"name": "list_leases", "args": {"a": 1}}
    assert contents[2]["parts"][0]["functionResponse"]["name"] == "list_leases"


def test_parse_gemini_response() -> None:
    completion = parse_gemini_response(
        {
            "candidates": [
                {
                    "content": {
                        "parts": [{"text": "hi"}, {"functionCall": {"name": "f", "args": {"a": 1}}}]
                    }
                }
            ]
        }
    )
    assert completion.content == "hi"
    assert completion.tool_calls[0].name == "f"
    assert completion.tool_calls[0].arguments == {"a": 1}


def test_parse_gemini_response_captures_thought_signature() -> None:
    completion = parse_gemini_response(
        {
            "candidates": [
                {
                    "content": {
                        "parts": [
                            {
                                "functionCall": {"name": "f", "args": {"a": 1}},
                                "thoughtSignature": "sig-abc",
                            }
                        ]
                    }
                }
            ]
        }
    )
    assert completion.tool_calls[0].thought_signature == "sig-abc"


def test_gemini_contents_echoes_thought_signature() -> None:
    conversation = [
        {
            "role": "assistant",
            "content": None,
            "tool_calls": [
                ToolCall("c1", "list_leases", {"a": 1}, thought_signature="sig-abc")
            ],
        },
    ]
    _system, contents = gemini_contents(conversation)
    part = contents[0]["parts"][0]
    assert part["functionCall"] == {"name": "list_leases", "args": {"a": 1}}
    assert part["thoughtSignature"] == "sig-abc"


def test_gemini_contents_omits_absent_thought_signature() -> None:
    _system, contents = gemini_contents(CONVERSATION)
    assert "thoughtSignature" not in contents[1]["parts"][0]
