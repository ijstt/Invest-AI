import ast

cli_path = "/home/ijstt/News/src/geoanalytics/cli.py"

with open(cli_path, "r", encoding="utf-8") as f:
    source = f.read()

tree = ast.parse(source, filename=cli_path)

print("=== TOP LEVEL GLOBALS / ASSIGNMENTS ===")
for node in tree.body:
    if isinstance(node, (ast.Assign, ast.AnnAssign)):
        print(f"L{node.lineno}: {ast.unparse(node)}")

# Inspect imports across the entire file (top level vs inside functions)
top_level_imports = []
inside_fn_imports = []

for node in tree.body:
    if isinstance(node, (ast.Import, ast.ImportFrom)):
        top_level_imports.append((node.lineno, ast.unparse(node)))
    elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
        for child in ast.walk(node):
            if isinstance(child, (ast.Import, ast.ImportFrom)):
                inside_fn_imports.append((node.name, child.lineno, ast.unparse(child)))

print("\n=== TOP LEVEL IMPORTS ===")
for lineno, imp in top_level_imports:
    print(f"L{lineno:4d}: {imp}")

print(f"\nTotal inline imports inside functions: {len(inside_fn_imports)}")
# Let's group inline imports by module imported
imported_modules = set()
for fn_name, lineno, imp in inside_fn_imports:
    imported_modules.add(imp)

print("\nUnique inline import statements:")
for imp in sorted(imported_modules):
    print(f"  {imp}")

