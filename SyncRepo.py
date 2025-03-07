'''
先安装 tiktoken, watchdog 和 tree:

brew install tree
pip3 install watchdog
pip3 install tiktoken
'''

import os
import subprocess
import urllib.parse
import time
import tiktoken
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler

def get_tree_structure(directory):
    try:
        result = subprocess.run(['tree', directory], capture_output=True, text=True)
        if result.returncode == 0:
            return result.stdout
        else:
            print("Tree command error:", result.stderr)
            return ""
    except Exception as e:
        print("Error executing tree command:", e)
        return ""

def read_files_in_directory(directory, extensions, excludes, exclude_dirs, sensitive_files):
    all_code = ""
    for root, dirs, files in os.walk(directory):
        dirs[:] = [d for d in dirs if d not in exclude_dirs]
        dirs.sort()
        files.sort()
        for file in files:
            if file in sensitive_files:
                continue
            if file.endswith(extensions):
                if file.endswith(excludes):
                    continue
                file_path = os.path.join(root, file)
                relative_path = os.path.relpath(file_path, directory)
                try:
                    with open(file_path, 'r', encoding='utf-8') as f:
                        file_content = f.read()
                    file_marker = f"----- FILE: {relative_path} -----\n"
                    all_code += file_marker + "\n```\n" + file_content + "\n```\n"
                except Exception as e:
                    print(f"Error reading file {file_path}: {e}")
    return all_code

def update_code_base(directory_path, output_file, enc, extensions, excludes,exclude_dirs, sensitive_files):
    tree_output = get_tree_structure(directory_path)
    if not tree_output:
        tree_output = "Could not generate tree structure."
    
    code_output = read_files_in_directory(directory_path, extensions, excludes, exclude_dirs, sensitive_files)
    
    combined_output = (
        "========== TREE OUTPUT ==========\n" +
        tree_output +
        "\n\n========== CODE OUTPUT ==========\n\n" +
        code_output
    )
    
    try:
        with open(output_file, "w", encoding="utf-8") as f:
            f.write(combined_output)
        print("Updated code base written to", output_file)
    except Exception as e:
        print(f"Error writing to file {output_file}: {e}")
    
    token_count = len(enc.encode(combined_output))
    print("-------------------------------")
    print(f"Token Volumn: {token_count} tokens.")
    usage_4o = (token_count / 128000) * 100
    usage_4_5 = (token_count / 128000) * 100
    usage_o1 = (token_count / 200000) * 100
    usage_o3 = (token_count / 200000) * 100
    print(f"gpt-4o usage: {usage_4o:.1f}%")
    print(f"gpt-4.5 usage: {usage_4_5:.1f}%")
    print(f"o1 usage: {usage_o1:.1f}%")
    print(f"o3-mini usage: {usage_o3:.1f}%")
    print("-------------------------------")
    print("Last updated at:", time.strftime("%Y-%m-%d %H:%M:%S", time.localtime()))

class ChangeHandler(FileSystemEventHandler):
    def __init__(self, directory, output_file, enc, extensions, excludes, exclude_dirs, sensitive_files):
        self.directory = directory
        self.output_file = output_file
        self.enc = enc
        self.extensions = extensions
        self.excludes = excludes
        self.exclude_dirs = exclude_dirs
        self.sensitive_files = sensitive_files
        self.debounce_time = 5  # (自定义)防抖动间隔（秒）
        self.last_update = time.time()

    def on_any_event(self, event):
        current_time = time.time()
        if current_time - self.last_update < self.debounce_time:
            return
        self.last_update = current_time
        print("Change detected, updating code base...")
        update_code_base(self.directory, self.output_file, self.enc, self.extensions, self.excludes, self.exclude_dirs, self.sensitive_files)

def main():
    directory_path = input("Please drag the directory into the terminal and press enter: ").strip()
    directory_path = directory_path.strip('\'"').replace('file:', '')
    directory_path = urllib.parse.unquote(directory_path)
    print(f"Final directory path: {directory_path}")

    # (自定义|必选) 指定需要导出的文件格式
    extensions = (".java", ".kt", ".jsp", ".properties", ".js", ".ts", ".css", ".json", ".html")
    # (自定义|可选) 排除的文件格式
    excludes = (".spec.ts",)
    # (自定义|可选) 排除的目录
    exclude_dirs = ["test", "build", "target"] 
    # (自定义|可选) 排除的具体敏感文件
    sensitive_files = [".env", "private.key", "secrets.txt", "passwords.json"]
    
    # (自定义|必选)设置输出路径（手动替换此处路径）, markdown保存在本机硬盘目录而非iCloud, 感觉同步会更灵敏
    output_dir = os.path.expanduser("/Users/terry/")
    
    #(自定义|必选)代码库同步保存的markdown文件名称,注释掉其他,只保留任意一个:
    output_file = os.path.join(output_dir, "Project Repository.md")
    # output_file = os.path.join(output_dir, "Frontend Repository.md")
    # output_file = os.path.join(output_dir, "其他任何你可能常用的文件名.md")
    
    if not os.path.exists(output_dir):
        os.makedirs(output_dir)
        print(f"Created directory: {output_dir}")
    print(f"Output will be saved to: {output_file}")

    enc = tiktoken.get_encoding("o200k_base")

    update_code_base(directory_path, output_file, enc, extensions, excludes, exclude_dirs, sensitive_files)

    event_handler = ChangeHandler(directory_path, output_file, enc, extensions, excludes, exclude_dirs, sensitive_files)
    observer = Observer()
    observer.schedule(event_handler, path=directory_path, recursive=True)
    observer.start()
    print("Watching for changes... (Press Ctrl+C to exit)")

    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        observer.stop()
    observer.join()

if __name__ == '__main__':
    main()

'''
为了防止执行该脚本的Visual Studio Code 和 负责显示代码库的 TextMate 在最小化时被系统挂起, 需要禁用App Nap.
请在Terminal中执行:

defaults write com.microsoft.VSCode NSAppSleepDisabled -bool YES
defaults write com.macromates.TextMate NSAppSleepDisabled -bool YES

开启后长期有效, 如需关闭, 只需再执行:

defaults write com.microsoft.VSCode NSAppSleepDisabled -bool NO
defaults write com.macromates.TextMate NSAppSleepDisabled -bool NO

---

推荐使用: TextMate, 
Download: https://macromates.com

如有更换TextMate应用图标的需求, 可以自行更改, 推荐方案:
https://github.com/marc2o/TextMate-macOS-Icon/raw/refs/heads/main/TextMate.icns
'''