import os
import json
import html as html_lib
import re
import subprocess
import time

from PIL import Image

from Code.llm_io_stream import openai_chat, claude_chat, extract_json_from_text
from Code.model_utils import is_claude_model


"""
Merged support module for:
1. Screenshot capture / fallback helpers.
2. Validation `code.html` generation helpers.
3. Deterministic runtime script selection and HTML rewrite helpers.

Supported validation screenshot/runtime families in this repository revision:
- PrismJS
- highlight.js
- Chart.js
- marked.js
"""


SCREENSHOT_TARGETS = (
    {
        "key": "prism",
        "script": "prismjs_capture_from_bug_info.cjs",
        "mode_env": "GUIREPAIR_PRISM_SCREENSHOT_MODE",
        "mode_default": "playwright",
        "match": lambda repo_basename, instance_id: repo_basename == "prism",
    },
    {
        "key": "highlightjs",
        "script": "highlightjs_capture_from_bug_info.cjs",
        "mode_env": "GUIREPAIR_HIGHLIGHTJS_SCREENSHOT_MODE",
        "mode_default": "playwright",
        "match": lambda repo_basename, instance_id: repo_basename == "highlight.js",
    },
    {
        "key": "chartjs",
        "script": "chartjs_capture_from_bug_info.cjs",
        "mode_env": "GUIREPAIR_CHARTJS_SCREENSHOT_MODE",
        "mode_default": "playwright",
        "match": lambda repo_basename, instance_id: repo_basename == "Chart.js",
    },
    {
        "key": "markedjs",
        "script": "markedjs_capture_from_bug_info.cjs",
        "mode_env": "GUIREPAIR_MARKEDJS_SCREENSHOT_MODE",
        "mode_default": "playwright",
        "match": lambda repo_basename, instance_id: repo_basename == "marked",
    },
)

HIGHLIGHTJS_TEMPLATE2_INSTANCE_IDS = {
    "highlightjs__highlight.js-2684",
    "highlightjs__highlight.js-2703",
    "highlightjs__highlight.js-2704",
    "highlightjs__highlight.js-2726",
    "highlightjs__highlight.js-2727",
    "highlightjs__highlight.js-2740",
    "highlightjs__highlight.js-2750",
    "highlightjs__highlight.js-2765",
    "highlightjs__highlight.js-2785",
    "highlightjs__highlight.js-2811",
    "highlightjs__highlight.js-2897",
    "highlightjs__highlight.js-2899",
    "highlightjs__highlight.js-2927",
    "highlightjs__highlight.js-2932",
    "highlightjs__highlight.js-2958",
    "highlightjs__highlight.js-2960",
    "highlightjs__highlight.js-2969",
    "highlightjs__highlight.js-2972",
    "highlightjs__highlight.js-3000",
    "highlightjs__highlight.js-3018",
}

HIGHLIGHTJS_TEMPLATE1_INSTANCE_IDS = {
    "highlightjs__highlight.js-3070",
    "highlightjs__highlight.js-3154",
    "highlightjs__highlight.js-3203",
    "highlightjs__highlight.js-3207",
    "highlightjs__highlight.js-3212",
    "highlightjs__highlight.js-3249",
    "highlightjs__highlight.js-3278",
    "highlightjs__highlight.js-3287",
    "highlightjs__highlight.js-3301",
    "highlightjs__highlight.js-3312",
    "highlightjs__highlight.js-3316",
    "highlightjs__highlight.js-3367",
    "highlightjs__highlight.js-3381",
    "highlightjs__highlight.js-3411",
    "highlightjs__highlight.js-3438",
    "highlightjs__highlight.js-3457",
    "highlightjs__highlight.js-3516",
    "highlightjs__highlight.js-3559",
    "highlightjs__highlight.js-3644",
}


def project_root_from_validation_dir() -> str:
    return os.path.normpath(os.path.join(os.path.dirname(__file__), "..", ".."))


