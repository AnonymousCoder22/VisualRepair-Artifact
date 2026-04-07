#!/usr/bin/env python3
import argparse
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path


def is_nonempty_file(path: str) -> bool:
    try:
        return os.path.isfile(path) and os.path.getsize(path) > 0
    except Exception:
        return False


def project_root_from_code_dir() -> str:
    return os.path.normpath(os.path.join(os.path.dirname(__file__), ".."))


def resolve_screenshot_script(project_root: str, repo_basename: str) -> str:
    project_root_path = Path(project_root)
    screenshot_root = project_root_path / "Tools" / "screenshot"
    script_candidates = {
        "prism": [
            screenshot_root / "prismjs_capture_from_bug_info.cjs",
        ],
        "highlight.js": [
            screenshot_root / "highlightjs_capture_from_bug_info.cjs",
        ],
        "Chart.js": [
            screenshot_root / "chartjs_capture_from_bug_info.cjs",
        ],
        "marked": [
            screenshot_root / "markedjs_capture_from_bug_info.cjs",
        ],
        "react-pdf": [
            screenshot_root / "reactpdf_capture_from_bug_info.cjs",
        ],
    }

    for candidate in script_candidates.get(repo_basename, []):
        if candidate.is_file():
            return str(candidate)
    return ""


def resolve_instance_root(repo_path: str, dataset_split: str, instance_id: str) -> str:
    repo_path = str(repo_path or "").strip()
    dataset_split = str(dataset_split or "").strip()
    instance_id = str(instance_id or "").strip()
    if not (repo_path and dataset_split and instance_id):
        return ""

    instance_prefix = instance_id.split("__", 1)[0] if "__" in instance_id else instance_id
    return os.path.join(repo_path, dataset_split, instance_prefix, instance_id)


def resolve_instance_repo_path(repo_path: str, dataset_split: str, instance_id: str) -> str:
    instance_root = resolve_instance_root(repo_path, dataset_split, instance_id)
    if not instance_root:
        return ""
    repo_root = os.path.join(instance_root, "REPO")
    if not os.path.isdir(repo_root):
        return ""

    candidates = []
    for name in sorted(os.listdir(repo_root)):
        full = os.path.join(repo_root, name)
        if os.path.isdir(full):
            candidates.append(full)

    if not candidates:
        if os.path.isfile(os.path.join(repo_root, "code.html")):
            return repo_root
        return ""

    for candidate in candidates:
        if os.path.isfile(os.path.join(candidate, "code0.html")):
            return candidate
    for candidate in candidates:
        if os.path.isfile(os.path.join(candidate, "code.html")):
            return candidate
    return candidates[0]


def default_html_file(repo_dir: str) -> str:
    for name in ("code0.html", "code.html"):
        if os.path.isfile(os.path.join(repo_dir, name)):
            return name
    return ""


def build_screenshot_cmd(repo_dir: str, baseline_png: str, html_file: str, instance_id: str = ""):
    repo_basename = os.path.basename(os.path.normpath(repo_dir))
    project_root = project_root_from_code_dir()
    script_map = {
        "prism": {
            "mode_env": "GUIREPAIR_PRISM_SCREENSHOT_MODE",
            "mode_default": "playwright",
        },
        "highlight.js": {
            "mode_env": "GUIREPAIR_HIGHLIGHTJS_SCREENSHOT_MODE",
            "mode_default": "playwright",
        },
        "Chart.js": {
            "mode_env": "GUIREPAIR_CHARTJS_SCREENSHOT_MODE",
            "mode_default": "playwright",
        },
        "marked": {
            "mode_env": "GUIREPAIR_MARKEDJS_SCREENSHOT_MODE",
            "mode_default": "playwright",
        },
        "react-pdf": {
            "mode_env": "GUIREPAIR_REACTPDF_SCREENSHOT_MODE",
            "mode_default": "",
        },
    }
    script_meta = script_map.get(repo_basename)
    if not script_meta:
        return []
    script_path = resolve_screenshot_script(project_root, repo_basename)

    if not script_path:
        return []

    cmd = ["node", script_path, "--repo", repo_dir, "--out", baseline_png]
    if instance_id:
        cmd.extend(["--instance-id", instance_id])
    mode = os.getenv(script_meta["mode_env"], script_meta["mode_default"])
    if mode:
        cmd.extend(["--mode", mode])
    if html_file:
        cmd.extend(["--html", html_file])
    if repo_basename == "react-pdf":
        cmd.append("--force-build")
    return cmd


