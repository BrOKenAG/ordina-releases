import importlib.util
from pathlib import Path
import unittest

spec = importlib.util.spec_from_file_location('setup_dispatch', Path(__file__).resolve().parents[1] / 'Integrations/Project Setup.py')
setup = importlib.util.module_from_spec(spec)
spec.loader.exec_module(setup)


class DispatcherTests(unittest.TestCase):
    def dispatcher(self):
        self.assertTrue(hasattr(setup, 'compatible_dispatcher'), 'Missing Resolve event compatibility adapter')
        class HostDispatcher(dict):
            def __init__(self, ui):
                super().__init__({'Window': {'On': {'Button': {'Clicked': ui.append}, 'Window': {'Close': ui.append}}}, 'On': {'Timeout': ui.append}})
            def Dispatch(self, event):
                if 'window' in event:
                    self[event['window']]['On'][event['who']][event['what']](event)
                else:
                    self['On'][event['what']](event)
        calls = []
        host = type('Host', (), {'UIDispatcher': HostDispatcher})
        return setup.compatible_dispatcher(host, calls), calls

    def test_missing_sender_and_stale_window_events_do_not_close_plugin(self):
        dispatcher, calls = self.dispatcher()
        for event in [{'window': 'Window', 'what': 'Show'},
                      {'window': 'OldWindow', 'who': 'Button', 'what': 'Clicked'},
                      {'window': 'Window', 'who': 'GoneButton', 'what': 'Clicked'},
                      {'window': 'Window', 'who': 'Button'}, None]:
            dispatcher.Dispatch(event)
        self.assertEqual(calls, [])

    def test_known_button_and_timer_events_still_reach_handlers(self):
        dispatcher, calls = self.dispatcher()
        events = [{'window': 'Window', 'who': 'Button', 'what': 'Clicked'}, {'what': 'Timeout'}]
        for event in events:
            dispatcher.Dispatch(event)
        self.assertEqual(calls, events)

    def test_senderless_close_reaches_own_window_handler(self):
        dispatcher, calls = self.dispatcher()
        dispatcher.Dispatch({'window': 'Window', 'what': 'Close'})
        self.assertEqual(calls, [{'window': 'Window', 'who': 'Window', 'what': 'Close'}])


if __name__ == '__main__':
    unittest.main()