def make_token_usage_record(args=None, *, base_model: str = "", skipped: bool = True) -> dict:
    resolved_base_model = base_model or getattr(args, "base_model", "")
    return {
        "base_model": resolved_base_model,
        "prompt_tokens": 0,
        "completion_tokens": 0,
        "total_tokens": 0,
        "skipped": bool(skipped),
    }


def merge_token_usage_record(target: dict, source: dict) -> dict:
    if not isinstance(target, dict) or not isinstance(source, dict):
        return target
    target["prompt_tokens"] = int(target.get("prompt_tokens") or 0) + int(source.get("prompt_tokens") or 0)
    target["completion_tokens"] = int(target.get("completion_tokens") or 0) + int(source.get("completion_tokens") or 0)
    target["total_tokens"] = int(target.get("total_tokens") or 0) + int(source.get("total_tokens") or 0)
    if not target.get("base_model") and source.get("base_model"):
        target["base_model"] = source.get("base_model")
    target["skipped"] = bool(int(target.get("total_tokens") or 0) == 0)
    return target


# Shared file/process helpers
def read_file(file_path):
    with open(file_path, 'r', encoding='utf-8', errors='ignore') as file:
        return file.read()


def save_file(file_path, data, encoding='utf-8'):
    try:
        with open(file_path, 'w', encoding=encoding) as file:
            file.write(data)
    except UnicodeEncodeError as e:
        print(f"Unicode encode error: {e}")
        return "Unicode encode error"


def run_process(argv, cwd: str, timeout_seconds: int = 300):
    try:
        result = subprocess.run(
            argv,
            cwd=cwd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout_seconds,
        )
        return result.returncode, (result.stdout or "").strip(), (result.stderr or "").strip()
    except subprocess.TimeoutExpired as e:
        return 124, "", f"TIMEOUT after {timeout_seconds}s: {e}"
    except FileNotFoundError as e:
        return 127, "", f"NOT FOUND: {e}"
    except Exception as e:
        return 1, "", f"ERROR: {e}"


def resolve_screenshot_target(instance_id, instance_repo_path):
    repo_basename = os.path.basename(os.path.normpath(instance_repo_path))
    for target in SCREENSHOT_TARGETS:
        if target["match"](repo_basename, instance_id):
            return repo_basename, target
    return repo_basename, None


def run_screenshot(argv: list[str], output_png_path: str, logger, timeout_seconds: int = 180) -> bool:
    try:
        result = subprocess.run(
            argv,
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=timeout_seconds,
        )
    except subprocess.TimeoutExpired:
        logger.warning(f"Screenshot capture timed out after {timeout_seconds}s: {' '.join(argv)}")
        return False
    except Exception as e:
        logger.warning(f"Screenshot capture failed to execute: {e}")
        return False

    if result.returncode != 0:
        stderr = (result.stderr or "").strip()
        stdout = (result.stdout or "").strip()
        msg = stderr if stderr else stdout
        if msg:
            logger.warning(f"Screenshot command failed (rc={result.returncode}): {msg}")
        else:
            logger.warning(f"Screenshot command failed (rc={result.returncode}) with empty output.")
        return False

    try:
        if os.path.isfile(output_png_path) and os.path.getsize(output_png_path) > 0:
            return True
        logger.warning(f"Screenshot command succeeded but output missing/empty: {output_png_path}")
        return False
    except Exception:
        logger.warning(f"Screenshot command succeeded but failed to stat output: {output_png_path}")
        return False


def check_nonempty_file(path: str) -> bool:
    try:
        return os.path.isfile(path) and os.path.getsize(path) > 0
    except Exception:
        return False