def requires_feedback_multi(repo_dir: str) -> bool:
    repo_basename = os.path.basename(os.path.normpath(repo_dir or ""))
    return repo_basename in {"prism", "highlight.js", "Chart.js", "marked", "react-pdf"}


def try_generate_baseline_image(
    baseline_png: str,
    repo_path: str,
    dataset_split: str,
    instance_id: str,
    timeout_seconds: int,
) -> bool:
    repo_dir = resolve_instance_repo_path(repo_path, dataset_split, instance_id)
    if not repo_dir:
        print(
            "WARN: cannot resolve instance repo for baseline generation "
            f"(repo_path={repo_path!r}, dataset_split={dataset_split!r}, instance_id={instance_id!r})"
        )
        return False

    html_file = default_html_file(repo_dir)

    cmd = build_screenshot_cmd(repo_dir, baseline_png, html_file, instance_id)
    if not cmd:
        print(f"WARN: baseline generation skipped, unsupported repo or screenshot script missing: {repo_dir}")
        return False

    os.makedirs(os.path.dirname(baseline_png), exist_ok=True)
    if html_file:
        print(f"INFO: baseline image missing, auto-generate via screenshot script (html={html_file})")
    else:
        print("INFO: baseline image missing, auto-generate via screenshot script (template default html)")
    result = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=max(1, int(timeout_seconds)),
    )
    combined = ((result.stdout or "") + (result.stderr or "")).strip("\n")
    if combined:
        for line in combined.splitlines():
            print(f"[baseline-capture] {line}")

    if result.returncode != 0:
        print(f"WARN: baseline auto-generation command failed (rc={result.returncode})")
        return False
    if not is_nonempty_file(baseline_png):
        print(f"WARN: baseline auto-generation succeeded but output missing/empty: {baseline_png}")
        return False
    print(f"INFO: generated baseline image: {baseline_png}")
    return True


