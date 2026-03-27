import os
import json
import subprocess
import re
import copy
import locale
import time
from difflib import SequenceMatcher
from build_cmd import make_build_cmd, make_check_cmd


import logging

logging.basicConfig(format = '%(asctime)s - %(levelname)s - %(name)s -   %(message)s',
                    datefmt = '%m/%d/%Y %H:%M:%S',
                    level = logging.INFO)
logger = logging.getLogger(__name__)


def preferred_text_encodings():
    encodings = ['utf-8', 'utf-8-sig']
    preferred = locale.getpreferredencoding(False)
    if preferred and preferred not in encodings:
        encodings.append(preferred)
    return encodings


def read_file(file_path):
    with open(file_path, 'r', encoding='utf-8', errors='ignore') as file:
        return file.read()

def read_file_lines(file_path):
    with open(file_path, 'r', encoding='utf-8', errors='ignore') as file:
        return file.readlines()

def save_file(file_path, data, encoding='utf-8'):
    """Save data to a file with the specified encoding."""
    try:
        with open(file_path, 'w', encoding=encoding) as file:
            file.write(data)
    except UnicodeEncodeError as e:
        print(f"Unicode encode error: {e}")
        return "Unicode encode error"

def read_json_file(file_path):
    for encoding in preferred_text_encodings():
        try:
            with open(file_path, 'r', encoding=encoding) as json_file:
                return json.load(json_file)
        except UnicodeDecodeError:
            continue
    raise UnicodeDecodeError('unknown', b'', 0, 1, f'Failed to decode JSON file: {file_path}')



def contains_chinese(obj):
    """check if contrain chinese string"""
    if isinstance(obj, str):
        return bool(re.search(r'[\u4e00-\u9fff]', obj))  
    elif isinstance(obj, dict):
        return any(contains_chinese(value) for value in obj.values())  
    elif isinstance(obj, (list, tuple, set)):
        return any(contains_chinese(item) for item in obj)  
    return False  

def save_json_file(file_path, data):
    """save JSON, if contrain chinese string >>> ensure_ascii=False"""
    contains_cn = contains_chinese(data)  
    with open(file_path, 'w', encoding='utf-8') as json_file:
        json.dump(data, json_file, indent=4, ensure_ascii=not contains_cn)  

def is_process_running(pid):
    if not isinstance(pid, int) or pid <= 0:
        return False

    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True

def acquire_script_lock(lock_file_path, logger):
    current_pid = os.getpid()

    if os.path.exists(lock_file_path):
        lock_pid = None
        try:
            lock_info = read_json_file(lock_file_path)
            lock_pid = int(lock_info.get("pid", 0))
        except Exception:
            lock_info = {}

        if lock_pid and is_process_running(lock_pid):
            raise RuntimeError(
                f"Another changes.py process is already running. "
                f"pid={lock_pid}, lock_file={lock_file_path}"
            )

        logger.info(f"Remove stale script lock: {lock_file_path}")
        os.remove(lock_file_path)

    with open(lock_file_path, 'x', encoding='utf-8') as lock_file:
        json.dump({
            "pid": current_pid,
            "script": os.path.abspath(__file__),
            "started_at": time.time()
        }, lock_file, indent=2)

    return lock_file_path

def release_script_lock(lock_file_path):
    if not lock_file_path or not os.path.exists(lock_file_path):
        return

    try:
        lock_info = read_json_file(lock_file_path)
        lock_pid = int(lock_info.get("pid", 0))
    except Exception:
        lock_pid = os.getpid()

    if lock_pid != os.getpid():
        return

    os.remove(lock_file_path)

def resolve_git_dir(repo_path):
    git_path = os.path.join(repo_path, '.git')
    if os.path.isdir(git_path):
        return git_path

    if os.path.isfile(git_path):
        git_ref = read_file(git_path).strip()
        if git_ref.startswith('gitdir:'):
            git_dir = git_ref.split(':', 1)[1].strip()
            if not os.path.isabs(git_dir):
                git_dir = os.path.abspath(os.path.join(repo_path, git_dir))
            return git_dir

    return None

