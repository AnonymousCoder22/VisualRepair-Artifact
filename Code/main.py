import os
import json
import logging
import argparse
from contextlib import contextmanager
from typing import cast
from datasets import Dataset, load_dataset

from Code.workflow import file_level_locating_val
from Code.model_utils import is_claude_model


logging.basicConfig(format = '%(asctime)s - %(levelname)s - %(name)s -   %(message)s',
                    datefmt = '%m/%d/%Y %H:%M:%S',
                    level = logging.INFO)
logger = logging.getLogger(__name__)


def redact_secret(value: str) -> str:
    if not value:
        return ""
    return "***REDACTED***"


def resolve_project_path(path_value: str) -> str:
    if not path_value:
        return path_value
    if os.path.isabs(path_value):
        return os.path.normpath(path_value)
    project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    return os.path.normpath(os.path.join(project_root, path_value))


def args_for_logging(args: argparse.Namespace) -> dict:
    data = vars(args).copy()
    if "openai_api_key" in data:
        data["openai_api_key"] = redact_secret(str(data.get("openai_api_key") or ""))
    if "claude_api_key" in data:
        data["claude_api_key"] = redact_secret(str(data.get("claude_api_key") or ""))
    return data


def split_instance_selectors(raw: str) -> list[str]:
    parts = str(raw or "").replace("\r", " ").replace("\n", " ").replace(",", " ").split()
    return [part.strip() for part in parts if part.strip()]


def resolve_instance_targets(instance_selector_raw: str, dataset_dict: dict) -> list[str]:
    selectors = split_instance_selectors(instance_selector_raw)
    if not selectors:
        return []

    resolved: list[str] = []
    seen: set[str] = set()
    all_instance_ids = list(dataset_dict.keys())

    for selector in selectors:
        if selector == "ALL":
            matches = all_instance_ids
        elif selector in dataset_dict:
            matches = [selector]
        else:
            matches = [instance_id for instance_id in all_instance_ids if selector in instance_id]
            if not matches:
                raise ValueError(f"instance selector {selector!r} matched 0 dataset instances")

        for instance_id in matches:
            if instance_id in seen:
                continue
            seen.add(instance_id)
            resolved.append(instance_id)

    return resolved


@contextmanager
def instance_log_handler(instance_output_dir: str, log_file: str):
    if not log_file:
        yield
        return

    log_path = os.path.join(instance_output_dir, log_file)
    handler = logging.FileHandler(log_path, encoding="utf-8")
    handler.setFormatter(logging.Formatter(
        '%(asctime)s - %(levelname)s - %(name)s -   %(message)s',
        datefmt='%m/%d/%Y %H:%M:%S',
    ))
    logger.addHandler(handler)
    try:
        yield
    finally:
        logger.removeHandler(handler)
        handler.close()


