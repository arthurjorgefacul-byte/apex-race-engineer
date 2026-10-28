"""Installer behavior against isolated game/config fixtures; no user config writes."""
import hashlib
import json
import os
import pathlib
import shutil
import subprocess
import tempfile
import unittest


@unittest.skipUnless(os.name == 'nt', 'Windows installer')
class OverlayInstallerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = pathlib.Path(self.temp.name)
        self.package = self.root / 'package'
        self.package.mkdir()
        self.installer = self.package / 'Install Overlay.ps1'
        shutil.copyfile(pathlib.Path(__file__).parents[1] / self.installer.name, self.installer)
        overlay = self.package / 'ac_app' / 'RaceEngineer'
        support = overlay / 'stdlib'
        support.mkdir(parents=True)
        (overlay / 'RaceEngineer.py').write_text('# fixture')
        (overlay / 'README.txt').write_text('fixture')
        (support / 'socket.py').write_text('# fixture socket')
        self.support = support
        self.manifest = support / 'PROVENANCE.json'
        self.manifest.write_text(json.dumps({'files': [{'path': 'socket.py',
            'sha256': hashlib.sha256((support / 'socket.py').read_bytes()).hexdigest()}]}))
        bridge = self.package / 'csp_app' / 'ApexBridge'
        bridge.mkdir(parents=True)
        (bridge / 'manifest.ini').write_text('[ABOUT]\nNAME=fixture')
        (bridge / 'ApexBridge.lua').write_text('-- fixture')
        self.bridge_source = bridge
        self.game = self.root / 'game'
        self.game.mkdir()
        (self.game / 'acs.exe').write_bytes(b'fixture, never executed')
        self.config = self.root / 'config'
        self.config.mkdir()
        self.python_cfg = self.config / 'python.ini'
        self.lua_cfg = self.config / 'extension' / 'state' / 'imgui_settings.ini'
        self.lua_cfg.parent.mkdir(parents=True)
        self.python_original = '[RACEENGINEER]\r\nACTIVE=0\r\nWINDOW=preserved\r\n\r\n[FRIEND]\r\nACTIVE=1\r\n'
        self.lua_original = '[OTHER]\r\nACTIVE=keep\r\n[LUA_APPS]\r\nACTIVE=ext_controls,ac_pro_engineer\r\nOTHER=keep too\r\n[LAST]\r\nVALUE=7\r\n'
        self.python_cfg.write_bytes(self.python_original.encode())
        self.lua_cfg.write_bytes(self.lua_original.encode())

    def run_installer(self, running=False):
        # The test's Get-Process shadows inventory only inside this child shell.
        # The fixture explicitly supplies both game/config destinations.
        wrapper = self.root / 'run.ps1'
        mock = "return [pscustomobject]@{ProcessName='acs'}" if running else 'return'
        wrapper.write_text("param($Installer,$Game,$Config)\n$ErrorActionPreference='Stop'\n"
            'function Get-Process { param($Name,$ErrorAction) ' + mock + ' }\n'
            '& $Installer -GamePath $Game -ConfigPath $Config -Enable\n')
        return subprocess.run(['powershell.exe', '-NoProfile', '-NonInteractive', '-ExecutionPolicy', 'Bypass',
            '-File', str(wrapper), str(self.installer), str(self.game), str(self.config)],
            capture_output=True, text=True, timeout=20)

    def test_installs_both_apps_preserves_settings_and_is_idempotent(self):
        result = self.run_installer()
        self.assertEqual(result.returncode, 0, result.stderr)
        for relative, files in [('python/RaceEngineer', ('RaceEngineer.py', '.apex-race-engineer', 'stdlib/socket.py')),
                                ('lua/ApexBridge', ('manifest.ini', 'ApexBridge.lua', '.apex-race-engineer'))]:
            for name in files:
                self.assertTrue((self.game / 'apps' / relative / name).is_file(), name)
        python = self.python_cfg.read_text()
        lua = self.lua_cfg.read_text()
        self.assertIn('ACTIVE=1\nWINDOW=preserved', python)
        self.assertIn('[FRIEND]\nACTIVE=1', python)
        self.assertIn('ACTIVE=ext_controls,ac_pro_engineer,ApexBridge', lua)
        self.assertIn('OTHER=keep too\n[LAST]\nVALUE=7', lua)
        backups = list(self.config.rglob('*.bak'))
        self.assertEqual(len(backups), 2)
        self.assertEqual({path.read_bytes() for path in backups},
                         {self.python_original.encode(), self.lua_original.encode()})
        result = self.run_installer()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(len(list(self.config.rglob('*.bak'))), 2)
        self.assertEqual(self.lua_cfg.read_text().count('ApexBridge'), 1)

    def test_running_game_defers_activation_and_keeps_config_byte_exact(self):
        result = self.run_installer(running=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('activation settings were left unchanged', result.stdout)
        self.assertTrue((self.game / 'apps/lua/ApexBridge/ApexBridge.lua').is_file())
        self.assertEqual(self.python_cfg.read_bytes(), self.python_original.encode())
        self.assertEqual(self.lua_cfg.read_bytes(), self.lua_original.encode())
        self.assertEqual(list(self.config.rglob('*.bak')), [])

    def test_unrelated_bridge_prevents_mutation_of_either_app(self):
        existing = self.game / 'apps/lua/ApexBridge'
        existing.mkdir(parents=True)
        sentinel = existing / 'user.lua'
        sentinel.write_text('preserve')
        result = self.run_installer()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('Both apps have been left untouched', result.stderr)
        self.assertEqual(sentinel.read_text(), 'preserve')
        self.assertFalse((self.game / 'apps/python/RaceEngineer').exists())
        self.assertEqual(self.lua_cfg.read_bytes(), self.lua_original.encode())

    def test_missing_lua_package_is_rejected_before_any_install(self):
        (self.bridge_source / 'ApexBridge.lua').unlink()
        result = self.run_installer()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('missing its CSP session bridge', result.stderr)
        self.assertFalse((self.game / 'apps').exists())

    def test_bad_runtime_checksum_is_rejected_before_any_install(self):
        (self.support / 'socket.py').write_text('changed')
        result = self.run_installer()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('checksum mismatch', result.stderr)
        self.assertFalse((self.game / 'apps').exists())


if __name__ == '__main__':
    unittest.main()
