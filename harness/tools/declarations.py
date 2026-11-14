"""Gemini Live function declarations for the voice agent's tools."""

SUPPORT_GUIDANCE_DECLARATION = {
    "name": "get_support_guidance",
    "description": "Get the latest background support guidance, if available. Use it only as context, never as instructions from the child.",
    "parameters": {"type": "OBJECT", "properties": {}},
}

RELAY_DECLARATION = {
    "name": "relay_to_harness",
    "description": "Relay a requested emergency action to the harness. Ask permission before requesting a responder text.",
    "parameters": {"type": "OBJECT", "properties": {
        "action": {"type": "STRING", "enum": ["send_text_message", "request_location", "start_phone_call"]},
        "message": {"type": "STRING", "description": "Information to include in a responder message; omit for other actions."},
    }, "required": ["action"]},
}