def get_args():
    parser = argparse.ArgumentParser()

    
    parser.add_argument("--task", type=str, default="run")

    parser.add_argument("--output_dir", type=str, required=True)
    parser.add_argument("--output_file", type=str, default="loc_outputs.jsonl")
    parser.add_argument("--log_file", type=str, default="loc_outputs.log")

    parser.add_argument("--base_model", type=str, default="gpt-4o-mini-2024-07-18")
    parser.add_argument("--top_n", type=int, default=3)
    parser.add_argument("--temperature", type=float, default=0.0)
    parser.add_argument("--num_samples", type=int, default=1)
    parser.add_argument("--all_bug_file_temperature", type=float, default=0.7)
    parser.add_argument("--all_bug_file_samples", type=int, default=2)
    parser.add_argument("--max_candidate_bug_files", type=int, default=4)
    parser.add_argument("--key_bug_file_temperature", type=float, default=0.0)
    parser.add_argument("--key_bug_file_samples", type=int, default=1) 
    parser.add_argument("--key_bug_class_function_temperature", type=float, default=0.0)
    parser.add_argument("--key_bug_class_function_samples", type=int, default=1)
    parser.add_argument("--line_level_fl_temperature", type=float, default=0.0)
    parser.add_argument("--line_level_fl_samples", type=int, default=1)
    parser.add_argument("--patch_generation_temperature", type=float, default=0.0)
    parser.add_argument("--patch_generation_samples", type=int, default=1) 

    parser.add_argument("--bug_keywords_temperature", type=float, default=0.0)
    parser.add_argument("--bug_keywords_samples", type=int, default=1) 

    parser.add_argument("--bug_docs_temperature", type=float, default=0.0)
    parser.add_argument("--bug_docs_samples", type=int, default=1) 

    parser.add_argument("--max_candidate_doc_files", type=int, default=6)
    
    parser.add_argument("--code_reproduce_temperature", type=float, default=0.0)
    parser.add_argument("--code_reproduce_samples", type=int, default=1) 

    parser.add_argument("--claude_api_key", type=str, default="")
    parser.add_argument("--claude_base_url", type=str, default="")



    parser.add_argument("--compress_assign", action="store_true")
    parser.add_argument("--compress_assign_total_lines", type=int, default=30)
    parser.add_argument("--compress_assign_prefix_lines", type=int, default=10)
    parser.add_argument("--compress_assign_suffix_lines", type=int, default=10)
    parser.add_argument("--context_window", type=int, default=10)

    parser.add_argument("--max_lines_per_snippet", type=int, default=500)
    parser.add_argument("--max_lines_per_key_file", type=int, default=500)
    
    parser.add_argument("--wait_time_after_build", type=int, default=0)
    parser.add_argument("--wait_time_after_api_request", type=int, default=20)
    parser.add_argument("--val_patch_no", type=str, default="1")
    
    parser.add_argument("--keywords_searching", default=False)
    parser.add_argument("--doc_RAG_query", default=False)
    parser.add_argument("--RAG_query", default=False)
    parser.add_argument("--Checked_Exception", default=False)

    parser.add_argument("--Patch_Check", default=False)
    parser.add_argument("--Patch_Select", default=False)
    parser.add_argument("--Overwrite_Code0", default=False)
    parser.add_argument("--Variant_Gen_By_LLM", default=False)
    parser.add_argument("--Variant_LLM_Only", default=False)
    parser.add_argument("--Variant_Max_Chars", type=int, default=120000)
    parser.add_argument("--Code_Reply", default=False)
    parser.add_argument("--File_Sort", default=False)

    # dataset info
    parser.add_argument("--dataset", type=str, default="princeton-nlp/SWE-bench_Multimodal")
    parser.add_argument("--dataset_split", type=str, default="dev")
    parser.add_argument("--instance_id", type=str, default="chartjs__Chart.js-8868")
    parser.add_argument("--repo_path", type=str, default="SWE-Bench-MM/Reproduce_Scenario")

    parser.add_argument(
        "--mock", action="store_true", help="Mock run to compute prompt tokens."
    )

    args = parser.parse_args()
    args.openai_api_key = ""
    args.openai_base_url = ""
    return args

def read_json_file(file_path):
    with open(file_path, 'r') as file:
        return json.load(file)

def save_json_file(file_path, data):
    with open(file_path, 'w') as json_file:
        json.dump(data, json_file, indent=4) 

def get_dataset():
    
    dataset_dict = {}
    dataset_json_file_path = os.path.join('dataset', args.dataset_split, (args.dataset).split('/')[1]+'.json')
    if not os.path.exists(os.path.join('dataset', args.dataset_split)):
        os.makedirs(os.path.join('dataset', args.dataset_split))
        

    if os.path.isfile(dataset_json_file_path):
        dataset_dict = read_json_file(dataset_json_file_path)
    else:
        dataset = cast(Dataset, load_dataset(args.dataset, split=args.dataset_split))
        for instance in dataset:     
            instance_id = instance['instance_id']
            dataset_dict[instance_id] = instance
        save_json_file(dataset_json_file_path, dataset_dict)
    
    return dataset_dict

def fault_localization_process(instance_id, instance_info):
    repo = instance_info['repo']
    instance_repo_path = os.path.join(args.repo_path, args.dataset_split, instance_id.split('__')[0], instance_id, 'REPO', repo.split('/')[-1])
    if os.path.exists(instance_repo_path):
        logger.info(f'Open Instance Repo {repo} Path: {instance_repo_path}')
    else:
        logger.info(f'Instance Repo {repo} Path is not exist: {instance_repo_path}, Plese checkout repo')
        return False
    
    
    # logger.info(f"")

    # root_output_dir = deep
    args.output_dir = os.path.join(original_output_dir, args.dataset_split, instance_id.split('__')[0], instance_id)

    if not os.path.exists(args.output_dir):
        os.makedirs(args.output_dir)

    with instance_log_handler(args.output_dir, args.log_file):
        diff_file = os.path.join(args.output_dir, 'changes.diff')
        if args.task == 'run' and args.Code_Reply == 'False':
            if os.path.isfile(diff_file):
                pass
            else:
                located_bug_files_result = file_level_locating_val(instance_id, instance_repo_path, instance_info, args, logger)
        elif args.task == 'run' and args.Code_Reply == 'True':
            if os.path.isfile(diff_file):
                pass
            else:
                located_bug_files_result = file_level_locating_val(instance_id, instance_repo_path, instance_info, args, logger)
        elif args.task == 'val':
            located_bug_files_result = file_level_locating_val(instance_id, instance_repo_path, instance_info, args, logger)

        else:
            if os.path.isfile(diff_file):
                pass
            else:
                located_bug_files_result = file_level_locating_val(instance_id, instance_repo_path, instance_info, args, logger)


    

    





