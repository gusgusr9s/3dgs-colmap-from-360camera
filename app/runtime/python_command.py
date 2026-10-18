"""Start the same private workers from Python or from the packaged runtime."""
import sys


def module_command(module, *arguments, executable=None):
    executable = executable or sys.executable
    flags = ['--worker-module'] if getattr(sys, 'frozen', False) else ['-u', '-X', 'utf8', '-m']
    return [executable, *flags, module, *map(str, arguments)]
