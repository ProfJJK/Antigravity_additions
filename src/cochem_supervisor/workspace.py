"""Supervisor-owned workspace cleanup without importing the application."""
from pathlib import Path
import shutil


def clear_workspace(path: Path) -> None:
    """Clear a provisioned directory's contents without following child links."""
    path = Path(path).absolute()
    if any(current.is_symlink() or getattr(current.lstat(), 'st_file_attributes', 0) & 0x400
           for current in (path, *path.parents)) or not path.is_dir():
        raise ValueError('Repair workspace must be a provisioned plain directory')
    for item in path.iterdir():
        if item.is_symlink():
            item.unlink()
        elif getattr(item.lstat(), 'st_file_attributes', 0) & 0x400:
            if item.is_dir():
                item.rmdir()
            else:
                item.unlink()
        elif item.is_dir():
            shutil.rmtree(item)
        else:
            item.unlink()