def get_git_lock_files(repo_path):
    git_dir = resolve_git_dir(repo_path)
    if not git_dir:
        return []

    lock_files = []
    for lock_name in ('index.lock', 'HEAD.lock'):
        lock_file = os.path.join(git_dir, lock_name)
        if os.path.exists(lock_file):
            lock_files.append(lock_file)
    return lock_files

def clear_stale_git_locks(repo_path, logger, stale_seconds=30):
    removed_lock_files = []
    now = time.time()

    for lock_file in get_git_lock_files(repo_path):
        lock_age_seconds = max(0, now - os.path.getmtime(lock_file))
        if lock_age_seconds < stale_seconds:
            logger.info(f"Git lock still fresh, keep it: {lock_file} (age={lock_age_seconds:.1f}s)")
            continue

        os.remove(lock_file)
        removed_lock_files.append(lock_file)
        logger.info(f"Removed stale git lock: {lock_file} (age={lock_age_seconds:.1f}s)")

    return removed_lock_files

def decode_command_output(raw_output):
    if not raw_output:
        return ""

    if isinstance(raw_output, str):
        return raw_output

    for encoding in preferred_text_encodings():
        try:
            return raw_output.decode(encoding)
        except UnicodeDecodeError:
            continue
    return raw_output.decode('utf-8', errors='replace')

def run_command(command, cwd=None):
    """Execute a shell command and return the result."""
    try:
        result = subprocess.run(
            command,
            cwd=cwd,
            shell=True,
            capture_output=True,
            text=False
        )
        stdout = decode_command_output(result.stdout).strip()
        stderr = decode_command_output(result.stderr).strip()
        if result.returncode != 0:
            # print(f"Run Command {command} has Error: {result.stderr} {result.stdout.strip()}")
            return f"Run Command {command} has Error: {stderr} {stdout}"
        return stdout
    except UnicodeDecodeError as e:
        print(f"Unicode decode error: {e}")
        return "Unicode decode error"

def run_git_command(command, instance_repo_path, logger):
    clear_stale_git_locks(instance_repo_path, logger)
    command_result = run_command(command)

    if 'Another git process seems to be running' in command_result:
        lock_files = get_git_lock_files(instance_repo_path)
        if lock_files:
            logger.info(f'Git lock files blocking repo: {lock_files}')
        else:
            logger.info(f'Git reported a lock conflict, but no lock file was found: {instance_repo_path}')

    return command_result


def clean_repo(instance_repo_path, logger):
    # clean changed files
    if 'chartjs__Chart.js-10806' in instance_repo_path or 'chartjs__Chart.js-11116' in instance_repo_path \
        or 'carbon-design-system' in instance_repo_path or 'PrismJS' in instance_repo_path:
        create_tmp_file = run_command(f'cd {instance_repo_path} && cd ../.. && mkdir -p tmp')
        clean_result = run_git_command(
            f'cd {instance_repo_path} && cp package.json ../../tmp/package.json && git reset --hard && cp ../../tmp/package.json ./package.json',
            instance_repo_path,
            logger
        )
        logger.info(f'Do not Clean Repo Package.json')
    elif 'openlayers' in instance_repo_path:
        create_tmp_file = run_command(f'cd {instance_repo_path} && cd ../.. && mkdir -p tmp')
        clean_result = run_git_command(
            f'cd {instance_repo_path} && cp config/tsconfig-build.json ../../tmp/tsconfig-build.json && git reset --hard && cp ../../tmp/tsconfig-build.json config/tsconfig-build.json',
            instance_repo_path,
            logger
        )
        logger.info(f'Do not Clean Repo config')
    else:
        clean_result = run_git_command(f'cd {instance_repo_path} && git reset --hard', instance_repo_path, logger)
        logger.info(f'Repo Clean All Files')
    logger.info(f'Repo Clean Result: {clean_result}')
    return clean_result


def get_bug_file_structure_dict(bug_file, repo_structure_dict):
    bug_file_structure_dict = repo_structure_dict
    for key in bug_file.split('/'):

        bug_file_structure_dict = bug_file_structure_dict[key]

    return bug_file_structure_dict