def check_capture_ui_screenshot(instance_id, instance_repo_path, output_png_path, logger, html_file=None) -> bool:
    try:
        os.makedirs(os.path.dirname(output_png_path), exist_ok=True)
    except Exception:
        pass

    output_png_path = os.path.abspath(output_png_path)
    repo_basename, target = resolve_screenshot_target(instance_id, instance_repo_path)
    if not target:
        return False

    project_root = project_root_from_validation_dir()
    script_path = os.path.join(project_root, "Tools", "screenshot", target["script"])
    if not os.path.isfile(script_path):
        logger.warning(f"Screenshot script not found: {script_path}")
        return False

    mode_env = target.get("mode_env")
    screenshot_mode = os.getenv(mode_env, target.get("mode_default", "")) if mode_env else ""
    argv = ["node", script_path, "--repo", instance_repo_path, "--out", output_png_path]
    if screenshot_mode:
        argv.extend(["--mode", screenshot_mode])
    if instance_id:
        argv.extend(["--instance-id", str(instance_id)])
    if html_file:
        argv.extend(["--html", html_file])

    logger.info(f"Capture screenshot: {' '.join(argv)}")
    return run_screenshot(argv, output_png_path, logger)


def capture_ui_screenshot_with_retry(instance_id, instance_repo_path, output_png_path, logger, html_file=None) -> bool:
    try:
        max_attempts = int(float(os.getenv("GUIREPAIR_SCREENSHOT_MAX_ATTEMPTS", "3")))
    except Exception:
        max_attempts = 3
    try:
        retry_sleep = float(os.getenv("GUIREPAIR_SCREENSHOT_RETRY_SLEEP_SECONDS", "1"))
    except Exception:
        retry_sleep = 1.0

    if max_attempts < 1:
        max_attempts = 1

    for attempt in range(1, max_attempts + 1):
        try:
            if os.path.isfile(output_png_path):
                os.remove(output_png_path)
        except Exception:
            pass

        ok = check_capture_ui_screenshot(
            instance_id,
            instance_repo_path,
            output_png_path,
            logger,
            html_file=html_file,
        )
        if ok and check_nonempty_file(output_png_path):
            return True

        logger.warning(f"Screenshot attempt {attempt}/{max_attempts} failed for {output_png_path}")
        if attempt < max_attempts and retry_sleep > 0:
            time.sleep(retry_sleep)

    return check_nonempty_file(output_png_path)


def write_placeholder_png(output_png_path: str, logger, reason: str = "") -> bool:
    try:
        os.makedirs(os.path.dirname(output_png_path), exist_ok=True)
    except Exception:
        pass
    try:
        img = Image.new("RGB", (16, 16), (255, 255, 255))
        img.save(output_png_path, format="PNG")
        if reason:
            logger.warning(f"Wrote placeholder PNG: {output_png_path} ({reason})")
        else:
            logger.warning(f"Wrote placeholder PNG: {output_png_path}")
        return True
    except Exception as e:
        logger.warning(f"Failed to write placeholder PNG {output_png_path}: {e}")
        return False


def check_png_from_baseline_or_placeholder(output_png_path: str, baseline_png_path: str, logger, reason: str = "") -> bool:
    if check_nonempty_file(output_png_path):
        return True

    if check_nonempty_file(baseline_png_path):
        try:
            with open(baseline_png_path, "rb") as src, open(output_png_path, "wb") as dst:
                dst.write(src.read())
            if check_nonempty_file(output_png_path):
                if reason:
                    logger.warning(f"Filled missing PNG by copying baseline: {output_png_path} ({reason})")
                else:
                    logger.warning(f"Filled missing PNG by copying baseline: {output_png_path}")
                return True
        except Exception as e:
            logger.warning(f"Failed to copy baseline PNG to {output_png_path}: {e}")

    return write_placeholder_png(output_png_path, logger, reason=reason)


def parse_patch_key_num(sample_key: str) -> float:
    try:
        return float(sample_key.split("/")[0])
    except Exception:
        return -1.0


def pick_patch_key_for_index(patches_edits_dict, patch_no: int):
    candidates = []
    for key in patches_edits_dict.keys():
        num = parse_patch_key_num(key)
        if int(num) == int(patch_no):
            candidates.append((num, key))

    if not candidates:
        return None

    candidates.sort(key=lambda item: item[0])
    for num, key in candidates:
        if abs(num - float(patch_no)) < 1e-9:
            return key
    return candidates[0][1]


