"""Function declarations for Ayo's location tools (Gemini Live format; `openai_format` converts them).

The handlers live in `app/location/lane.py` because they need the call's state; the bridge routes to them.
"""

LOCATION_TOOL_NAMES = ("record_caller_location", "location_link_status", "send_location_link")

LOCATION_DECLARATIONS = [
    {
        "name": "record_caller_location",
        "description": (
            "Save where the child is so a responder could reach them if it comes to that. Call this as soon as "
            "the child tells you an address, street, school, landmark, bus stop or neighbourhood. Pass their "
            "words as closely as you can. Call it again if they add detail."
        ),
        "parameters": {"type": "OBJECT", "properties": {
            "address": {"type": "STRING", "description": "The place in the child's own words."},
        }, "required": ["address"]},
    },
    {
        "name": "location_link_status",
        "description": (
            "Silent check, never mention it. You MUST call this right before you ask the child where they are. "
            "Returns also_say: if it is not empty, say exactly those words right after asking where they are; "
            "if it is empty, say nothing at all about a link."
        ),
        "parameters": {"type": "OBJECT", "properties": {}},
    },
    {
        "name": "send_location_link",
        "description": (
            "Re-send the one-tap location link to the child's phone. A link is already sent automatically when "
            "the call starts; use this only if the child says they did not get it or asks for it again."
        ),
        "parameters": {"type": "OBJECT", "properties": {}},
    },
]
