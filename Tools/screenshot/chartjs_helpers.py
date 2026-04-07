#!/usr/bin/env python3
import argparse
import json
import logging
import os
import shutil
import subprocess
import sys
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import List, Optional

from PIL import Image, UnidentifiedImageError

SCREENSHOT_ROOT = Path(__file__).resolve().parent
TOOLS_ROOT = SCREENSHOT_ROOT.parent
PROJECT_ROOT = TOOLS_ROOT.parent
WORKSPACE_ROOT = PROJECT_ROOT.parent

REPRODUCE_ROOT = Path(os.getenv("GUIREPAIR_REPO_PATH") or (WORKSPACE_ROOT / "Reproduce_Scenario")).resolve()
DEFAULT_SCENARIO_ROOT = REPRODUCE_ROOT / "dev" / "chartjs"
DEFAULT_RESULT_ROOT = PROJECT_ROOT / "Result" / "trace" / "o3" / "result" / "dev" / "chartjs"
DEFAULT_DATASET_JSON = PROJECT_ROOT / "Code" / "dataset" / "dev" / "SWE-bench_Multimodal.json"
DEFAULT_QUARANTINE_ROOT = (
    PROJECT_ROOT / "Result" / "trace" / "o3" / "result" / "dev" / "chartjs_excluded" / "fallback_template"
)

GENERATED_ROOT = SCREENSHOT_ROOT / "generated" / "chartjs"
DEFAULT_RERUN_SUMMARY_JSON = GENERATED_ROOT / "chartjs_rerun_summary.json"
DEFAULT_AUDIT_JSON = GENERATED_ROOT / "chartjs_bug_vs_baseline_audit.json"
DEFAULT_FALLBACK_TEMPLATE_AUDIT_JSON = GENERATED_ROOT / "chartjs_fallback_template_audit.json"


def ensure_project_root_on_path() -> None:
    root = str(PROJECT_ROOT)
    if root not in sys.path:
        sys.path.insert(0, root)


def ensure_generated_root() -> Path:
    GENERATED_ROOT.mkdir(parents=True, exist_ok=True)
    return GENERATED_ROOT


ensure_project_root_on_path()

from Code.workflow import (  # noqa: E402
    back_fill_patch,
    clean_repo,
    has_nonempty_patch_edits,
    patch_edits_match_repo,
    select_matchable_patch_key,
)
from Tools.validation.validation_tools import (  # noqa: E402
    capture_ui_screenshot_with_retry,
    check_nonempty_file,
    ensure_chartjs_code_html_for_val,
    extract_first_markdown_code_block,
    load_chartjs_reproduce_code,
    pick_patch_key_for_index,
)

LOGGER = logging.getLogger("chartjs_rerun_images")

LOCAL_BUNDLE_CANDIDATES = (
    "chart.umd.js",
    "chart.js",
    "chart.min.js",
    "Chart.js",
    "Chart.min.js",
)


def read_json(path: Path):
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def reset_image_dir(image_dir: Path):
    image_dir.mkdir(parents=True, exist_ok=True)
    for png in image_dir.glob("*.png"):
        png.unlink()


def copy_png(src: Path, dst: Path):
    if not src.is_file():
        return False
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, dst)
    return True


def html_uses_local_bundle(code_html_path: Path) -> bool:
    if not code_html_path.is_file():
        return False
    text = code_html_path.read_text(encoding="utf-8", errors="replace")
    return "GUIREPAIR_CHARTJS_LOCAL_BUNDLE" in text or "./dist/chart.umd.js" in text


def package_version(repo_dir: Path) -> str:
    package_json = repo_dir / "package.json"
    if not package_json.is_file():
        return ""
    try:
        data = read_json(package_json)
    except Exception:
        return ""
    return str(data.get("version", "")).strip()


def bundle_candidate_urls(version: str) -> list[str]:
    resolved_version = version or "latest"
    base = f"https://cdn.jsdelivr.net/npm/chart.js@{resolved_version}/dist"
    return [
        f"{base}/chart.umd.js",
        f"{base}/chart.umd.min.js",
        f"{base}/chart.min.js",
        f"{base}/Chart.min.js",
        f"{base}/Chart.js",
    ]