def resolve_prism_code_template_path(logger) -> str:
    candidates = [
        os.path.join(project_root_from_validation_dir(), "Tools", "screenshot", "code_template", "PrismJS", "template_prismjs.html"),
    ]
    seen = set()
    for p in candidates:
        norm = os.path.normpath(p)
        if norm in seen:
            continue
        seen.add(norm)
        if os.path.isfile(norm):
            return norm
    logger.warning(f"No prism template found in candidates: {candidates}")
    return ""


def extract_first_markdown_code_block(text: str):
    if not text:
        return "", ""
    m = re.search(r"```(?P<lang>[^\n`]*)\n(?P<code>[\s\S]*?)```", text, flags=re.IGNORECASE)
    if not m:
        return "", ""
    lang = (m.group("lang") or "").strip()
    code = (m.group("code") or "").strip("\n")
    return code, lang


def parse_json_payload_from_text(text_payload):
    if isinstance(text_payload, dict):
        return text_payload
    if not isinstance(text_payload, str):
        return {}

    parsed = extract_json_from_text(text_payload)
    if isinstance(parsed, dict):
        return parsed

    fenced = re.search(r"```(?:json)?\s*([\s\S]*?)```", text_payload, flags=re.IGNORECASE)
    candidate = fenced.group(1).strip() if fenced else text_payload.strip()
    try:
        loaded = json.loads(candidate)
        return loaded if isinstance(loaded, dict) else {}
    except Exception:
        return {}


# LLM extraction / generation helpers
def sanitize_extracted_code_text(code_text: str, logger) -> str:
    text = html_lib.unescape(str(code_text or "")).strip()
    if not text:
        return ""

    fenced = re.fullmatch(r"```[^\n`]*\n([\s\S]*?)```", text, flags=re.IGNORECASE)
    if fenced:
        text = fenced.group(1).strip("\n")

    code_block_match = re.search(
        r"<pre\b[^>]*>\s*<code\b[^>]*>(?P<body>[\s\S]*?)</code>\s*</pre>",
        text,
        flags=re.IGNORECASE,
    )
    if code_block_match:
        logger.info("IMAGE->code extraction returned HTML code block wrapper; keeping only <pre><code> body.")
        return html_lib.unescape(code_block_match.group("body")).strip("\n")

    whole_html_match = re.search(r"<html\b|<!DOCTYPE\s+html", text, flags=re.IGNORECASE)
    if whole_html_match:
        logger.warning("IMAGE->code extraction returned a full HTML document; discarding wrapper markup.")
        stripped = re.sub(r"<!DOCTYPE[\s\S]*?>", "", text, flags=re.IGNORECASE)
        stripped = re.sub(r"</?(?:html|head|body)\b[^>]*>", "", stripped, flags=re.IGNORECASE)
        return stripped.strip()

    return text


def sanitize_generated_html_text(html_text: str, logger) -> str:
    text = html_lib.unescape(str(html_text or "")).strip()
    if not text:
        return ""

    fenced = re.fullmatch(r"```(?:html)?\s*([\s\S]*?)```", text, flags=re.IGNORECASE)
    if fenced:
        text = fenced.group(1).strip()

    whole_html_match = re.search(r"<!DOCTYPE\s+html|<html\b", text, flags=re.IGNORECASE)
    if whole_html_match:
        start = whole_html_match.start()
        end_match = re.search(r"</html>\s*$", text, flags=re.IGNORECASE)
        if end_match:
            return text[start:end_match.end()].strip()
        return text[start:].strip()

    if re.search(r"<(?:head|body|pre|code|script|style)\b", text, flags=re.IGNORECASE):
        logger.warning("IMAGE+template->html generation returned HTML without outer <html>; keeping raw HTML fragment.")
        return text

    logger.warning("IMAGE+template->html generation returned non-HTML text; ignoring it.")
    return ""


