"""Recover installed Windows tools when an IDE inherited an older PATH."""
import os
import shutil


def windows_registered_paths():
    if os.name != 'nt':
        return []
    import winreg
    paths = []
    for hive, key in ((winreg.HKEY_LOCAL_MACHINE, r'SYSTEM\CurrentControlSet\Control\Session Manager\Environment'),
                      (winreg.HKEY_CURRENT_USER, 'Environment')):
        try:
            with winreg.OpenKey(hive, key) as handle:
                value, _ = winreg.QueryValueEx(handle, 'Path')
            paths.extend(os.path.expandvars(value).split(os.pathsep))
        except OSError:
            continue
    return paths


def tool_environment(environment=None):
    result = dict(os.environ if environment is None else environment)
    entries, seen = [], set()
    # Keep explicit process/venv choices first; append installed tool locations.
    for item in [*result.get('PATH', '').split(os.pathsep), *windows_registered_paths()]:
        item = item.strip().strip('"')
        key = os.path.normcase(item)
        if item and key not in seen:
            entries.append(item)
            seen.add(key)
    result['PATH'] = os.pathsep.join(entries)
    return result


def find_tool(name, environment=None):
    return shutil.which(name, path=tool_environment(environment)['PATH'])


def refresh_tool_path():
    os.environ['PATH'] = tool_environment()['PATH']
