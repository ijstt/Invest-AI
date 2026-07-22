import ast

cli_path = "/home/ijstt/News/src/geoanalytics/cli.py"

with open(cli_path, "r", encoding="utf-8") as f:
    source = f.read()

tree = ast.parse(source, filename=cli_path)

# Check functions that call _rich_link or _fmt
link_callers = []
fmt_callers = []
table_creators = []
console_users = []

for node in tree.body:
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
        for child in ast.walk(node):
            if isinstance(child, ast.Call):
                func_str = ast.unparse(child.func)
                if func_str == "_rich_link":
                    link_callers.append(node.name)
                elif func_str == "_fmt":
                    fmt_callers.append(node.name)
                elif "Table" in func_str:
                    table_creators.append(node.name)
                elif "console" in func_str:
                    console_users.append(node.name)

print(f"_rich_link callers: {set(link_callers)}")
print(f"_fmt callers: {set(fmt_callers)}")
print(f"Table creators ({len(set(table_creators))} functions): {sorted(list(set(table_creators)))}")
