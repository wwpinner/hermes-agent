"""Shared request-time Zero Data Retention enforcement for OpenRouter."""

from typing import Any


def enforce_openrouter_zdr(
    api_kwargs: dict[str, Any],
    *,
    is_openrouter: bool,
    base_url: Any = None,
) -> None:
    """Force ``provider.zdr=true`` at the final OpenRouter wire boundary."""
    if not is_openrouter:
        return
    try:
        from agent.gemini_native_adapter import is_native_gemini_base_url

        if is_native_gemini_base_url(base_url):
            return
    except Exception:
        # Privacy wins if endpoint classification is temporarily unavailable.
        pass
    try:
        from hermes_cli.config import openrouter_zdr_enabled

        if not openrouter_zdr_enabled():
            return
    except Exception as exc:
        raise RuntimeError(
            "Could not determine the configured OpenRouter ZDR policy; "
            "refusing to build the request."
        ) from exc

    raw_extra = api_kwargs.get("extra_body")
    extra_body = dict(raw_extra) if isinstance(raw_extra, dict) else {}
    raw_provider = extra_body.get("provider")
    provider = dict(raw_provider) if isinstance(raw_provider, dict) else {}
    provider["zdr"] = True
    extra_body["provider"] = provider
    api_kwargs["extra_body"] = extra_body
