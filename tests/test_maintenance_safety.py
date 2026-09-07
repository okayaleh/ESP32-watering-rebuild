import os
import sys
import types
import unittest
sys.path.insert(0, os.path.abspath('src'))
from application import Application

class MaintenanceSafetyTests(unittest.TestCase):
    def make(self, idle=True):
        actions = []
        controller = types.SimpleNamespace(paused=False, idle=lambda:idle,
            stop_all=lambda reason: actions.append('closed') or True,
            _persist=lambda:actions.append('persisted'))
        app = Application(types.SimpleNamespace(), types.SimpleNamespace(data={}), controller, None)
        app.updater = types.SimpleNamespace(request_check=lambda:actions.append(('check',controller.paused)),
            request_install=lambda:actions.append(('install',controller.paused)))
        return app, actions
    def test_check_cannot_write_during_active_watering(self):
        app, actions = self.make(False)
        with self.assertRaises(RuntimeError): app.update_action('check')
        self.assertEqual(actions, [])
    def test_check_pauses_starts_before_staging(self):
        app, actions = self.make()
        app.update_action('check')
        self.assertEqual(actions, ['closed', ('check', True)])
        self.assertTrue(app.controller.paused)
    def test_credentials_write_follows_output_closure(self):
        app, actions = self.make(False)
        app.wifi = types.SimpleNamespace(save_credentials=lambda *args:actions.append('credentials'))
        app.save_wifi({'ssid':'bench','password':'test'})
        self.assertEqual(actions[:3], ['closed','persisted','credentials'])
        self.assertTrue(app.controller.paused)

if __name__ == '__main__': unittest.main()
