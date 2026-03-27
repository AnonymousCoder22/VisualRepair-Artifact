def normalize_model_name(model_name: str) -> str:
    return str(model_name or "").strip().lower()


def is_claude_model(model_name: str) -> bool:
    return "claude" in normalize_model_name(model_name)


def is_openai_compatible_model(model_name: str) -> bool:
    model_name = normalize_model_name(model_name)
    return bool(model_name) and not is_claude_model(model_name)


def is_reasoning_model(model_name: str) -> bool:
    model_name = normalize_model_name(model_name)
    return is_openai_compatible_model(model_name) and model_name.startswith("o")
