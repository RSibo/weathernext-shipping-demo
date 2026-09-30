"""A scripted stand-in for google-genai's Client: each generate_content call pops the next response."""

from __future__ import annotations

from google.genai import types

SID = "T-1"
GOOD = {
    "shipment_id": SID, "status": "REROUTE", "chosen_option_id": "detour:i-70-e",
    "headline": "Reroute via I-70 E: high winds on I-80 E", "recommendation": "Take I-70 E instead of I-80 E.",
    "rationale": "I-80 E exceeds the 35 mph limit; I-70 E stays below it.", "driver_message": "Take I-70 E today.",
}


def call(name, **args):
    return (name, {"shipment_id": SID, **args})


def resp(*calls, thought=None, text=None):
    parts = [types.Part(text=thought, thought=True)] if thought else []
    parts += [types.Part(function_call=types.FunctionCall(id=f"c{i}", name=n, args=a)) for i, (n, a) in enumerate(calls)]
    if text:
        parts.append(types.Part(text=text))
    return types.GenerateContentResponse(
        candidates=[types.Candidate(content=types.Content(role="model", parts=parts))],
        usage_metadata=types.GenerateContentResponseUsageMetadata(
            prompt_token_count=100, candidates_token_count=10, thoughts_token_count=5
        ),
    )


class FakeClient:
    def __init__(self, script):
        self.script = list(script)
        self.requests = []
        self.models = self

    def generate_content(self, *, model, contents, config):
        self.requests.append({"model": model, "contents": list(contents), "config": config})
        step = self.script.pop(0) if self.script else resp(call("check_route_weather"))
        if isinstance(step, BaseException):
            raise step
        return step


HERO = [
    resp(call("check_route_weather"), thought="Check the weather first."),
    resp(call("compute_detour_route"), call("evaluate_departure_delay")),
    resp(call("submit_advisory", **{k: v for k, v in GOOD.items() if k != "shipment_id"})),
]
