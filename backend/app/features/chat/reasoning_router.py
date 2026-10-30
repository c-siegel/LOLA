"""
Reasoning Route

Decides per turn whether the model is asked to reason. Today a project's mode alone decides; the
planned "adaptive" mode (docs/adaptive-reasoning-plan.md) will look at the turn as well, which is
why this is a function of its own and not a field read in the pipeline.
"""

import logging
from collections.abc import Callable
from dataclasses import dataclass
from typing import Literal

from app.features.ai.llm.reasoning import reasoning_support

logger = logging.getLogger(__name__)

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


def decide(
    provider: str, model_id: str, mode: str, effort: str | None, budget: Callable[[], bool] | None = None
) -> RouteDecision:
    """`budget` spends one reasoning reply and says whether there was room (None: no limit, as for
    the owner's own tests). Out of budget, the turn is answered without reasoning."""
    if mode == "off":
        return FAST_ROUTE
    if mode == "always" and reasoning_support(provider, model_id) != "none":
        if budget is not None and not budget():
            logger.info("Reasoning budget used up, answering without reasoning")
            return FAST_ROUTE
        return RouteDecision("deep", effort or "on")
    return DEFAULT_ROUTE