def ensure_local_bundle(repo_dir: Path) -> tuple[bool, str]:
    dist_dir = repo_dir / "dist"
    for candidate_name in LOCAL_BUNDLE_CANDIDATES:
        candidate_path = dist_dir / candidate_name
        if candidate_path.is_file() and candidate_path.stat().st_size > 0:
            return True, f"existing:{candidate_name}"

    dist_dir.mkdir(parents=True, exist_ok=True)
    errors = []
    for url in bundle_candidate_urls(package_version(repo_dir)):
        try:
            with urllib.request.urlopen(url, timeout=60) as response:
                content = response.read()
            if not content:
                errors.append(f"empty:{url}")
                continue
            bundle_path = dist_dir / "chart.umd.js"
            bundle_path.write_bytes(content)
            return True, f"downloaded:{url}"
        except urllib.error.HTTPError as exc:
            errors.append(f"http:{exc.code}:{url}")
        except Exception as exc:
            errors.append(f"error:{url}:{exc}")
    return False, "; ".join(errors)


def maybe_apply_patch(
    patch_no: int,
    patches_edits_dict: dict,
    repo_dir: Path,
    repo_structure_dict: dict,
) -> tuple[bool, str | None]:
    patch_key = pick_patch_key_for_index(patches_edits_dict, patch_no)
    patch_edits = patches_edits_dict.get(patch_key, {}) if patch_key else {}

    if not patch_key or not has_nonempty_patch_edits(patch_edits):
        patch_key = select_matchable_patch_key(
            patches_edits_dict,
            preferred_patch_no=patch_no,
            instance_repo_path=str(repo_dir),
            repo_structure_dict=repo_structure_dict,
            applicability_cache={},
        )
        patch_edits = patches_edits_dict.get(patch_key, {}) if patch_key else {}

    if not patch_key or not has_nonempty_patch_edits(patch_edits):
        return False, patch_key

    if not patch_edits_match_repo(patch_edits, str(repo_dir), repo_structure_dict):
        return False, patch_key

    back_fill_patch(patch_edits, str(repo_dir), repo_structure_dict, LOGGER)
    return True, patch_key


def problem_statement_for(instance_id: str, dataset_dict: dict) -> str:
    item = dataset_dict.get(instance_id, {})
    return str(item.get("problem_statement", "") or "")


def patch_numbers(patches_edits_dict: dict) -> list[int]:
    found = set()
    for key in patches_edits_dict.keys():
        try:
            found.add(int(float(str(key).split("/")[0])))
        except Exception:
            continue
    return sorted(found)


