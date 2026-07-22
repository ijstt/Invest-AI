import sys
import subprocess
import os

# Put src in sys.path
sys.path.insert(0, "/home/ijstt/News/src")

def verify():
    from geoanalytics.cli import app
    print("=== TYPER APP INSPECTION ===")
    
    commands = []
    for cmd in app.registered_commands:
        commands.append(cmd.name or cmd.callback.__name__)

    groups = []
    for group in app.registered_groups:
        groups.append((group.name, [c.name or c.callback.__name__ for c in group.typer_instance.registered_commands]))

    print(f"Total top-level registered commands: {len(commands)}")
    print(f"Top-level commands list:")
    for cmd_name in sorted(commands):
        print(f"  - {cmd_name}")

    print(f"\nTotal sub-typer registered groups: {len(groups)}")
    for gname, subcmds in sorted(groups, key=lambda x: x[0] or ""):
        print(f"  Group '{gname}': {len(subcmds)} commands -> {subcmds}")

if __name__ == "__main__":
    verify()
