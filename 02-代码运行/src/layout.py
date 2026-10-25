"""Resolve CSV outputs and GitHub configuration independently of the code folder."""
from pathlib import Path


def project_path(root, relative):
    root, relative = Path(root), Path(relative)
    if relative.is_absolute():
        return relative
    resolved = root.resolve()
    if resolved.name == "02-代码运行" and relative.parts:
        if relative.parts[0] == "csv":
            return resolved.parent / "01-CSV" / Path(*relative.parts[1:])
        if relative.parts[0] == ".github":
            return resolved.parent / relative
    return root / relative