def rerun_one_instance(instance_id: str, result_root: Path, repo_root: Path, dataset_dict: dict) -> dict:
    output_dir = result_root / instance_id
    repo_dir = repo_root / instance_id / "REPO" / "Chart.js"
    image_dir = output_dir / "IMAGE"
    summary = {
        "instance_id": instance_id,
        "baseline": False,
        "patches_written": 0,
        "code_html": False,
        "code_source": "",
        "fallback_template": False,
        "bundle": "",
        "reason": "",
    }

    if not repo_dir.is_dir():
        summary["reason"] = f"repo_missing:{repo_dir}"
        return summary

    args = SimpleNamespace(
        base_model="o3",
        output_dir=str(output_dir),
        repo_path=str(REPRODUCE_ROOT),
        dataset_split="dev",
    )

    reset_image_dir(image_dir)
    clean_repo(str(repo_dir), LOGGER)

    problem_statement = problem_statement_for(instance_id, dataset_dict)
    code_html_path, _, code_html_meta = ensure_chartjs_code_html_for_val(
        instance_id,
        str(repo_dir),
        problem_statement,
        [],
        args,
        LOGGER,
        return_metadata=True,
    )
    code_html_path = Path(code_html_path)
    summary["code_html"] = code_html_path.is_file()
    summary["code_source"] = str((code_html_meta or {}).get("source", "") or "")
    summary["fallback_template"] = summary["code_source"] == "template_default"
    if not summary["code_html"]:
        summary["reason"] = "code_html_missing"
        return summary

    if html_uses_local_bundle(code_html_path):
        bundle_ok, bundle_reason = ensure_local_bundle(repo_dir)
        summary["bundle"] = bundle_reason
        if not bundle_ok:
            summary["reason"] = f"bundle_unavailable:{bundle_reason}"
            return summary
    else:
        summary["bundle"] = "external"

    baseline_png = image_dir / "0.png"
    baseline_ok = capture_ui_screenshot_with_retry(
        instance_id,
        str(repo_dir),
        str(baseline_png),
        LOGGER,
        html_file="code.html",
    )
    summary["baseline"] = bool(baseline_ok and check_nonempty_file(str(baseline_png)))
    if not summary["baseline"]:
        summary["reason"] = "baseline_capture_failed"
        return summary

    patches_file = output_dir / "5-2_patches_edits_val.json"
    repo_structure_file = output_dir / "1-1_repo_structure.json"
    if not patches_file.is_file():
        return summary

    try:
        patches_edits_dict = read_json(patches_file)
    except Exception as exc:
        summary["reason"] = f"patches_read_failed:{exc}"
        return summary

    try:
        repo_structure_dict = read_json(repo_structure_file) if repo_structure_file.is_file() else {}
    except Exception:
        repo_structure_dict = {}

    local_bundle_mode = (
        summary["bundle"].startswith("downloaded:")
        or summary["bundle"].startswith("existing:")
        or summary["bundle"] == "existing"
    )

    for patch_no in patch_numbers(patches_edits_dict):
        patch_png = image_dir / f"{patch_no}.png"
        clean_repo(str(repo_dir), LOGGER)
        applied, patch_key = maybe_apply_patch(
            patch_no,
            patches_edits_dict,
            repo_dir,
            repo_structure_dict,
        )
        if not applied:
            copy_png(baseline_png, patch_png)
            summary["patches_written"] += 1
            continue

        if local_bundle_mode and (repo_dir / "node_modules").is_dir():
            build = subprocess.run(
                ["pnpm", "build"],
                cwd=str(repo_dir),
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=300,
            )
            if build.returncode != 0:
                LOGGER.warning(
                    "Patch build failed for %s patch %s key=%s: %s",
                    instance_id,
                    patch_no,
                    patch_key,
                    (build.stderr or build.stdout or "").strip(),
                )
                copy_png(baseline_png, patch_png)
                summary["patches_written"] += 1
                continue

        patch_ok = capture_ui_screenshot_with_retry(
            instance_id,
            str(repo_dir),
            str(patch_png),
            LOGGER,
            html_file="code.html",
        )
        if not patch_ok or not check_nonempty_file(str(patch_png)):
            copy_png(baseline_png, patch_png)
        summary["patches_written"] += 1

    clean_repo(str(repo_dir), LOGGER)
    return summary


def current_blank_instances(result_root: Path) -> list[str]:
    items = []
    for inst_dir in sorted(result_root.iterdir()):
        if not inst_dir.is_dir():
            continue
        image_dir = inst_dir / "IMAGE"
        pngs = sorted(image_dir.glob("*.png")) if image_dir.is_dir() else []
        if not pngs:
            items.append(inst_dir.name)
            continue
        all_blank = True
        for png in pngs:
            try:
                from PIL import Image, ImageStat

                image = Image.open(png).convert("RGB")
                stat = ImageStat.Stat(image)
                mean = sum(stat.mean) / 3
                std = sum(stat.stddev) / 3
                if not (mean > 254.9 and std < 0.1):
                    all_blank = False
                    break
            except Exception:
                continue
        if all_blank:
            items.append(inst_dir.name)
    return items


