import ast
import subprocess
import os

def get_head_cli_content():
    return subprocess.run(
        ["git", "show", "HEAD:src/geoanalytics/cli.py"],
        capture_output=True, text=True, cwd="/home/ijstt/News"
    ).stdout

def get_current_cli_files():
    cli_dir = "/home/ijstt/News/src/geoanalytics/cli"
    files = [os.path.join(cli_dir, f) for f in os.listdir(cli_dir) if f.endswith(".py")]
    files.append("/home/ijstt/News/src/geoanalytics/cli.py")
    return sorted(files)

def check_fmt_and_all():
    head_code = get_head_cli_content()
    head_tree = ast.parse(head_code)
    
    current_files = get_current_cli_files()
    current_trees = {f: ast.parse(open(f, encoding="utf-8").read(), filename=f) for f in current_files}

    head_fmts = []
    for node in ast.walk(head_tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == "_fmt":
            args = [a.arg for a in node.args.args]
            head_fmts.append((node.lineno, args, ast.get_docstring(node)))

    curr_fmts = []
    for fpath, tree in current_trees.items():
        rel = os.path.relpath(fpath, "/home/ijstt/News")
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == "_fmt":
                args = [a.arg for a in node.args.args]
                curr_fmts.append((rel, node.lineno, args, ast.get_docstring(node)))

    print("HEAD _fmt definitions:")
    for item in head_fmts:
        print(" ", item)

    print("\nCurrent _fmt definitions:")
    for item in curr_fmts:
        print(" ", item)

if __name__ == "__main__":
    check_fmt_and_all()