def generate_html_from_images_with_template_via_llm(
    problem_statement: str,
    image_file_list: list,
    template_html: str,
    args,
    logger,
):
    empty_usage = make_token_usage_record(args, skipped=True)
    if not image_file_list or not template_html:
        return "", empty_usage

    issue_excerpt = (problem_statement or "").strip()
    if len(issue_excerpt) > 4000:
        issue_excerpt = issue_excerpt[:4000] + "\n... (truncated)"

    system_prompt = """
You generate a complete validation HTML file from bug screenshots and a provided HTML template.
Return JSON only with keys:
{
  "html": "the full final HTML document"
}
Rules:
- Output JSON only, no markdown.
- Use the provided template HTML as the base document.
- Return a complete HTML document, preserving the template structure unless the screenshots clearly require changing visible code content or the code language class.
- Keep existing scripts, styles, runtime initialization, and page bootstrap logic from the template unless the screenshots clearly require a different code snippet or code block language.
- Do not omit required script tags or wrapper structure from the template.
- Prefer changing only the code content shown in the screenshots and any directly related language-* class.
- If uncertain, stay close to the template and make the smallest necessary edits.
"""
    user_prompt = [
        {
            "type": "text",
            "text": f"""Generate the final validation HTML by combining the provided template with the code shown in the bug images.
Issue context:
'''
{issue_excerpt}
'''

Template HTML:
```html
{template_html}
```
""",
        }
    ] + list(image_file_list)

    try:
        if is_claude_model(getattr(args, "base_model", "")):
            llm_results, llm_text, token_usage = claude_chat(system_prompt, user_prompt, args, 0.0, 1)
        else:
            llm_results, token_usage = openai_chat(system_prompt, user_prompt, args, 0.0, 1)
    except Exception as e:
        logger.warning(f"IMAGE+template->html generation failed: {e}")
        return "", empty_usage

    if not isinstance(llm_results, dict) or not llm_results:
        return "", token_usage

    first_key = next(iter(llm_results))
    raw_response = llm_results.get(first_key)
    payload = parse_json_payload_from_text(raw_response)

    html_text = ""
    if isinstance(payload, dict):
        html_text = str(payload.get("html", "")).strip()
    if not html_text and isinstance(raw_response, str):
        html_text = raw_response.strip()

    final_html = sanitize_generated_html_text(html_text, logger)
    if not final_html:
        return "", token_usage

    token_usage["skipped"] = False
    return final_html, token_usage


def extract_code_from_images_via_llm(problem_statement: str, image_file_list: list, args, logger):
    empty_usage = make_token_usage_record(args, skipped=True)
    if not image_file_list:
        return "", "", empty_usage

    issue_excerpt = (problem_statement or "").strip()
    if len(issue_excerpt) > 4000:
        issue_excerpt = issue_excerpt[:4000] + "\n... (truncated)"

    system_prompt = """
You extract code snippets from bug screenshots for syntax-highlighting validation.
Return JSON only with keys:
{
  "language": "short language id like sql/javascript/css/plain",
  "code": "the code snippet text shown in the screenshot"
}
Rules:
- Output JSON only, no markdown.
- Return only the raw code snippet text that belongs inside the existing <pre><code>...</code></pre> block.
- Do not return <pre>, <code>, <script>, <html>, <head>, <body>, or any other wrapper tags.
- Do not rewrite or invent any surrounding template/script content outside the visible code snippet.
- DO not change page initialization / bootstrap logic or other runtime scripts; only extract the code snippet text and its language if visible in the screenshots.
- Keep line breaks and indentation in code.
- If uncertain, provide best-effort code and set language to "plain".
"""
    user_prompt = [
        {
            "type": "text",
            "text": f"""Extract the code snippet visible in these bug images.
Issue context:
'''
{issue_excerpt}
'''
""",
        }
    ] + list(image_file_list)

    try:
        if is_claude_model(getattr(args, "base_model", "")):
            llm_results, llm_text, token_usage = claude_chat(system_prompt, user_prompt, args, 0.0, 1)
        else:
            llm_results, token_usage = openai_chat(system_prompt, user_prompt, args, 0.0, 1)
    except Exception as e:
        logger.warning(f"IMAGE->code extraction failed: {e}")
        return "", "", empty_usage

    if not isinstance(llm_results, dict) or not llm_results:
        return "", "", token_usage

    first_key = next(iter(llm_results))
    payload = parse_json_payload_from_text(llm_results.get(first_key))
    if not isinstance(payload, dict):
        return "", "", token_usage

    code_text = str(payload.get("code", "")).strip()
    language = str(payload.get("language", "")).strip()

    if not code_text and isinstance(llm_results.get(first_key), str):
        code_text, maybe_lang = extract_first_markdown_code_block(llm_results.get(first_key))
        if not language:
            language = maybe_lang

    return sanitize_extracted_code_text(code_text, logger), language, token_usage