def back_fill_patch(patch_edits, instance_repo_path, repo_structure_dict, logger):
    for bug_file in patch_edits.keys():
        logger.info(f'Backfill: {bug_file}')
        patch_edits_list = patch_edits[bug_file]

        bug_file_path = os.path.join(instance_repo_path, bug_file)
        
        try:
            bug_file_structure_dict = get_bug_file_structure_dict(bug_file, repo_structure_dict)
            bug_file_content = bug_file_structure_dict['text']
            logger.info('get Backfill src file')
        except:
            if os.path.exists(bug_file_path):
                bug_file_content = [line.rstrip("\n") for line in read_file_lines(bug_file_path)]
                logger.info(f'get not exist Backfill src file: {bug_file_path}')
            else:
                logger.info(f'Backfill Bug File path not exist: {bug_file}')
                continue

        pre_file_content = pre2bug(patch_edits_list, bug_file_content, bug_file_path, logger) # backfill predicted patch to bug file, get the pre file

def pre2bug(patch_edits_list, bug_file_content, bug_file_path, logger):

    pre_file_content = copy.deepcopy(bug_file_content)
    positions = []
    
    # First：find all patch edit locations
    # Iterate over each patch edit
    for patch_edit in patch_edits_list:
        search_lines = patch_edit["SEARCH"]
        replace_lines = patch_edit["REPLACE"]
        back_fill_result = 'Fail'
        # Find the start index of the search lines in the bug file content
        for i in range(len(bug_file_content) - len(search_lines) + 1):
            if [line.strip() for line in bug_file_content[i:i + len(search_lines)]] == search_lines:
                # Replace the search lines with the replace lines
                # pre_file_content[i:i + len(search_lines)] = replace_lines
                positions.append((i, patch_edit))
                # logger.info('A Pre2Bug Success')
                back_fill_result = 'Success'
                break
        
        if back_fill_result == 'Fail':
            for i in range(len(bug_file_content) - len(search_lines) + 1):
                if [line.replace('\\','').strip() for line in bug_file_content[i:i + len(search_lines)]] == [line.replace('\\','').strip() for line in search_lines]:
                    # Replace the search lines with the replace lines
                    # pre_file_content[i:i + len(search_lines)] = replace_lines
                    positions.append((i, patch_edit))
                    # logger.info('A Pre2Bug Success (remove \\)')
                    back_fill_result = 'Success (remove \\)'
                    break

        if back_fill_result == 'Fail':
            for i in range(len(bug_file_content) - len(search_lines) + 1):    
                search_str = '\n'.join([line.strip() for line in search_lines])
                replace_str = '\n'.join([line.strip() for line in bug_file_content[i:i + len(search_lines)]])
                similarity = SequenceMatcher(None, search_str, replace_str).ratio()
                if similarity >= 0.9:
                    # Replace the search lines with the replace lines
                    # pre_file_content[i:i + len(search_lines)] = replace_lines
                    positions.append((i, patch_edit))
                    # logger.info('A Pre2Bug Success (remove \\)')
                    back_fill_result = f'Success (similarity {similarity})'
                    break
        
        if back_fill_result == 'Fail':
            for i in range(len(bug_file_content) - len(search_lines) + 1): 
                if [line.strip() for line in bug_file_content[i:i + len(search_lines)] if '\\' not in line] == [line.strip() for line in search_lines if '\\' not in line]:
                    # Replace the search lines with the replace lines
                    # pre_file_content[i:i + len(search_lines)] = replace_lines
                    positions.append((i, patch_edit))
                    back_fill_result = 'Success (skip \\)'
                    break
            

        logger.info(f'A Pre2Bug {back_fill_result}')

    # Second：Relace all edit postions
    for i, patch_edit in sorted(positions, key=lambda x: x[0], reverse=True):
        search_lines = patch_edit["SEARCH"]
        replace_lines = patch_edit["REPLACE"]

        pre_file_content[i:i + len(search_lines)] = replace_lines
        logger.info(f"Patch applied successfully at line {i}")

    pre_file_content = "\n".join(pre_file_content)
    save_file(bug_file_path, pre_file_content+"\n")

    return pre_file_content

