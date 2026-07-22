import ast
import re

cli_path = "/home/ijstt/News/src/geoanalytics/cli.py"

with open(cli_path, "r", encoding="utf-8") as f:
    source = f.read()

lines = source.splitlines()

tree = ast.parse(source, filename=cli_path)

# Find all imports
imports = []
for node in ast.walk(tree):
    if isinstance(node, ast.Import):
        for name in node.names:
            imports.append((node.lineno, f"import {name.name}" + (f" as {name.asname}" if name.asname else "")))
    elif isinstance(node, ast.ImportFrom):
        module = node.module or ""
        level = node.level
        dots = "." * level
        for name in node.names:
            imports.append((node.lineno, f"from {dots}{module} import {name.name}" + (f" as {name.asname}" if name.asname else "")))

imports.sort(key=lambda x: x[0])

# Find all Typer app instantiations & add_typer calls
typer_apps = []
add_typer_calls = []

class TyperVisitor(ast.NodeVisitor):
    def visit_Assign(self, node):
        if isinstance(node.value, ast.Call):
            func_name = ast.unparse(node.value.func)
            if "Typer" in func_name or func_name == "typer.Typer":
                targets = [ast.unparse(t) for t in node.targets]
                typer_apps.append((node.lineno, targets, ast.unparse(node.value)))
        self.generic_visit(node)

    def visit_Expr(self, node):
        if isinstance(node.value, ast.Call):
            func_name = ast.unparse(node.value.func)
            if "add_typer" in func_name:
                add_typer_calls.append((node.lineno, ast.unparse(node.value)))
        self.generic_visit(node)

TyperVisitor().visit(tree)

# Collect all top-level functions and examine their decorators and internal imports/calls
functions_info = []

for node in tree.body:
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
        name = node.name
        start_line = node.lineno
        end_line = getattr(node, 'end_lineno', start_line)
        length = end_line - start_line + 1
        
        decorators = [ast.unparse(d) for d in node.decorator_list]
        
        # Check if function is a command, callback, or helper
        cmd_decorators = [d for d in decorators if "command" in d or "callback" in d]
        
        # Find imports inside function
        local_imports = []
        calls = []
        for child in ast.walk(node):
            if isinstance(child, (ast.Import, ast.ImportFrom)):
                local_imports.append(ast.unparse(child))
            elif isinstance(child, ast.Call):
                calls.append(ast.unparse(child.func))
        
        functions_info.append({
            "name": name,
            "start": start_line,
            "end": end_line,
            "length": length,
            "decorators": decorators,
            "cmd_decorators": cmd_decorators,
            "local_imports": local_imports,
            "calls": calls,
            "doc": ast.get_docstring(node)
        })

print("=== TOP-LEVEL IMPORTS ===")
for lineno, imp in imports:
    print(f"L{lineno:4d}: {imp}")

print("\n=== TYPER APPS INSTANTIATIONS ===")
for lineno, targets, val in typer_apps:
    print(f"L{lineno:4d}: {', '.join(targets)} = {val}")

print("\n=== SUB-APP ADDITIONS (add_typer) ===")
for lineno, call in add_typer_calls:
    print(f"L{lineno:4d}: {call}")

print(f"\nTotal functions: {len(functions_info)}")

helpers = [f for f in functions_info if not f['cmd_decorators']]
commands = [f for f in functions_info if f['cmd_decorators']]

print(f"Helper functions (no @*.command): {len(helpers)}")
for h in helpers:
    print(f"  L{h['start']:4d}-L{h['end']:4d} ({h['length']} lines): {h['name']}")

print(f"\nCommand functions: {len(commands)}")
for c in commands:
    decs = ", ".join(c['cmd_decorators'])
    print(f"  L{c['start']:4d}-L{c['end']:4d} ({c['length']:2d} lines): {c['name']} [{decs}]")