def main():
    dataset_dict = get_dataset()
    variant_failures_all = {}

    uses_claude = is_claude_model(args.base_model)
    has_openai_key = bool(args.openai_api_key or os.getenv("OPENAI_API_KEY"))
    has_claude_key = bool(
        args.claude_api_key
        or os.getenv("ANTHROPIC_API_KEY")
        or os.getenv("CLAUDE_API_KEY")
    )

    if uses_claude and not has_claude_key:
        logger.info('Please set your Claude API Key (e.g. ANTHROPIC_API_KEY or --claude_api_key)')
        return
    if (not uses_claude) and (not has_openai_key):
        logger.info('Please set OPENAI_API_KEY in the environment')
        return

    try:
        instance_targets = resolve_instance_targets(args.instance_id, dataset_dict)
    except ValueError as exc:
        logger.info(str(exc))
        return

    if not instance_targets:
        logger.info('Please give the correct instance ID')
        return

    if len(instance_targets) == len(dataset_dict):
        logger.info('Repair all instances...')
    elif len(instance_targets) == 1:
        logger.info('Repair one instance ID...')

    for index, instance_id in enumerate(instance_targets, start=1):
        if len(instance_targets) > 1:
            logger.info("")
            logger.info(f"================ {instance_id} ================")
            logger.info("")
            logger.info(f'Instance No: {index}/{len(instance_targets)}, ID: {instance_id}')
        else:
            logger.info(f'Repair Instance ID: {instance_id}')

        fault_localization_process(instance_id, dataset_dict[instance_id])
        instance_output_dir = os.path.join(original_output_dir, args.dataset_split, instance_id.split('__')[0], instance_id)
        vf = os.path.join(instance_output_dir, "variant_gen_failures.json")
        if os.path.isfile(vf):
            try:
                variant_failures_all[instance_id] = read_json_file(vf)
            except Exception:
                pass

    if variant_failures_all:
        instances_file_too_large = []
        for iid, payload in variant_failures_all.items():
            try:
                failures = payload.get("failures") if isinstance(payload, dict) else None
                if isinstance(failures, list) and any((f.get("reason") == "file_too_large") for f in failures if isinstance(f, dict)):
                    instances_file_too_large.append(iid)
            except Exception:
                pass
        try:
            save_json_file(
                os.path.join(original_output_dir, "variant_gen_failures_all.json"),
                {
                    "dataset_split": args.dataset_split,
                    "variant_gen_by_llm": args.Variant_Gen_By_LLM,
                    "variant_llm_only": args.Variant_LLM_Only,
                    "variant_max_chars": args.Variant_Max_Chars,
                    "instances_file_too_large": instances_file_too_large,
                    "instances": variant_failures_all,
                },
            )
        except Exception:
            pass


if __name__ == '__main__':
    args = get_args()
    args.output_dir = resolve_project_path(args.output_dir)

    # OpenAI connection settings are sourced from env vars only.
    args.openai_api_key = os.getenv("OPENAI_API_KEY") or ""
    args.openai_base_url = os.getenv("OPENAI_BASE_URL") or os.getenv("OPENAI_API_BASE") or ""
    if not args.claude_api_key:
        args.claude_api_key = os.getenv("CLAUDE_API_KEY") or os.getenv("ANTHROPIC_API_KEY") or ""
    if not args.claude_base_url:
        args.claude_base_url = (
            os.getenv("ANTHROPIC_BASE_URL")
            or os.getenv("CLAUDE_BASE_URL")
            or ""
        )

    logger.info(args_for_logging(args))
    
    original_output_dir = args.output_dir
    main()
