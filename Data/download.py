from datasets import Dataset, load_dataset
from typing import cast
import subprocess
import os
import json
import requests
import time
import re

github_token = ""  # Replace with your GitHub token

def run_command(command, cwd=None):
    result = subprocess.run(command, cwd=cwd, shell=True, capture_output=True, text=True)
    if result.returncode != 0:
        print(f"Error: {result.stderr}")
        exit(1)
    return result.stdout.strip()

def search_github_issues(repo, pull_id):
    # GitHub API URL for searching issues
    search_url = "https://api.github.com/search/issues"
    
    headers = {
        "Authorization": f"token {github_token}"
    }
    
    # Construct the search query
    query = f'repo:{repo} "{pull_id}"'
    
    params = {
        'q': query
    }
    
    response = requests.get(search_url, headers=headers, params=params)
    
    if response.status_code == 200:
        search_results = response.json()
        if search_results['total_count'] > 0:
            # Return the URL of the first matching issue
            return search_results['items'][0]['html_url']
        else:
            print("No matching issues found.")
    else:
        print("Failed to search issues:", response.status_code)
    
    return None


def read_json_file(file_path):
    with open(file_path, 'r') as file:
        return json.load(file)

def save_bug_info_file(bug_info_file_path, data):
    with open(bug_info_file_path, 'w') as json_file:
        json.dump(data, json_file, indent=4) 

def save_bug_reproduce_files(file_path):
    html_file_path = file_path + '/index.html'
    css_file_path = file_path + '/style.css'
    js_file_path = file_path + '/script.js'
    with open(html_file_path, 'w') as html_file, open(css_file_path, 'w') as css_file, open(js_file_path, 'w') as js_file:
        html_file.write("")
        css_file.write("")
        js_file.write("")

def save_issue_report_images(problem_statement, save_dir):
    """
    Extract image URLs from a GitHub issue description and download them to a specified directory.

    Args:
        problem_statement (str): The issue description containing image URLs.
        save_dir (str): Directory where images will be saved. Defaults to "images".
    """
    # Ensure the save directory exists
    os.makedirs(save_dir, exist_ok=True)

    if type(problem_statement) == str:
        # Regex to extract image URLs (handles both ![alt text](url) and plain URLs)
        img_url_pattern = r'!\[.*?\]\((https?://[^)]+\.(?:png|jpg|jpeg|gif|bmp|svg))\)'
        image_urls = re.findall(img_url_pattern, problem_statement)
    elif type(problem_statement) == list:
        image_urls = problem_statement

    if not image_urls:
        print("No image URLs found.")
        return

    # Download each image
    for idx, url in enumerate(image_urls, 1):
        try:
            response = requests.get(url, stream=True)
            response.raise_for_status()
            
            # Extract filename from URL
            filename = os.path.join(save_dir, url.split("/")[-1])
            
            # Save image
            with open(filename, "wb") as f:
                for chunk in response.iter_content(chunk_size=8192):
                    f.write(chunk)
            
            print(f"Downloaded: {filename}")
        except requests.exceptions.RequestException as e:
            print(f"Failed to download {url}: {e}")

def save_all_repo_scenario(split_dataset):
    dataset = cast(Dataset, load_dataset("princeton-nlp/SWE-bench_Multimodal", split=split_dataset))

    project_list = []
    all_bug_nums = 0

    for instance in dataset:
        # print(instance)
        # print(type(instance))
        all_bug_nums += 1
        print(all_bug_nums)
        project_id = instance['instance_id'].split('__')[0]
        # if project_id in ['grommet']:
        #     print(instance['instance_id'])
        #     continue
        # continue
        bug_id = instance['instance_id']
        bug_info_dict = instance

        problem_statement = instance['problem_statement']
        image_assets = instance['image_assets']

        repo = instance['repo']
        base_commit = instance['base_commit']
        pull_id = bug_id.split('-')[-1]
        # please find the pull link page
        pull_link = search_github_issues(repo, pull_id)
        print(pull_link)
        bug_info_dict["pull_url"] = pull_link
        bug_info_dict["issue_url"] = ''
        bug_info_dict["reproduce_url"] = ''
        bug_info_dict["interaction"] = ''
        

        os.makedirs(f"Reproduce_Scenario/{split_dataset}/{project_id}/{bug_id}/BUG", exist_ok=True)
        os.makedirs(f"Reproduce_Scenario/{split_dataset}/{project_id}/{bug_id}/FIX", exist_ok=True)
        os.makedirs(f"Reproduce_Scenario/{split_dataset}/{project_id}/{bug_id}/PRE", exist_ok=True)
        os.makedirs(f"Reproduce_Scenario/{split_dataset}/{project_id}/{bug_id}/IMAGE", exist_ok=True)

        bug_info_file_path = f"Reproduce_Scenario/{split_dataset}/{project_id}/{bug_id}/bug_info.json"

        save_bug_info_file(bug_info_file_path, bug_info_dict)
        save_bug_reproduce_files(f"Reproduce_Scenario/{split_dataset}/{project_id}/{bug_id}/BUG")

        save_issue_report_images(problem_statement, f"Reproduce_Scenario/{split_dataset}/{project_id}/{bug_id}/IMAGE")

        all_bug_nums += 1

        # please add a time wait 2s
        time.sleep(2)
        # break


    print(type(dataset))
    print(f'Have saved all {all_bug_nums} bug items of the SWE-Bench MultiModel test dataset.')

