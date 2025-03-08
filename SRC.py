import os
import subprocess
import urllib.parse
import time
import threading
import re

import tiktoken
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler, FileSystemMovedEvent

class RepositoryCache:
    """
    Manages an in-memory map of relative_path -> file_content.
    Provides methods to load, update, and remove files in the cache.
    """
    def __init__(self):
        self._file_cache = {}

    def load_initial_cache(self, directory, extensions, excludes, exclude_dirs, sensitive_files, regex_patterns=None):
        """Scan the entire directory and load all relevant files into _file_cache."""
        self._file_cache.clear()
        for root, dirs, files in os.walk(directory):
            dirs[:] = [d for d in dirs if d not in exclude_dirs]
            dirs.sort()
            files.sort()
            for file in files:
                if self._should_include(file, extensions, excludes, sensitive_files, regex_patterns):
                    file_path = os.path.join(root, file)
                    relative_path = os.path.relpath(file_path, directory)
                    self._read_and_store_file(file_path, relative_path)

    def update_file(self, file_path, relative_path):
        """Read the changed or created file from disk and update the cache."""
        if not os.path.exists(file_path):
            # If the file no longer exists, remove from cache
            self.remove_file(relative_path)
            return
        self._read_and_store_file(file_path, relative_path)

    def remove_file(self, relative_path):
        """Remove a file from the cache if it exists."""
        if relative_path in self._file_cache:
            del self._file_cache[relative_path]

    def get_all_files_sorted(self):
        """Return all paths in the cache, sorted by relative path."""
        return sorted(self._file_cache.keys())

    def get_file_content(self, relative_path):
        return self._file_cache.get(relative_path, "")

    def _should_include(self, filename, extensions, excludes, sensitive_files, regex_patterns):
        """Return True if filename passes all filters (extensions, excludes, regex, etc.)."""
        if filename in sensitive_files:
            return False
        # Simple suffix-based checks
        if not any(filename.endswith(ext) for ext in extensions):
            return False
        if any(filename.endswith(exc) for exc in excludes):
            return False
        # Optional regex checks
        if regex_patterns:
            for pattern in regex_patterns:
                if re.search(pattern, filename):
                    # If you want to exclude matches, invert the logic here
                    return False
        return True

    def _read_and_store_file(self, file_path, relative_path):
        """Read a single file and update _file_cache."""
        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                content = f.read()
            self._file_cache[relative_path] = content
        except Exception as e:
            print(f"[ERROR] Reading file {file_path}: {e}")

def get_tree_structure(directory):
    """Run the 'tree' command and return the output as a string."""
    try:
        result = subprocess.run(['tree', directory], capture_output=True, text=True)
        if result.returncode == 0:
            return result.stdout
        else:
            print("[WARN] 'tree' command returned an error:", result.stderr)
            return ""
    except Exception as e:
        print("[ERROR] Executing 'tree' command:", e)
        return ""

def generate_markdown_output(repo_cache, directory, tree_output):
    """
    Combine the 'tree' output and the current file cache into a single Markdown string.
    """
    code_output = ""
    for relative_path in repo_cache.get_all_files_sorted():
        file_content = repo_cache.get_file_content(relative_path)
        code_output += f"----- FILE: {relative_path} -----\n"
        code_output += "```\n" + file_content + "\n```\n\n"

    combined_output = (
        "========== TREE OUTPUT ==========\n" +
        tree_output +
        "\n\n========== CODE OUTPUT ==========\n\n" +
        code_output
    )
    return combined_output

def write_markdown_output(output_file, combined_output, enc):
    """Write the combined Markdown output to the file and print usage stats."""
    try:
        with open(output_file, "w", encoding="utf-8") as f:
            f.write(combined_output)
        print("[INFO] Updated code base written to", output_file)
    except Exception as e:
        print(f"[ERROR] Writing to file {output_file}: {e}")

    token_count = len(enc.encode(combined_output))
    print("-------------------------------")
    print(f"Token Volume: {token_count} tokens.")
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

def update_code_base(directory_path, repo_cache, output_file, enc):
    """Regenerate the Markdown from the in-memory cache + fresh 'tree' output."""
    tree_output = get_tree_structure(directory_path)
    if not tree_output:
        tree_output = "Could not generate tree structure."
    combined_output = generate_markdown_output(repo_cache, directory_path, tree_output)
    write_markdown_output(output_file, combined_output, enc)

# ---------------------------------------------
# Debounce logic: schedule updates via a Timer
# ---------------------------------------------
class Debouncer:
    def __init__(self, wait_time, callback):
        """
        :param wait_time: how many seconds to wait after the last event before calling callback
        :param callback: function to call when the time is up
        """
        self.wait_time = wait_time
        self.callback = callback
        self._timer = None
        self._lock = threading.Lock()

    def trigger(self):
        """Reset the timer on each call; only run callback after the time has fully elapsed."""
        with self._lock:
            if self._timer is not None:
                self._timer.cancel()
            self._timer = threading.Timer(self.wait_time, self.callback)
            self._timer.start()

    def cancel(self):
        """Cancel any pending callbacks."""
        with self._lock:
            if self._timer is not None:
                self._timer.cancel()
                self._timer = None

