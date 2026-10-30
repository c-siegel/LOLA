"""
Reasoning Route

Decides per turn whether the model is asked to reason, and which key answers: the project's main
one (fast) or its second one (deep, when it has one). The mode alone decides for "off", "always"
and "default"; "adaptive" asks the escalation rules (chat/escalation_rules.py) about the turn.
"""

import logging
from collections.abc import Callable
from dataclasses import dataclass
from typing import Literal

from app.features.ai.llm.reasoning import reasoning_support
from app.features.chat.escalation_rules import check

logger = logging.getLogger(__name__)

# "deep": reasoning was requested (or the second model answers). "fast": reasoning was switched
# off. "default": nothing was sent, the model does what it does without being asked (also the case
# for a model that can't be switched, whatever the project's mode).
Route = Literal["default", "fast", "deep"]


@dataclass(frozen=True)
class RouteDecision:
    route: Route
    # What goes into ChatRequest.reasoning.
    reasoning: str | None
    # A short code saying why (never message text): for logs and the Latency Lab.
    reason: str = ""
    # Whether the project's second key answers this turn.
    use_deep_key: bool = False


# How long a reasoning call may stay silent before the turn is answered without reasoning instead.
# A constant until measurements show a project needs its own (docs/adaptive-reasoning-plan.md).
DEEP_TIMEOUT_SECONDS = 60.0

DEFAULT_ROUTE = RouteDecision("default", None, "mode_default")
FAST_ROUTE = RouteDecision("fast", "off", "fast")


def decide(
    *,
    mode: str,
    effort: str | None,
    main: tuple[str, str],
    deep: tuple[str, str] | None,
    message: str,
    history: list[dict],
    language: str,
    budget: Callable[[], bool] | None = None,
    fast_only: bool = False,
    forced: str | None = None,
) -> RouteDecision:
    """`main`/`deep` are (provider, model id) of the two keys (`deep` None: no second key).

    `budget` spends one reasoning reply and says whether there was room (None: no limit, as for the
    owner's own tests); out of budget, the turn is answered without reasoning. `fast_only` (a repeat
    after a failed attempt) skips reasoning without spending the budget again. `forced` ("fast" or
    "deep") is the Latency Lab's override and replaces the mode and the rules.
    """
    if forced:
        mode = "adaptive"
    if mode == "off":
        return FAST_ROUTE
    if mode not in ("always", "adaptive"):
        return DEFAULT_ROUTE

    target = deep or main
    can_reason = reasoning_support(*target) != "none"
    if not can_reason and deep is None:
        # Nothing to switch and no second model: reasoning can't be offered.
        return DEFAULT_ROUTE
    deep_route = RouteDecision("deep", (effort or "on") if can_reason else None, use_deep_key=deep is not None)

    if forced == "fast":
        return RouteDecision("fast", "off", "forced")
    if forced == "deep":
        reason = "forced"
    elif mode == "adaptive":
        escalation = check(message, history, language)
        if not escalation.deep:
            return RouteDecision("fast", "off", escalation.reason)
        reason = escalation.reason
    else:
        reason = "always"
    if fast_only:
        return RouteDecision("fast", "off", "retry")
    if budget is not None and not budget():
        logger.info("Reasoning budget used up, answering without reasoning")
        return RouteDecision("fast", "off", "budget")
    return RouteDecision(deep_route.route, deep_route.reasoning, reason, deep_route.use_deep_key)