# Local fallback HTML assembly helpers
def build_code_html_locally_from_template(
    problem_statement: str,
    image_file_list: list,
    template_html: str,
    args,
    logger,
    replace_html_fn,
):
    token_usage = make_token_usage_record(args, skipped=True)
    code_text = ""
    language = ""
    source = "template_default"

    code_text, language, token_usage = extract_code_from_images_via_llm(
        problem_statement,
        image_file_list or [],
        args,
        logger,
    )
    if code_text:
        source = "image"
    else:
        fallback_code, fallback_lang = extract_first_markdown_code_block(problem_statement or "")
        if fallback_code:
            code_text = fallback_code
            language = fallback_lang or language
            source = "problem_statement"

    final_html = template_html
    if code_text:
        final_html = replace_html_fn(template_html, code_text, language, logger)

    return final_html, token_usage, source, len(code_text or "")


def write_code_html_with_llm_then_fallback(
    instance_id,
    code_html_path: str,
    problem_statement: str,
    image_file_list: list,
    template_path: str,
    template_html: str,
    args,
    logger,
    replace_html_fn,
):
    total_token_usage = make_token_usage_record(args, skipped=True)

    final_html, token_usage = generate_html_from_images_with_template_via_llm(
        problem_statement,
        image_file_list or [],
        template_html,
        args,
        logger,
    )
    merge_token_usage_record(total_token_usage, token_usage)
    if final_html:
        save_file(code_html_path, final_html)
        logger.info(
            f"Generated code.html for {instance_id}: {code_html_path} "
            f"(template={template_path}, source=image_plus_template_llm, html_len={len(final_html)})"
        )
        return code_html_path, total_token_usage

    final_html, fallback_token_usage, source, code_len = build_code_html_locally_from_template(
        problem_statement,
        image_file_list or [],
        template_html,
        args,
        logger,
        replace_html_fn,
    )
    merge_token_usage_record(total_token_usage, fallback_token_usage)
    save_file(code_html_path, final_html)
    logger.info(
        f"Generated missing code.html for {instance_id}: {code_html_path} "
        f"(template={template_path}, code_source={source}, code_len={code_len})"
    )
    return code_html_path, total_token_usage


def normalize_language_for_code_class(language: str) -> str:
    lang = (language or "").strip().lower()
    if lang.startswith("language-"):
        lang = lang[len("language-"):]
    lang = re.sub(r"[^a-z0-9_+\-]", "", lang)
    return lang or "plain"


def rewrite_code_open_tag_language(code_open_tag: str, language: str) -> str:
    lang = normalize_language_for_code_class(language)
    class_re = re.compile(r'(\bclass\s*=\s*)(["\'])([^"\']*)(\2)', flags=re.IGNORECASE)
    m = class_re.search(code_open_tag or "")
    if not m:
        return re.sub(r">\s*$", f' class="language-{lang}">', code_open_tag, count=1)

    classes = [c for c in re.split(r"\s+", m.group(3).strip()) if c]
    classes = [c for c in classes if not c.startswith("language-")]
    classes.append(f"language-{lang}")
    new_class = " ".join(classes)
    return (
        code_open_tag[:m.start()]
        + f"{m.group(1)}{m.group(2)}{new_class}{m.group(2)}"
        + code_open_tag[m.end():]
    )


