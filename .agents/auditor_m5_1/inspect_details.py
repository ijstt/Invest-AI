import ast
import subprocess
import os

def get_head_cli_content():
    return subprocess.run(
        ["git", "show", "HEAD:src/geoanalytics/cli.py"],
        capture_output=True, text=True, cwd="/home/ijstt/News"
    ).stdout

def inspect_details():
    head_code = get_head_cli_content()
    
    # 1. Semicolons check
    print("=== SEMICOLONS CHECK ===")
    cli_dir = "/home/ijstt/News/src/geoanalytics/cli"
    for root, _, files in os.walk(cli_dir):
        for f in files:
            if f.endswith(".py"):
                fpath = os.path.join(root, f)
                with open(fpath, "r", encoding="utf-8") as file:
                    for i, line in enumerate(file, 1):
                        if ";" in line:
                            print(f"{f}:{i}: {line.strip()}")

    # 2. Long lines check
    print("\n=== LONG LINES (>200 chars) CHECK ===")
    for root, _, files in os.walk(cli_dir):
        for f in files:
            if f.endswith(".py"):
                fpath = os.path.join(root, f)
                with open(fpath, "r", encoding="utf-8") as file:
                    for i, line in enumerate(file, 1):
                        if len(line) > 200:
                            print(f"{f}:{i} (len={len(line)}): {line[:120]}...")

    # 3. Inspect _fmt in HEAD vs current
    print("\n=== _fmt INSPECTION ===")
    head_tree = ast.parse(head_code)
    for node in ast.walk(head_tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == "_fmt":
            print(f"HEAD _fmt args: {[a.arg for a in node.args.args]}")
            print(f"HEAD _fmt lineno: {node.lineno}")

    # 4. Inspect forecasts diff
    print("\n=== forecasts DIFF INSPECTION ===")
    # Extract forecasts from HEAD
    head_lines = head_code.splitlines()
    for node in ast.walk(head_tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == "forecasts":
            head_forecasts_lines = head_lines[node.lineno-1:node.end_lineno]
            print(f"HEAD forecasts lines: {len(head_forecasts_lines)}")
            head_forecasts_str = "\n".join(head_forecasts_lines)

    # Extract forecasts from nlp.py
    nlp_path = "/home/ijstt/News/src/geoanalytics/cli/nlp.py"
    with open(nlp_path, "r", encoding="utf-8") as f:
        nlp_code = f.read()
    nlp_tree = ast.parse(nlp_code)
    nlp_lines = nlp_code.splitlines()
    for node in ast.walk(nlp_tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == "forecasts":
            curr_forecasts_lines = nlp_lines[node.lineno-1:node.end_lineno]
            print(f"Curr forecasts lines: {len(curr_forecasts_lines)}")
            curr_forecasts_str = "\n".join(curr_forecasts_lines)

    import difflib
    diff = list(difflib.unified_diff(
        head_forecasts_str.splitlines(),
        curr_forecasts_str.splitlines(),
        fromfile="HEAD forecasts",
        tofile="Curr forecasts",
        lineterm=""
    ))
    for line in diff:
        print(line)

if __name__ == "__main__":
    inspect_details()
