import os
import re
from collections import Counter


valid_extensions = (".js", ".jsx", ".js.snap", ".ts", ".tsx", ".lua", ".json")



def search_keywords_in_file(repo_path, file_path, keywords, file_match_count, match_details):

    try:
        with open(file_path, "r", encoding="utf-8") as f:
            for line_num, line in enumerate(f, 1):
                for keyword in keywords:
                    if re.search(rf"\b{re.escape(keyword)}\b", line): 
                        bug_file_without_root = ''
                        if '/' == repo_path[-1]:
                            bug_file_without_root = file_path.replace(repo_path,'')
                        else:
                            bug_file_without_root = file_path.replace(repo_path+'/','')
                        file_match_count[bug_file_without_root] += 1  
                        match_details.append((bug_file_without_root, line_num, line.strip()))
    except (UnicodeDecodeError, PermissionError) as e:
        print(f"[ERROR] {file_path}: {e}")

def search_keywords_in_repo(repo_path, all_files_list, keywords, file_match_count, match_details):

    for file in all_files_list:
        if file.endswith(valid_extensions): 
            file_path = os.path.join(repo_path, file)
            search_keywords_in_file(repo_path, file_path, keywords, file_match_count, match_details)

def bug_file_searching_by_keywords(repo_path, all_files_list, keywords):

    file_match_count = Counter()  
    match_details = []  


    search_keywords_in_repo(repo_path, all_files_list, keywords, file_match_count, match_details)


    top_bug_files = file_match_count.most_common(10)


    for idx, (file, count) in enumerate(top_bug_files, 1):
        print(f"{idx}. {file} - : {count}")


    return top_bug_files