def save_all_repo_scenario_image(split_dataset):
    dataset = cast(Dataset, load_dataset("princeton-nlp/SWE-bench_Multimodal", split=split_dataset))

    project_list = []
    all_bug_nums = 0

    for instance in dataset:
        # print(instance)
        # print(type(instance))
        all_bug_nums += 1
        print(all_bug_nums)
        project_id = instance['instance_id'].split('__')[0]

        bug_id = instance['instance_id']
        bug_info_dict = instance

        problem_statement = instance['problem_statement']
        image_assets = json.loads(instance['image_assets'])
        # print(image_assets)
        # print(type(image_assets))

        repo = instance['repo']
        base_commit = instance['base_commit']
        pull_id = bug_id.split('-')[-1]

        os.makedirs(f"Reproduce_Scenario/{split_dataset}/{project_id}/{bug_id}/IMAGE", exist_ok=True)

        bug_info_file_path = f"Reproduce_Scenario/{split_dataset}/{project_id}/{bug_id}/bug_info.json"

        save_issue_report_images(image_assets['problem_statement'], f"Reproduce_Scenario/{split_dataset}/{project_id}/{bug_id}/IMAGE")

        # all_bug_nums += 1

        # please add a time wait 6s
        time.sleep(6)
        # break


    print(type(dataset))
    print(f'Have saved all {all_bug_nums} bug items of the SWE-Bench MultiModel test dataset.')


def look_all_repo_scenario(split_dataset):
    dataset = cast(Dataset, load_dataset("princeton-nlp/SWE-bench_Multimodal", split=split_dataset))

    project_list = []
    all_bug_nums = 0
    reproduce_url_nums = 0
    interaction_nums = 0

    for instance in dataset:
        # print(instance)
        # print(type(instance))

        project_id = instance['instance_id'].split('__')[0]
        bug_id = instance['instance_id']
        bug_info_dict = instance

        repo = instance['repo']
        base_commit = instance['base_commit']
        if repo in ['processing/p5.js']:
            pass
        else:
            continue
        # please find the pull link page
        # pull_link = search_github_issues(repo, pull_id)
        # print(pull_link)
        # bug_info_dict["pull_url"] = pull_link
        # bug_info_dict["issue_url"] = ''
        # bug_info_dict["reproduce_url"] = ''
        # bug_info_dict["interaction"] = ''

        bug_info_file_path = f"Reproduce_Scenario/{split_dataset}/{project_id}/{bug_id}/bug_info.json"

        bug_info_file_dict = read_json_file(bug_info_file_path)

        reproduce_url = bug_info_file_dict['reproduce_url']
        interaction = bug_info_file_dict['interaction']

        if 'http' in reproduce_url:
            reproduce_url_nums += 1
        if 'YES' in interaction:
            interaction_nums += 1

        all_bug_nums += 1

        # break

    # print(type(dataset))
    print(f'Have looked all {all_bug_nums} bug items of the SWE-Bench MultiModel test dataset.')
    print(f'There are {reproduce_url_nums} instances have reproduce url, and {interaction_nums} instances need interaction.')



