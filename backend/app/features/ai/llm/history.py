"""
Building The Provider Request Body

Turns a ChatRequest into the OpenAI-style message list and sampling parameters every LLM
integration sends (litellm and Arcana alike).
"""

from app.features.ai.llm.base import ChatRequest

# Limits how many previous messages are sent per request, regardless of how much the client
# actually sends (the history only lives in the browser tab, see
# frontend/src/pages/PublicChat/index.tsx, so it's client-controlled). Without this cap, a long
# conversation would let the context window and cost per request grow without bound.
MAX_HISTORY_MESSAGES = 20


def sampling_params(request: ChatRequest) -> dict:
    """The project's temperature/top_p as request kwargs, omitting whichever one is None.

    Both are standard OpenAI-compatible parameters that litellm passes on to every provider it
    supports (`litellm.get_supported_openai_params` lists them for openai, ollama, gemini, ...),
    and the GWDG Arcana endpoint accepts them in its request body too. Omitting rather than
    sending a default matters: a provider that doesn't know a parameter can reject the whole
    request, so "not configured" has to mean "not in the body at all".
    """
    params = {}
    if request.temperature is not None:
        params["temperature"] = request.temperature
    if request.top_p is not None:
        params["top_p"] = request.top_p
    return params


def thinking_extra_body(request: ChatRequest) -> dict:
    """`chat_template_kwargs` that switch a vLLM-style model's reasoning on or off, or {}.

    Qwen3-style hybrid models read `enable_thinking` from the chat template; a model without that
    switch (an Instruct-only model, or one that always reasons) ignores it. Left out entirely when
    the project doesn't set it, for the same reason as in sampling_params.
    """
    if request.thinking is None:
        return {}
    return {"chat_template_kwargs": {"enable_thinking": request.thinking}}


def build_messages(request: ChatRequest) -> list[dict]:
    """Build the litellm-style message list from the project's prompt, optional start message, and history."""
    # start_prompt is inserted as an earlier assistant message (not as part of the system
    # prompt) — that way the model "remembers", on every request, that it actually said this
    # itself (e.g. a posed task), consistent with the bubble students see as the first message
    # (see the public chat's load route). history is the actual, already-exchanged conversation
    # so far — without it, every request would be stateless.
    system = request.preprompt
    if request.reference_material:
        system = f"{system}\n\n{request.reference_material}" if system else request.reference_material
    messages = [{"role": "system", "content": system}]
    if request.start_prompt:
        messages.append({"role": "assistant", "content": request.start_prompt})
    if request.history:
        messages.extend(request.history[-MAX_HISTORY_MESSAGES:])
    messages.append({"role": "user", "content": request.message})
    return messages