def patch_validation_xjy(patches_edits_dict, instance_repo_path, repo_structure_dict, logger, args):

    if os.path.exists(os.path.join(args.output_dir, '5-2_patches_val_flag.json')):
        logger.info(f'Exist Validation Flag File')
        return

    run_command(f'cd {instance_repo_path} && npm install')
    patch_val_res = {}

    for sample_no in patches_edits_dict.keys():
        args.current_patch_no = sample_no.split('/')[0]

        patch_val_res[sample_no] = {
            "compile": False,
            "patch diff": ""
        }

        if float(sample_no.split('/')[0]) < float(args.val_patch_no):
            continue

        logger.info(f'Patch No. {sample_no}')

        # 1. clean repo
        clean_repo(instance_repo_path, logger)

        # 2. apply patch
        patch_edits = patches_edits_dict[sample_no]
        try:
            if float(args.val_patch_no) > 0:
                back_fill_patch(patch_edits, instance_repo_path, repo_structure_dict, logger)
                logger.info(f'Pre Backfill Finish.')
        except Exception as e:
            logger.info(f'Pre Backfill Fail: {e}')
            continue

        # 3. get patch diff
        diff_file_name = f'changes_{args.current_patch_no}.diff'
        diff_file_path = os.path.join(instance_repo_path, diff_file_name)

        run_command(
            f'cd {instance_repo_path} && git diff -- "*.js" "*.ts" "*.jsx" "*.tsx" "*.js.snap" "*.scss" "*.md" "*.lua" > {diff_file_name}'
        )

        # Read the generated diff.
        if os.path.exists(diff_file_path):
            patch_diff = read_file(diff_file_path)
        else:
            patch_diff = ""

        patch_val_res[sample_no]["patch diff"] = patch_diff

        # Remove the temporary diff file.
        run_command(f'cd {instance_repo_path} && rm -f {diff_file_name}')

        # Skip patches that did not produce any diff.
        if len(patch_diff.strip()) == 0:
            logger.info(f'Patch No. {sample_no} has no diff, skip.')
            continue

        # 4. build / test / lint
        build_cmd = make_build_cmd(instance_repo_path)
        build_response = 'Fail'

        try:
            build_result = run_command(f'cd {instance_repo_path} && {build_cmd}')
            logger.info(f'Build CMD: {build_cmd}')
            logger.info(f'Build Result:\n{build_result}')

            if 'SyntaxError' in build_result or 'TypeError' in build_result or 'has Error' in build_result:
                logger.info(f'Build Error')
            else:
                build_response = 'Success'

        except Exception as e:
            logger.info(f'Build Exception: {e}')

        # 5. compile result
        if build_response == 'Success':
            patch_val_res[sample_no]["compile"] = True
            logger.info(f'Patch {sample_no} Compile Success')
        else:
            logger.info(f'Patch {sample_no} Compile Fail')

        # reset repo
        run_command(f'cd {instance_repo_path} && git reset --hard')

    save_json_file(os.path.join(args.output_dir, '5-2_patches_val_flag.json'), patch_val_res)

