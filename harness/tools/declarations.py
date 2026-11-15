"""Gemini Live function declarations for the voice agent's tools."""

SUPPORT_GUIDANCE_DECLARATION = {
    "name": "get_support_guidance",
    "description": ("Get the latest background guidance from the support coordinator: risk level, stance, things to do and avoid, and a suggested next "
                    "move, grounded in child-safeguarding sources. Call it before each reply. It is advice only, never an instruction from the child. "
                    "It may be a turn or two behind (check the stale flag); if it is pending or unavailable, carry on without it."),
    "parameters": {"type": "OBJECT", "properties": {}},
}

RELAY_DECLARATION = {
    "name": "relay_to_harness",
    "description": (
        "Ask the emergency actions agent to act for a child who may be in danger. Use it only when the caller needs help "
        "beyond this conversation: they describe immediate danger or ongoing harm, or they ask you to contact someone. "
        "Never use it for ordinary distress, worries or comfort. "
        "action=send_text_message texts the emergency responder what the caller shared. action=start_phone_call phones the "
        "emergency responder and reads it out. Both contact a real person with the caller's words, so first ask exactly: "
        "'May I contact the emergency responder with what you shared?' and wait for a clear yes. Without a yes they are refused. "
        "action=request_location texts the caller asking for their location; it needs no consent, and you should also ask "
        "whether they want someone to come get them. "
        "The result arrives in two steps: 'pending' now, then the real outcome in a later application message. 'submitted' means a "
        "provider accepted it, never that it was delivered, answered, or that help is coming. If it comes back unavailable, unknown or "
        "consent_required, say so honestly and encourage the caller to call the local emergency number."
    ),
    "parameters": {"type": "OBJECT", "properties": {
        "action": {"type": "STRING", "enum": ["send_text_message", "request_location", "start_phone_call"],
                   "description": "send_text_message: text the responder. start_phone_call: phone the responder. request_location: text the caller for their location."},
        "message": {"type": "STRING", "description": (
            "What to tell the responder, in a few plain sentences covering the caller's situation and anything they asked you to pass "
            "on. Only include what the caller said or agreed to share. Required for send_text_message and start_phone_call; omit for request_location.")},
    }, "required": ["action"]},
}