class ChangeHandler(FileSystemEventHandler):
    """
    Handles file system events and schedules an update of the code base
    after a short period of no additional changes.
    """
    def __init__(self, directory, repo_cache, output_file, enc,
                 extensions, excludes, exclude_dirs, sensitive_files,
                 wait_time=5, regex_patterns=None):
        super().__init__()
        self.directory = directory
        self.repo_cache = repo_cache
        self.output_file = output_file
        self.enc = enc
        self.extensions = extensions
        self.excludes = excludes
        self.exclude_dirs = exclude_dirs
        self.sensitive_files = sensitive_files
        self.regex_patterns = regex_patterns or []
        
        # Initialize a Debouncer; after 'wait_time' seconds of no new events, do a full update
        self.debouncer = Debouncer(wait_time, self._on_debounced_update)

    def on_created(self, event):
        if not event.is_directory and self._is_relevant_file(event.src_path):
            print(f"[EVENT] Created: {event.src_path}")
            self._handle_change(event.src_path)

    def on_modified(self, event):
        if not event.is_directory and self._is_relevant_file(event.src_path):
            print(f"[EVENT] Modified: {event.src_path}")
            self._handle_change(event.src_path)

    def on_deleted(self, event):
        if not event.is_directory and self._is_relevant_file(event.src_path):
            print(f"[EVENT] Deleted: {event.src_path}")
            rel_path = os.path.relpath(event.src_path, self.directory)
            self.repo_cache.remove_file(rel_path)
            # Schedule an update (which will remove the file from final MD output)
            self.debouncer.trigger()

    def on_moved(self, event):
        # Moved events are typically FileSystemMovedEvent, which has src_path and dest_path
        if isinstance(event, FileSystemMovedEvent) and not event.is_directory:
            old_path = event.src_path
            new_path = event.dest_path
            # If old_path was relevant, remove it from cache
            if self._is_relevant_file(old_path):
                old_rel = os.path.relpath(old_path, self.directory)
                self.repo_cache.remove_file(old_rel)
            # If new_path is relevant, read new file
            if self._is_relevant_file(new_path):
                print(f"[EVENT] Moved from {old_path} to {new_path}")
                self._handle_change(new_path)

    def _handle_change(self, file_path):
        """Update the in-memory cache for this file and schedule a rebuild."""
        rel_path = os.path.relpath(file_path, self.directory)
        if os.path.exists(file_path):
            self.repo_cache.update_file(file_path, rel_path)
        self.debouncer.trigger()

    def _on_debounced_update(self):
        """Called by the Debouncer when no additional changes have occurred for wait_time seconds."""
        print("[DEBOUNCE] No new changes, updating code base...")
        update_code_base(self.directory, self.repo_cache, self.output_file, self.enc)

    def _is_relevant_file(self, file_path):
        """Check if a file should be included based on extension, exclude, sensitivity, etc."""
        file_name = os.path.basename(file_path)
        if file_name in self.sensitive_files:
            return False
        # Check if extension is in the desired list
        if not any(file_name.endswith(ext) for ext in self.extensions):
            return False
        # Check excludes
        if any(file_name.endswith(x) for x in self.excludes):
            return False
        # Check if it's in an excluded directory
        for ed in self.exclude_dirs:
            abs_excl = os.path.join(self.directory, ed)
            if os.path.commonpath([file_path, abs_excl]) == abs_excl:
                return False
        # Check optional regex filters
        for pattern in self.regex_patterns:
            if re.search(pattern, file_name):
                return False
        return True

def main():
    directory_path = input("Please drag the directory into the terminal and press enter: ").strip()
    directory_path = directory_path.strip('\'"').replace('file:', '')
    directory_path = urllib.parse.unquote(directory_path)
    print(f"[INFO] Final directory path: {directory_path}")

    # (自定义|必选) 指定需要导出的文件格式
    extensions = (".java", ".kt", ".jsp", ".properties", ".js", ".ts", ".css", ".json", ".html")
    # (自定义|可选) 排除的文件格式
    excludes = (".spec.ts",)
    # (自定义|可选) 排除的目录
    exclude_dirs = ["test", "build", "target"]
    # (自定义|可选) 排除的具体敏感文件
    sensitive_files = [".env", "private.key", "secrets.txt", "passwords.json"]
    # (可选) Regex patterns to exclude (example: anything with "test" in the filename)
    regex_patterns = []
    
    # (自定义|必选) 设置输出路径（手动替换此处路径）
    output_dir = os.path.expanduser("/Users/terry/")
    output_file = os.path.join(output_dir, "Project Repository.md")

    if not os.path.exists(output_dir):
        os.makedirs(output_dir)
        print(f"[INFO] Created directory: {output_dir}")
    print(f"[INFO] Output will be saved to: {output_file}")

    # Initialize the tokenizer
    enc = tiktoken.get_encoding("o200k_base")

    # Initialize the repository cache
    repo_cache = RepositoryCache()
    repo_cache.load_initial_cache(directory_path, extensions, excludes, exclude_dirs, sensitive_files, regex_patterns)

    # Write the initial Markdown file
    update_code_base(directory_path, repo_cache, output_file, enc)

    # Set up the watchdog
    event_handler = ChangeHandler(
        directory=directory_path,
        repo_cache=repo_cache,
        output_file=output_file,
        enc=enc,
        extensions=extensions,
        excludes=excludes,
        exclude_dirs=exclude_dirs,
        sensitive_files=sensitive_files,
        wait_time=5,  # seconds
        regex_patterns=regex_patterns
    )

    observer = Observer()
    observer.schedule(event_handler, path=directory_path, recursive=True)
    observer.start()
    print("[INFO] Watching for changes... (Press Ctrl+C to exit)")

    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        observer.stop()
    observer.join()
    # Cancel any pending debounces before exiting
    event_handler.debouncer.cancel()

if __name__ == '__main__':
    main()