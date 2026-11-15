import csv
import os
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import Mock, patch
import starlink_dashboard as sd
from tests.helpers import dashboard

class FollowupTests(unittest.TestCase):
    def test_mode_persistence_and_legacy_migration(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(sd,'LOCATION_FILE',Path(tmp)/'location.json'):
            self.assertEqual(sd.load_location_mode(),'auto')
            sd.save_gps_port('COM7')
            self.assertEqual(sd.load_location_mode(),'external')
            for mode in ('manual','auto','external'):
                sd.save_location_mode(mode)
                self.assertEqual(sd.load_location_mode(),mode)
                self.assertEqual(sd.load_gps_port(),'COM7')

    def test_transient_retains_fix_but_denial_discards_it(self):
        d = dashboard()
        d._apply_dish_location(0,(47.6,-122.3),'Available',time.monotonic())
        d._apply_dish_location(0,None,'Unavailable',time.monotonic())
        self.assertEqual(d._location_source,'dish')
        d._apply_dish_location(0,None,'Not shared',time.monotonic(),True)
        self.assertEqual(d._location_source,'none')
        d._apply_dish_location(0,(47.6,-122.3),'Available',time.monotonic())
        d._dish_location_received -= sd.GPS_MAX_AGE + 1
        d._apply_dish_location(0,None,'Unavailable',time.monotonic())
        self.assertEqual(d._location_source,'none')

    def test_button_interrupts_location_backoff(self):
        d = dashboard(); d._gps_reader = None; d._gps_generation = 0
        waiting = threading.Event(); resumed = threading.Event()
        def wait():
            waiting.set()
            if d._location_wake.wait(5): resumed.set()
        worker = threading.Thread(target=wait); worker.start()
        self.assertTrue(waiting.wait(1))
        with patch.object(sd,'save_location_mode') as save:
            d._use_dish_location()
            save.assert_called_once_with('auto')
        self.assertTrue(resumed.wait(1)); worker.join(1)

    def test_stuck_gps_reader_has_visible_deadline(self):
        d = dashboard(); d.root = Mock(); d._gps_generation = 1
        d._gps_reader = Mock(); d._gps_reader._thread.is_alive.return_value = True
        with patch.object(sd,'GpsReader') as reader:
            d._start_gps_when_ready(1,'COM7',time.monotonic()-1)
        reader.assert_not_called(); d.root.after.assert_not_called()
        self.assertIn('did not stop',d.location_panel._gps_var.set.call_args.args[0])

    def test_partial_serial_lines_survive_timeouts(self):
        import serial
        g = sd.GpsReader.__new__(sd.GpsReader)
        g._stop = threading.Event(); g._on_raw = Mock(); g._on_update = Mock(); g._parse = Mock()
        chunks = iter([b'$GPGGA,partial',b'',b',remainder*00\r\n'])
        def read():
            try: return next(chunks)
            except StopIteration: g._stop.set(); return b''
        port = Mock(); port.readline.side_effect = read
        context = Mock(); context.__enter__ = Mock(return_value=port); context.__exit__ = Mock()
        with patch.object(serial,'Serial',return_value=context): g._run('COM7',9600)
        g._parse.assert_called_once_with('$GPGGA,partial,remainder*00')

    def test_repeated_event_errors_are_rate_limited(self):
        d = dashboard(); d.root = Mock()
        def report(error):
            try: raise error
            except Exception: d._report_event_error()
        with patch.object(sd.time,'monotonic',return_value=100):
            for _ in range(100): report(ValueError('repeat'))
            d.root.report_callback_exception.assert_called_once()
            report(RuntimeError('different'))
            report(ValueError('different message'))
        self.assertEqual(d.root.report_callback_exception.call_count,3)
        with patch.object(sd.time,'monotonic',return_value=161): report(ValueError('repeat'))
        self.assertEqual(d.root.report_callback_exception.call_count,4)

    def test_csv_partial_write_rolls_back_before_next_row(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(sd.DataLogger,'DATA_DIR',Path(tmp)):
            logger = sd.DataLogger(); logger.log({'dl_mbps':'1'})
            real = logger._fh
            def partial(data): return real.write(data[:5])
            wrapper = Mock(wraps=real); wrapper.closed = False; wrapper.name = real.name
            wrapper.write.side_effect = partial; logger._fh = wrapper
            with self.assertRaises(OSError): logger.log({'dl_mbps':'2'})
            logger.log({'dl_mbps':'3'}); logger.close()
            with next(Path(tmp).glob('*.csv')).open(newline='') as file: rows=list(csv.DictReader(file))
            self.assertEqual([row['dl_mbps'] for row in rows],['1','3'])
            self.assertTrue(all(None not in row for row in rows))

    def test_truncated_catalogue_does_not_replace_large_cache(self):
        good = ('TEST\n1 25544U 98067A   26319.50000000  .00016717  00000-0  10270-3 0  9999\n'
                '2 25544  51.6416  77.2154 0006703 130.5360 325.0288 15.50000000400000\n')
        with tempfile.TemporaryDirectory() as tmp:
            matcher=sd.SatelliteMatcher(); matcher.CACHE=Path(tmp)/'tle.txt'
            matcher.CACHE.write_text(good*500); os.utime(matcher.CACHE,(0,0))
            with patch('urllib.request.urlopen') as request:
                for count in (1,100):
                    request.return_value.read.return_value=(good*count).encode()
                    with self.assertRaises(ValueError): matcher._refresh_cache()
            self.assertEqual(matcher.CACHE.read_text(),good*500)
            self.assertEqual(matcher.CACHE.stat().st_mtime,0)