def patch_diff_only(patches_edits_dict, instance_repo_path, repo_structure_dict, logger, args):

    diff_json_path = os.path.join(args.output_dir, '5-2_patches_diff.json')
    diff_save_dir = os.path.join(args.output_dir, "patch_diffs")

    os.makedirs(diff_save_dir, exist_ok=True)

    if os.path.exists(diff_json_path):
        logger.info(f'Exist Diff JSON File')
        return

    patch_diff_res = {}

    for sample_no in patches_edits_dict.keys():
        args.current_patch_no = sample_no.split('/')[0]

        patch_diff_res[sample_no] = {
            "patch diff": "",
            "diff file": ""
        }

        logger.info(f'Patch No. {sample_no}')

        # 1. clean repo
        clean_repo(instance_repo_path, logger)

        # 2. apply patch (SEARCH → REPLACE)
        patch_edits = patches_edits_dict[sample_no]
        try:
            back_fill_patch(patch_edits, instance_repo_path, repo_structure_dict, logger)
            logger.info(f'Backfill Finish.')
        except Exception as e:
            logger.info(f'Backfill Fail: {e}')
            continue

        # 3. generate git diff
        diff_file_name = f'changes_{args.current_patch_no}.diff'
        diff_file_path = os.path.join(instance_repo_path, diff_file_name)

        run_command(
            f'cd {instance_repo_path} && git diff -- "*.js" "*.ts" "*.jsx" "*.tsx" "*.js.snap" "*.scss" "*.md" "*.lua" > {diff_file_name}'
        )

        # 4. read diff
        if os.path.exists(diff_file_path):
            patch_diff = read_file(diff_file_path)
            patch_diff_res[sample_no]["patch diff"] = patch_diff

            # 5. move diff file to output_dir/patch_diffs
            target_diff_path = os.path.join(diff_save_dir, diff_file_name)
            run_command(f'mv {diff_file_path} {target_diff_path}')

            patch_diff_res[sample_no]["diff file"] = target_diff_path

            logger.info(f'Diff saved to: {target_diff_path}')
        else:
            logger.info(f'No diff generated for patch {sample_no}')
            patch_diff_res[sample_no]["patch diff"] = ""
            patch_diff_res[sample_no]["diff file"] = ""

        # 6. reset repo
        run_command(f'cd {instance_repo_path} && git reset --hard')

    # 7. save diff json
    save_json_file(diff_json_path, patch_diff_res)
    logger.info(f'Diff JSON saved to {diff_json_path}')


def run_patch_validation_all_instances(args, logger):
    project_root = os.path.normpath(os.path.join(os.path.dirname(__file__), ".."))
    result_root = os.path.join(project_root, "Code", "o3", "result", "test")
    reproduce_root = os.path.join(project_root, "Reproduce_Scenario", "test")

    for repo in os.listdir(result_root):
        repo_result_path = os.path.join(result_root, repo)
        if not os.path.isdir(repo_result_path):
            continue

        logger.info(f"\n===== Processing Repo: {repo} =====")

        for instance_id in os.listdir(repo_result_path):
            instance_result_path = os.path.join(repo_result_path, instance_id)
            if not os.path.isdir(instance_result_path):
                continue

            logger.info(f"\n--- Instance: {instance_id} ---")

            # -------------------------
            # 1. patches_edits_dict
            # -------------------------
            patches_edits_file = os.path.join(instance_result_path, "5-2_patches_edits.json")
            if not os.path.exists(patches_edits_file):
                logger.info("No 5-2_patches_edits.json, skip")
                continue
            patches_edits_dict = read_json_file(patches_edits_file)

            # -------------------------
            # 2. repo_structure_dict
            # -------------------------
            repo_structure_file = os.path.join(instance_result_path, "1-1_repo_structure.json")
            if not os.path.exists(repo_structure_file):
                logger.info("No repo_structure.json, skip")
                continue
            repo_structure_dict = read_json_file(repo_structure_file)

            # -------------------------
            # 3. instance_repo_path
            # -------------------------
            reproduce_instance_path = os.path.join(reproduce_root, repo, instance_id, "REPO")
            if not os.path.exists(reproduce_instance_path):
                logger.info("REPO path not found, skip")
                continue

            # The actual repository directory under REPO may be named next, eslint, chart.js, etc.
            repo_dirs = os.listdir(reproduce_instance_path)
            if len(repo_dirs) == 0:
                logger.info("Empty REPO dir, skip")
                continue

            real_repo_name = repo_dirs[0]
            instance_repo_path = os.path.join(reproduce_instance_path, real_repo_name)

            logger.info(f"Repo Path: {instance_repo_path}")

            # -------------------------
            # 4. output_dir
            # -------------------------
            args.output_dir = instance_result_path

            # -------------------------
            # 5. Run patch validation
            # -------------------------
            try:
                patch_diff_only(
                    patches_edits_dict,
                    instance_repo_path,
                    repo_structure_dict,
                    logger,
                    args
                )
            except Exception as e:
                logger.info(f"Patch Validation Error: {e}")


def main():
    class Args:
        pass

    args = Args()
    args.val_patch_no = 1
    lock_file_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'changes.py.lock')
    acquire_script_lock(lock_file_path, logger)

    try:
        run_patch_validation_all_instances(args, logger)
    finally:
        release_script_lock(lock_file_path)

if __name__ == '__main__':
    main()
