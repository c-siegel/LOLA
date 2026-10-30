"""
Reasoning Route

Decides per turn whether the model is asked to reason. Today a project's mode alone decides; the
planned "adaptive" mode (docs/adaptive-reasoning-plan.md) will look at the turn as well, which is
why this is a function of its own and not a field read in the pipeline.
"""

from dataclasses import dataclass
from typing import Literal

from app.features.ai.llm.reasoning import reasoning_support

# "deep": reasoning was requested. "fast": reasoning was switched off. "default": nothing was
# sent, the model does what it does without being asked (also the case for a model that can't be
# switched, whatever the project's mode).
Route = Literal["default", "fast", "deep"]


@dataclass(frozen=True)
class RouteDecision:
    route: Route
    # What goes into ChatRequest.reasoning.
    reasoning: str | None


DEFAULT_ROUTE = RouteDecision("default", None)
FAST_ROUTE = RouteDecision("fast", "off")


def decide(provider: str, model_id: str, mode: str, effort: str | None) -> RouteDecision:
    if mode == "off":
        return FAST_ROUTE
    if mode == "always" and reasoning_support(provider, model_id) != "none":
        return RouteDecision("deep", effort or "on")
    return DEFAULT_ROUTE