def replace_prism_code_block_html(base_html: str, code_text: str, language: str, logger) -> str:
    if not base_html:
        return base_html

    code_block_re = re.compile(
        r"(?P<prefix><pre\b[^>]*>\s*<code\b[^>]*>)(?P<body>[\s\S]*?)(?P<suffix></code>\s*</pre>)",
        flags=re.IGNORECASE,
    )
    m = code_block_re.search(base_html)
    if not m:
        logger.warning("Template code.html has no <pre><code> block; skipping code replacement.")
        return base_html

    new_prefix = m.group("prefix")
    if language:
        new_prefix = re.sub(
            r"<code\b[^>]*>",
            lambda mm: rewrite_code_open_tag_language(mm.group(0), language),
            new_prefix,
            count=1,
            flags=re.IGNORECASE,
        )

    escaped_code = html_lib.escape(code_text or "", quote=False)
    return base_html[:m.start()] + new_prefix + escaped_code + m.group("suffix") + base_html[m.end():]


def ensure_prism_code_html_for_val(instance_id, instance_repo_path, problem_statement, image_file_list, args, logger):
    code_html_path = os.path.join(instance_repo_path, "code.html")
    token_usage = make_token_usage_record(args, skipped=True)
    if os.path.isfile(code_html_path):
        logger.info(f"Existing code.html found; regenerating and overwriting: {code_html_path}")

    template_path = resolve_prism_code_template_path(logger)
    if not template_path:
        return code_html_path, token_usage

    try:
        template_html = read_file(template_path)
    except Exception as e:
        logger.warning(f"Failed to read template code.html: {template_path}, error={e}")
        return code_html_path, token_usage

    return write_code_html_with_llm_then_fallback(
        instance_id,
        code_html_path,
        problem_statement,
        image_file_list,
        template_path,
        template_html,
        args,
        logger,
        replace_prism_code_block_html,
    )


def resolve_highlightjs_code_template_path(instance_id, logger) -> str:
    template_dir = os.path.join(
        project_root_from_validation_dir(),
        "Tools",
        "screenshot",
        "code_template",
        "highlightjs",
    )
    if instance_id in HIGHLIGHTJS_TEMPLATE1_INSTANCE_IDS:
        template_name = "template_highlightjs1.html"
    elif instance_id in HIGHLIGHTJS_TEMPLATE2_INSTANCE_IDS:
        template_name = "template_highlightjs2.html"
    else:
        template_name = "template_highlightjs1.html"
        logger.warning(
            f"Highlightjs instance_id not found in template mapping, fallback to template1: {instance_id}"
        )

    template_path = os.path.normpath(os.path.join(template_dir, template_name))
    if os.path.isfile(template_path):
        return template_path

    fallback_path = os.path.normpath(os.path.join(template_dir, "template_highlightjs1.html"))
    logger.warning(
        f"Selected highlightjs template missing: {template_path}; fallback={fallback_path}"
    )
    if os.path.isfile(fallback_path):
        return fallback_path

    logger.warning(
        f"No highlightjs code.html template found in candidates: {[template_path, fallback_path]}"
    )
    return ""


def resolve_chartjs_code_template_path(logger) -> str:
    candidates = [
        os.path.join(project_root_from_validation_dir(), "Tools", "screenshot", "code_template", "chartjs", "template_chartjs.html"),
    ]
    seen = set()
    for p in candidates:
        norm = os.path.normpath(p)
        if norm in seen:
            continue
        seen.add(norm)
        if os.path.isfile(norm):
            return norm
    logger.warning(f"No chartjs code.html template found in candidates: {candidates}")
    return ""