def rerun_images_main(argv: Optional[List[str]] = None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--result-root", default=str(DEFAULT_RESULT_ROOT))
    parser.add_argument("--repo-root", default=str(DEFAULT_SCENARIO_ROOT))
    parser.add_argument("--dataset-json", default=str(DEFAULT_DATASET_JSON))
    parser.add_argument(
        "--instances",
        default="blank",
        help='Comma-separated instance ids, "all", or "blank" (default).',
    )
    parser.add_argument("--summary-json", default=str(DEFAULT_RERUN_SUMMARY_JSON))
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(levelname)s - %(name)s - %(message)s",
    )

    ensure_generated_root()
    result_root = Path(args.result_root)
    repo_root = Path(args.repo_root)
    dataset_dict = read_json(Path(args.dataset_json))

    if args.instances == "all":
        instance_ids = sorted([p.name for p in result_root.iterdir() if p.is_dir()])
    elif args.instances == "blank":
        instance_ids = current_blank_instances(result_root)
    else:
        instance_ids = [part.strip() for part in args.instances.split(",") if part.strip()]

    summaries = []
    for instance_id in instance_ids:
        LOGGER.info("rerun chartjs screenshots: %s", instance_id)
        summary = rerun_one_instance(instance_id, result_root, repo_root, dataset_dict)
        summaries.append(summary)
        LOGGER.info("summary %s", summary)

    summary_path = Path(args.summary_json)
    summary_path.parent.mkdir(parents=True, exist_ok=True)
    summary_path.write_text(json.dumps(summaries, indent=2, ensure_ascii=False), encoding="utf-8")
    print(summary_path)

def dhash_value(image_path: Path, size: int = 8) -> int:
    image = Image.open(image_path).convert("L").resize((size + 1, size))
    pixels = list(image.getdata())
    rows = [pixels[i * (size + 1):(i + 1) * (size + 1)] for i in range(size)]
    bits = []
    for row in rows:
        for idx in range(size):
            bits.append(1 if row[idx] > row[idx + 1] else 0)
    value = 0
    for bit in bits:
        value = (value << 1) | bit
    return value


def hamming(a: int, b: int) -> int:
    return (a ^ b).bit_count()


def image_size(path: Path):
    image = Image.open(path)
    return image.size


def is_readable_image(path: Path) -> bool:
    try:
        with Image.open(path) as image:
            image.verify()
        return True
    except (UnidentifiedImageError, OSError, ValueError):
        return False


def audit_bug_vs_baseline_main(argv: Optional[List[str]] = None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--scenario-root", default=str(DEFAULT_SCENARIO_ROOT))
    parser.add_argument("--result-root", default=str(DEFAULT_RESULT_ROOT))
    parser.add_argument("--output-json", default=str(DEFAULT_AUDIT_JSON))
    args = parser.parse_args(argv)

    ensure_generated_root()
    scenario_root = Path(args.scenario_root)
    result_root = Path(args.result_root)
    rows = []

    for inst_dir in sorted(p for p in scenario_root.iterdir() if p.is_dir()):
        image_candidates = [path for path in sorted((inst_dir / "IMAGE").glob("*.png")) if is_readable_image(path)]
        baseline_png = result_root / inst_dir.name / "IMAGE" / "0.png"
        if not image_candidates or not baseline_png.is_file():
            continue

        original_png = image_candidates[0]
        original_hash = dhash_value(original_png)
        baseline_hash = dhash_value(baseline_png)
        rows.append(
            {
                "instance_id": inst_dir.name,
                "original_image": str(original_png),
                "baseline_image": str(baseline_png),
                "original_size": image_size(original_png),
                "baseline_size": image_size(baseline_png),
                "dhash_distance": hamming(original_hash, baseline_hash),
            }
        )

    rows.sort(key=lambda item: (-item["dhash_distance"], item["instance_id"]))
    output_path = Path(args.output_json)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(rows, indent=2, ensure_ascii=False), encoding="utf-8")
    print(output_path)

QUARANTINE_LOGGER = logging.getLogger("chartjs_quarantine_fallback_templates")