def try_copy_reference_baseline_image(
    baseline_png: str,
    repo_path: str,
    dataset_split: str,
    instance_id: str,
) -> bool:
    instance_root = resolve_instance_root(repo_path, dataset_split, instance_id)
    if not instance_root:
        return False
    image_dir = os.path.join(instance_root, "IMAGE")
    if not os.path.isdir(image_dir):
        return False

    candidates = []
    for name in sorted(os.listdir(image_dir)):
        if not name.lower().endswith(".png"):
            continue
        full = os.path.join(image_dir, name)
        if is_nonempty_file(full):
            candidates.append(full)
    if not candidates:
        return False

    src = candidates[0]
    os.makedirs(os.path.dirname(baseline_png), exist_ok=True)
    try:
        shutil.copy2(src, baseline_png)
    except Exception as e:
        print(f"WARN: failed to copy reference baseline image: {src} -> {baseline_png}, error={e}")
        return False

    if is_nonempty_file(baseline_png):
        print(f"INFO: baseline fallback copied from reference image: {src}")
        return True
    return False


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Validate GUIRepair val-mode image completeness."
    )
    parser.add_argument("--log-dir", required=True, help="Instance output directory.")
    parser.add_argument("--task", required=True, help="run or val.")
    parser.add_argument("--patch-select", required=True, help="True/False.")
    parser.add_argument("--val-patch-no", required=True, help="Validation patch index.")
    parser.add_argument("--dataset-split", default="", help="Dataset split, e.g. test/dev.")
    parser.add_argument("--instance-id", default="", help="Instance id.")
    parser.add_argument("--repo-path", default="", help="Dataset root repo path.")
    parser.add_argument("--screenshot-timeout-seconds", type=int, default=180)
    args = parser.parse_args()

    task = str(args.task)
    patch_select = str(args.patch_select)
    val_patch_no = str(args.val_patch_no)
    log_dir = args.log_dir

    if task != "val":
        print("SKIP: task is not val")
        return 0
    if patch_select != "True":
        print("SKIP: Patch_Select != True")
        return 0
    try:
        if int(float(val_patch_no)) <= 0:
            print("SKIP: val_patch_no <= 0")
            return 0
    except Exception:
        print(f"WARN: invalid val_patch_no={val_patch_no!r}, skip strict image check")
        return 0

    feedback_path = os.path.join(log_dir, "5-3_feedback_multi.json")
    image_dir = os.path.join(log_dir, "IMAGE")
    baseline = os.path.join(image_dir, "0.png")
    repo_dir = resolve_instance_repo_path(args.repo_path, args.dataset_split, args.instance_id)
    strict_feedback_required = requires_feedback_multi(repo_dir)

    if not repo_dir and args.repo_path and args.dataset_split and args.instance_id:
        instance_root = resolve_instance_root(args.repo_path, args.dataset_split, args.instance_id)
        repo_root = os.path.join(instance_root, "REPO") if instance_root else ""
        if os.path.isdir(repo_root):
            print(
                "SKIP: strict feedback check disabled because repo basename is not in "
                f"runtime feedback allowlist: {repo_root}"
            )
            strict_feedback_required = False

    if not is_nonempty_file(baseline):
        generated = False
        try:
            generated = try_generate_baseline_image(
                baseline,
                args.repo_path,
                args.dataset_split,
                args.instance_id,
                int(args.screenshot_timeout_seconds),
            )
        except subprocess.TimeoutExpired:
            print(f"WARN: baseline auto-generation timed out after {args.screenshot_timeout_seconds}s")
        if not generated and not is_nonempty_file(baseline):
            try_copy_reference_baseline_image(
                baseline,
                args.repo_path,
                args.dataset_split,
                args.instance_id,
            )

    if strict_feedback_required and not repo_dir:
        print(
            "FAIL: strict feedback repo requires resolved instance repo, but REPO checkout is missing "
            f"(repo_path={args.repo_path!r}, dataset_split={args.dataset_split!r}, instance_id={args.instance_id!r})"
        )
        return 2
    if strict_feedback_required and not os.path.isfile(feedback_path):
        print(f"FAIL: missing feedback file: {feedback_path}")
        return 2
    if not strict_feedback_required and not os.path.isfile(feedback_path):
        print("PASS: feedback file not required for this repo")
        return 0
    if not is_nonempty_file(baseline):
        print(f"FAIL: missing baseline image: {baseline}")
        return 3

    try:
        with open(feedback_path, "r", encoding="utf-8") as f:
            feedback = json.load(f)
    except Exception as e:
        print(f"FAIL: failed to parse feedback json: {e}")
        return 4

    selected = feedback.get("selected_patch_no")
    if selected in (None, "", 0, "0"):
        print("PASS: no selected_patch_no (no LLM YES), baseline image exists")
        return 0

    try:
        selected_no = int(selected)
    except Exception:
        print(f"FAIL: invalid selected_patch_no: {selected!r}")
        return 5

    if selected_no < 1:
        print("PASS: selected_patch_no < 1")
        return 0

    missing = []
    for i in range(1, selected_no + 1):
        png = os.path.join(image_dir, f"{i}.png")
        if not is_nonempty_file(png):
            missing.append(i)

    if missing:
        miss = ",".join(str(x) for x in missing)
        print(f"FAIL: missing/empty png(s) up to selected_patch_no={selected_no}: {miss}")
        return 6

    print(f"PASS: image set complete (0..{selected_no})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