def checkout_all_repo_scenario(split_dataset):
    dataset = cast(Dataset, load_dataset("princeton-nlp/SWE-bench_Multimodal", split=split_dataset))

    for instance in dataset:
        project_id = instance['instance_id'].split('__')[0]
        # if project_id in ['chartjs']:
        #     continue
        bug_id = instance['instance_id']
        bug_info_dict = instance

        repo = instance['repo']
        base_commit = instance['base_commit']

        
        os.makedirs(f"Reproduce_Scenario/{split_dataset}/{project_id}/{bug_id}/REPO", exist_ok=True)

        if 'Automattic' in project_id:
            # Configuring Code Repository Information
            REPO_URL = "https://github.com/Automattic/wp-calypso.git"  # GitHub Repo
            BASE_COMMIT = base_commit  # Target Base Commit
            CLONE_DIR = f"Reproduce_Scenario/{split_dataset}/{project_id}/{bug_id}/REPO"  # Local Dir
            CLONE_REPO_DIR = f"wp-calypso"

            print(f"{CLONE_DIR}")
            # please complete this command
            try:
                run_command(f"cd {CLONE_DIR} && git clone {REPO_URL} && cd {CLONE_REPO_DIR} && git checkout {BASE_COMMIT}")
            except:
                pass

        if 'chartjs' in project_id:
            # Configuring Code Repository Information
            REPO_URL = "https://github.com/chartjs/Chart.js.git"  # GitHub Repo
            BASE_COMMIT = base_commit  # Target Base Commit
            CLONE_DIR = f"Reproduce_Scenario/{split_dataset}/{project_id}/{bug_id}/REPO"  # Local Dir
            CLONE_REPO_DIR = f"Chart.js"

            print(f"{CLONE_DIR}")
            # please complete this command
            try:
                run_command(f"cd {CLONE_DIR} && git clone {REPO_URL} && cd {CLONE_REPO_DIR} && git checkout {BASE_COMMIT}")
            except:
                pass

        if 'diegomura' in project_id:
            # Configuring Code Repository Information
            REPO_URL = "https://github.com/diegomura/react-pdf.git"  # GitHub Repo
            BASE_COMMIT = base_commit  # Target Base Commit
            CLONE_DIR = f"Reproduce_Scenario/{split_dataset}/{project_id}/{bug_id}/REPO"  # Local Dir
            CLONE_REPO_DIR = f"react-pdf"

            print(f"{CLONE_DIR}")
            # please complete this command
            try:
                run_command(f"cd {CLONE_DIR} && git clone {REPO_URL} && cd {CLONE_REPO_DIR} && git checkout {BASE_COMMIT}")
            except:
                pass
        
        if 'processing' in project_id:
            # Configuring Code Repository Information
            REPO_URL = "https://github.com/processing/p5.js.git"  # GitHub Repo
            BASE_COMMIT = base_commit  # Target Base Commit
            CLONE_DIR = f"Reproduce_Scenario/{split_dataset}/{project_id}/{bug_id}/REPO"  # Local Dir
            CLONE_REPO_DIR = f"p5.js"

            print(f"{CLONE_DIR}")
            # please complete this command
            try:
                run_command(f"cd {CLONE_DIR} && git clone {REPO_URL} && cd {CLONE_REPO_DIR} && git checkout {BASE_COMMIT}")
            except:
                pass

            # you must run above command to build js repo:

            # mkdir -p docs/reference
            # echo "{}" > docs/reference/data.json
            
            # echo "{}" > docs/parameterData.json

        if 'markedjs' in project_id:
            # Configuring Code Repository Information
            REPO_URL = "https://github.com/markedjs/marked.git"  # GitHub Repo
            BASE_COMMIT = base_commit  # Target Base Commit
            CLONE_DIR = f"Reproduce_Scenario/{split_dataset}/{project_id}/{bug_id}/REPO"  # Local Dir
            CLONE_REPO_DIR = f"marked"

            print(f"{CLONE_DIR}")
            # please complete this command
            try:
                run_command(f"cd {CLONE_DIR} && git clone {REPO_URL} && cd {CLONE_REPO_DIR} && git checkout {BASE_COMMIT}")
            except:
                pass


        if 'GoogleChrome' in project_id:
            # Configuring Code Repository Information
            REPO_URL = "https://github.com/GoogleChrome/lighthouse.git"  # GitHub Repo
            BASE_COMMIT = base_commit  # Target Base Commit
            CLONE_DIR = f"Reproduce_Scenario/{split_dataset}/{project_id}/{bug_id}/REPO"  # Local Dir
            CLONE_REPO_DIR = f"lighthouse"

            print(f"{CLONE_DIR}")
            # please complete this command
            try:
                run_command(f"cd {CLONE_DIR} && git clone {REPO_URL} && cd {CLONE_REPO_DIR} && git checkout {BASE_COMMIT}")
            except:
                pass

        
        if 'alibaba-fusion' in project_id:
            # Configuring Code Repository Information
            REPO_URL = "https://github.com/alibaba-fusion/next.git"  # GitHub Repo
            BASE_COMMIT = base_commit  # Target Base Commit
            CLONE_DIR = f"Reproduce_Scenario/{split_dataset}/{project_id}/{bug_id}/REPO"  # Local Dir
            CLONE_REPO_DIR = f"next"

            print(f"{CLONE_DIR}")
            # please complete this command
            try:
                run_command(f"cd {CLONE_DIR} && git clone {REPO_URL} && cd {CLONE_REPO_DIR} && git checkout {BASE_COMMIT}")
            except:
                pass

        if 'bpmn-io' in project_id:
            # Configuring Code Repository Information
            REPO_URL = "https://github.com/bpmn-io/bpmn-js.git"  # GitHub Repo
            BASE_COMMIT = base_commit  # Target Base Commit
            CLONE_DIR = f"Reproduce_Scenario/{split_dataset}/{project_id}/{bug_id}/REPO"  # Local Dir
            CLONE_REPO_DIR = f"bpmn-js"

            print(f"{CLONE_DIR}")
            # please complete this command
            try:
                run_command(f"cd {CLONE_DIR} && git clone {REPO_URL} && cd {CLONE_REPO_DIR} && git checkout {BASE_COMMIT}")
            except:
                pass

        if 'carbon-design-system' in project_id:
            # Configuring Code Repository Information
            REPO_URL = "https://github.com/carbon-design-system/carbon.git"  # GitHub Repo
            BASE_COMMIT = base_commit  # Target Base Commit
            CLONE_DIR = f"Reproduce_Scenario/{split_dataset}/{project_id}/{bug_id}/REPO"  # Local Dir
            CLONE_REPO_DIR = f"carbon"

            if os.path.exists(os.path.join(CLONE_DIR, CLONE_REPO_DIR)):
                continue

            print(f"{CLONE_DIR}")
            # please complete this command
            try:
                run_command(f"cd {CLONE_DIR} && git clone --depth 1 {REPO_URL} && cd {CLONE_REPO_DIR} && git fetch --depth 1 origin {BASE_COMMIT} && git checkout {BASE_COMMIT}")
            except:
                pass 

        if 'eslint' in project_id:
            # Configuring Code Repository Information
            REPO_URL = "https://github.com/eslint/eslint.git"  # GitHub Repo
            BASE_COMMIT = base_commit  # Target Base Commit
            CLONE_DIR = f"Reproduce_Scenario/{split_dataset}/{project_id}/{bug_id}/REPO"  # Local Dir
            CLONE_REPO_DIR = f"eslint"

            print(f"{CLONE_DIR}")
            # please complete this command
            try:
                run_command(f"cd {CLONE_DIR} && git clone {REPO_URL} && cd {CLONE_REPO_DIR} && git checkout {BASE_COMMIT}")
            except:
                pass 

        if 'grommet' in project_id:
            # Configuring Code Repository Information
            REPO_URL = "https://github.com/grommet/grommet.git"  # GitHub Repo
            BASE_COMMIT = base_commit  # Target Base Commit
            CLONE_DIR = f"Reproduce_Scenario/{split_dataset}/{project_id}/{bug_id}/REPO"  # Local Dir
            CLONE_REPO_DIR = f"grommet"

            print(f"{CLONE_DIR}")
            # please complete this command
            try:
                run_command(f"cd {CLONE_DIR} && git clone {REPO_URL} && cd {CLONE_REPO_DIR} && git checkout {BASE_COMMIT}")
            except:
                pass 

        if 'highlightjs' in project_id:
            # Configuring Code Repository Information
            REPO_URL = "https://github.com/highlightjs/highlight.js.git"  # GitHub Repo
            BASE_COMMIT = base_commit  # Target Base Commit
            CLONE_DIR = f"Reproduce_Scenario/{split_dataset}/{project_id}/{bug_id}/REPO"  # Local Dir
            CLONE_REPO_DIR = f"highlight.js"

            print(f"{CLONE_DIR}")
            # please complete this command
            try:
                run_command(f"cd {CLONE_DIR} && git clone {REPO_URL} && cd {CLONE_REPO_DIR} && git checkout {BASE_COMMIT}")
            except:
                pass 

        if 'openlayers' in project_id:
            # Configuring Code Repository Information
            REPO_URL = "https://github.com/openlayers/openlayers.git"  # GitHub Repo
            BASE_COMMIT = base_commit  # Target Base Commit
            CLONE_DIR = f"Reproduce_Scenario/{split_dataset}/{project_id}/{bug_id}/REPO"  # Local Dir
            CLONE_REPO_DIR = f"openlayers"

            print(f"{CLONE_DIR}")
            # please complete this command
            try:
                run_command(f"cd {CLONE_DIR} && git clone {REPO_URL} && cd {CLONE_REPO_DIR} && git checkout {BASE_COMMIT}")
            except:
                pass 

        if 'prettier' in project_id:
            # Configuring Code Repository Information
            REPO_URL = "https://github.com/prettier/prettier.git"  # GitHub Repo
            BASE_COMMIT = base_commit  # Target Base Commit
            CLONE_DIR = f"Reproduce_Scenario/{split_dataset}/{project_id}/{bug_id}/REPO"  # Local Dir
            CLONE_REPO_DIR = f"prettier"

            print(f"{CLONE_DIR}")
            # please complete this command
            try:
                run_command(f"cd {CLONE_DIR} && git clone {REPO_URL} && cd {CLONE_REPO_DIR} && git checkout {BASE_COMMIT}")
            except:
                pass 


        if 'PrismJS' in project_id:
            # Configuring Code Repository Information
            REPO_URL = "https://github.com/PrismJS/prism.git"  # GitHub Repo
            BASE_COMMIT = base_commit  # Target Base Commit
            CLONE_DIR = f"Reproduce_Scenario/{split_dataset}/{project_id}/{bug_id}/REPO"  # Local Dir
            CLONE_REPO_DIR = f"prism"

            print(f"{CLONE_DIR}")
            # please complete this command
            try:
                run_command(f"cd {CLONE_DIR} && git clone {REPO_URL} && cd {CLONE_REPO_DIR} && git checkout {BASE_COMMIT}")
            except:
                pass 

        if 'quarto-dev' in project_id:
            # Configuring Code Repository Information
            REPO_URL = "https://github.com/quarto-dev/quarto-cli.git"  # GitHub Repo
            BASE_COMMIT = base_commit  # Target Base Commit
            CLONE_DIR = f"Reproduce_Scenario/{split_dataset}/{project_id}/{bug_id}/REPO"  # Local Dir
            CLONE_REPO_DIR = f"quarto-cli"

            print(f"{CLONE_DIR}")
            # please complete this command
            try:
                run_command(f"cd {CLONE_DIR} && git clone {REPO_URL} && cd {CLONE_REPO_DIR} && git checkout {BASE_COMMIT}")
            except:
                pass 

        if 'scratchfoundation' in project_id:
            # Configuring Code Repository Information
            REPO_URL = "https://github.com/scratchfoundation/scratch-gui.git"  # GitHub Repo
            BASE_COMMIT = base_commit  # Target Base Commit
            CLONE_DIR = f"Reproduce_Scenario/{split_dataset}/{project_id}/{bug_id}/REPO"  # Local Dir
            CLONE_REPO_DIR = f"scratch-gui"

            print(f"{CLONE_DIR}")
            # please complete this command
            try:
                run_command(f"cd {CLONE_DIR} && git clone {REPO_URL} && cd {CLONE_REPO_DIR} && git checkout {BASE_COMMIT}")
            except:
                pass 
      



if __name__ == "__main__":
    # save all repo info (when you first save these info, you need to run this function)
    save_all_repo_scenario("dev")
    save_all_repo_scenario("test")
    # save all issue iamges
    save_all_repo_scenario_image("dev")
    save_all_repo_scenario_image("test")

    # look all repo info (if you want to see )
    # look_all_repo_scenario("dev")

    # checkout all repo code (you can checkout code repo and build it to reproduce the bug scerarion)
    checkout_all_repo_scenario("dev")
    checkout_all_repo_scenario("test")