def detect_code_source(instance_id: str, output_dir: Path, problem_statement: str) -> str:
    args = SimpleNamespace(
        output_dir=str(output_dir),
        repo_path="",
        dataset_split="",
    )
    _, source = load_chartjs_reproduce_code(instance_id, args, QUARANTINE_LOGGER)
    if source:
        return source
    fallback_code, _ = extract_first_markdown_code_block(problem_statement or "")
    if fallback_code:
        return "problem_statement"
    return "template_default"


def move_image_dir(instance_id: str, image_dir: Path, quarantine_root: Path) -> tuple[bool, str]:
    if not image_dir.is_dir():
        return False, ""

    target_dir = quarantine_root / instance_id / "IMAGE"
    target_dir.parent.mkdir(parents=True, exist_ok=True)
    if target_dir.exists():
        shutil.rmtree(target_dir)
    shutil.move(str(image_dir), str(target_dir))
    return True, str(target_dir)


def write_marker(instance_dir: Path, payload: dict):
    marker_path = instance_dir / "EXCLUDED_fallback_template.json"
    marker_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")


def quarantine_fallback_templates_main(argv: Optional[List[str]] = None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--result-root", default=str(DEFAULT_RESULT_ROOT))
    parser.add_argument("--dataset-json", default=str(DEFAULT_DATASET_JSON))
    parser.add_argument("--quarantine-root", default=str(DEFAULT_QUARANTINE_ROOT))
    parser.add_argument("--audit-json", default=str(DEFAULT_FALLBACK_TEMPLATE_AUDIT_JSON))
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(levelname)s - %(name)s - %(message)s",
    )

    ensure_generated_root()
    result_root = Path(args.result_root)
    quarantine_root = Path(args.quarantine_root)
    dataset = read_json(Path(args.dataset_json))

    audit_rows = []
    for instance_dir in sorted(p for p in result_root.iterdir() if p.is_dir()):
        instance_id = instance_dir.name
        image_dir = instance_dir / "IMAGE"
        png_count = len(list(image_dir.glob("*.png"))) if image_dir.is_dir() else 0
        problem_statement = str((dataset.get(instance_id) or {}).get("problem_statement", "") or "")
        code_source = detect_code_source(instance_id, instance_dir, problem_statement)
        row = {
            "instance_id": instance_id,
            "code_source": code_source,
            "fallback_template": code_source == "template_default",
            "png_count": png_count,
            "quarantined": False,
            "quarantine_path": "",
        }

        if row["fallback_template"] and image_dir.is_dir() and png_count > 0:
            moved, quarantine_path = move_image_dir(instance_id, image_dir, quarantine_root)
            row["quarantined"] = moved
            row["quarantine_path"] = quarantine_path
            if moved:
                marker_payload = {
                    "instance_id": instance_id,
                    "reason": "fallback_template",
                    "code_source": code_source,
                    "quarantine_path": quarantine_path,
                    "moved_at": datetime.now(timezone.utc).isoformat(),
                }
                write_marker(instance_dir, marker_payload)
                QUARANTINE_LOGGER.info("Quarantined fallback template images: %s -> %s", instance_id, quarantine_path)

        audit_rows.append(row)

    audit_path = Path(args.audit_json)
    audit_path.parent.mkdir(parents=True, exist_ok=True)
    audit_path.write_text(json.dumps(audit_rows, indent=2, ensure_ascii=False), encoding="utf-8")
    print(audit_path)


def main() -> int:
    argv = sys.argv[1:]
    command = argv[0] if argv else 'rerun-images'
    sub_argv = argv[1:] if argv else []

    if command in {'rerun-images', 'rerun'}:
        rerun_images_main(sub_argv)
        return 0
    if command in {'audit-bug-vs-baseline', 'audit'}:
        audit_bug_vs_baseline_main(sub_argv)
        return 0
    if command in {'quarantine-fallback-templates', 'quarantine'}:
        quarantine_fallback_templates_main(sub_argv)
        return 0

    print(
        'Usage: chartjs_helpers.py [rerun-images|audit-bug-vs-baseline|quarantine-fallback-templates] [args...]',
        file=sys.stderr,
    )
    return 2


if __name__ == '__main__':
    raise SystemExit(main())
