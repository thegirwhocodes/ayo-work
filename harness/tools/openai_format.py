"""Convert the Gemini Live function declarations in this package to OpenAI Realtime tools.

The declarations in `declarations.py` stay the single source of truth (AGENTS.md: tool
definitions live in harness/tools/). Gemini writes JSON-schema types in upper case
("OBJECT", "STRING"); OpenAI expects lower case ("object", "string") and wraps each
function as {"type": "function", "name", "description", "parameters"}.
"""
from __future__ import annotations

from typing import Any


def _lower_types(schema: Any) -> Any:
    if isinstance(schema, dict):
        return {key: (value.lower() if key == "type" and isinstance(value, str) else _lower_types(value))
                for key, value in schema.items()}
    if isinstance(schema, list):
        return [_lower_types(item) for item in schema]
    return schema


def to_openai_tools(declarations: list[dict[str, Any]]) -> list[dict[str, Any]]:
    tools = []
    for declaration in declarations or []:
        tool = {"type": "function", "name": declaration["name"], "description": declaration.get("description", "")}
        tool["parameters"] = _lower_types(declaration.get("parameters") or {"type": "object", "properties": {}})
        tools.append(tool)
    return tools
