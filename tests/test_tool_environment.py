import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from app.runtime import tool_environment as tools


class ToolEnvironmentTests(unittest.TestCase):
    def test_registered_paths_are_appended_without_changing_venv_precedence(self):
        original={'PATH':os.pathsep.join(['venv','custom']), 'CPU_MODE':'balanced'}
        with patch.object(tools,'windows_registered_paths',return_value=['custom','installed']):
            recovered=tools.tool_environment(original)
        self.assertEqual(recovered['PATH'].split(os.pathsep),['venv','custom','installed'])
        self.assertEqual(original['PATH'],os.pathsep.join(['venv','custom']))
        self.assertEqual(recovered['CPU_MODE'],'balanced')

    def test_missing_tool_is_found_in_registered_path(self):
        with tempfile.TemporaryDirectory() as temporary:
            executable=Path(temporary)/('ffmpeg.exe' if os.name=='nt' else 'ffmpeg')
            executable.write_bytes(b'fixture')
            executable.chmod(0o755)
            with patch.object(tools,'windows_registered_paths',return_value=[temporary]):
                self.assertEqual(Path(tools.find_tool('ffmpeg',{'PATH':''})),executable)

    def test_explicit_refresh_propagates_to_worker_environment(self):
        with patch.dict(os.environ,{'PATH':'old'}),patch.object(tools,'windows_registered_paths',return_value=['installed']):
            tools.refresh_tool_path()
            self.assertEqual(os.environ['PATH'],os.pathsep.join(['old','installed']))
            tools.refresh_tool_path()
            self.assertEqual(os.environ['PATH'],os.pathsep.join(['old','installed']))


if __name__ == '__main__':
    unittest.main()