def resolve_markedjs_code_template_path(logger) -> str:
    candidates = [
        os.path.join(project_root_from_validation_dir(), "Tools", "screenshot", "code_template", "markedjs", "template_marked.html"),
    ]
    seen = set()
    for p in candidates:
        norm = os.path.normpath(p)
        if norm in seen:
            continue
        seen.add(norm)
        if os.path.isfile(norm):
            return norm
    logger.warning(f"No markedjs code.html template found in candidates: {candidates}")
    return ""


def ensure_highlightjs_code_html_for_val(instance_id, instance_repo_path, problem_statement, image_file_list, args, logger):
    code_html_path = os.path.join(instance_repo_path, "code.html")
    token_usage = make_token_usage_record(args, skipped=True)
    if os.path.isfile(code_html_path):
        logger.info(f"Existing code.html found; regenerating and overwriting: {code_html_path}")

    template_path = resolve_highlightjs_code_template_path(instance_id, logger)
    if not template_path:
        return code_html_path, token_usage

    try:
        template_html = read_file(template_path)
    except Exception as e:
        logger.warning(f"Failed to read highlightjs template: {template_path}, error={e}")
        return code_html_path, token_usage

    return write_code_html_with_llm_then_fallback(
        instance_id,
        code_html_path,
        problem_statement,
        image_file_list,
        template_path,
        template_html,
        args,
        logger,
        replace_prism_code_block_html,
    )


def replace_marked_input_html(base_html: str, markdown_text: str, language: str, logger) -> str:
    if not markdown_text:
        return base_html
    return replace_prism_code_block_html(base_html, markdown_text, language or "markdown", logger)


def normalize_chartjs_inline_script(code_text: str) -> str:
    text = str(code_text or "").strip()
    if not text:
        return ""
    return text


def replace_chartjs_inline_script_html(base_html: str, code_text: str, language: str, logger) -> str:
    if not base_html:
        return base_html

    normalized = normalize_chartjs_inline_script(code_text)
    if not normalized:
        return base_html
    return replace_prism_code_block_html(base_html, normalized, language or "javascript", logger)


def ensure_markedjs_code_html_for_val(instance_id, instance_repo_path, problem_statement, image_file_list, args, logger):
    code_html_path = os.path.join(instance_repo_path, "code.html")
    token_usage = make_token_usage_record(args, skipped=True)
    if os.path.isfile(code_html_path):
        logger.info(f"Existing code.html found; regenerating and overwriting: {code_html_path}")

    template_path = resolve_markedjs_code_template_path(logger)
    if not template_path:
        return code_html_path, token_usage

    try:
        template_html = read_file(template_path)
    except Exception as e:
        logger.warning(f"Failed to read markedjs template: {template_path}, error={e}")
        return code_html_path, token_usage

    return write_code_html_with_llm_then_fallback(
        instance_id,
        code_html_path,
        problem_statement,
        image_file_list,
        template_path,
        template_html,
        args,
        logger,
        replace_marked_input_html,
    )


def ensure_chartjs_code_html_for_val(instance_id, instance_repo_path, problem_statement, image_file_list, args, logger):
    code_html_path = os.path.join(instance_repo_path, "code.html")
    token_usage = make_token_usage_record(args, skipped=True)
    if os.path.isfile(code_html_path):
        logger.info(f"Existing code.html found; regenerating and overwriting: {code_html_path}")

    template_path = resolve_chartjs_code_template_path(logger)
    if not template_path:
        return code_html_path, token_usage

    try:
        template_html = read_file(template_path)
    except Exception as e:
        logger.warning(f"Failed to read chartjs template: {template_path}, error={e}")
        return code_html_path, token_usage

    return write_code_html_with_llm_then_fallback(
        instance_id,
        code_html_path,
        problem_statement,
        image_file_list,
        template_path,
        template_html,
        args,
        logger,
        replace_chartjs_inline_script_html,
    )


def norm_rel_path(p: str) -> str:
    if not p:
        return ""
    if re.match(r'^[a-zA-Z]+://', p):
        return p
    s = str(p).replace("\\", "/")
    if s.startswith("./"):
        s = s[2:]
    if s.startswith("/"):
        s = s[1:]
    return s
