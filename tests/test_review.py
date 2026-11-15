import csv
import datetime
import json
import os
from pathlib import Path
import queue
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

import starlink_dashboard as sd
from monitor_core import visible_border_paths
from tests.helpers import dashboard, status
from tests.smoke_build import check_protocol


class ReviewRegressions(unittest.TestCase):
    @unittest.skipIf(os.name == 'nt', 'POSIX resource limit reproduction')
    def test_failed_header_does_not_accumulate_files(self):
        import resource
        import signal
        for limit in (0,16):
            with self.subTest(limit=limit), tempfile.TemporaryDirectory() as tmp, \
                 patch.object(sd.DataLogger,'DATA_DIR',Path(tmp)):
                logger = sd.DataLogger()
                filename = Path(tmp)/f'starlink_{datetime.datetime.now(datetime.timezone.utc).date()}.csv'
                filename.touch()  # existing empty files must be reusable too
                previous_limit = resource.getrlimit(resource.RLIMIT_FSIZE)
                previous_signal = signal.signal(signal.SIGXFSZ,signal.SIG_IGN)
                try:
                    resource.setrlimit(resource.RLIMIT_FSIZE,(limit,previous_limit[1]))
                    for _ in range(5):
                        with self.assertRaises(OSError): logger.log({})
                finally:
                    resource.setrlimit(resource.RLIMIT_FSIZE,previous_limit)
                    signal.signal(signal.SIGXFSZ,previous_signal)
                self.assertEqual(list(Path(tmp).iterdir()),[filename])
                self.assertEqual(filename.stat().st_size,0)
                logger.log({'dl_mbps':'8'})
                logger.close()
                with filename.open(newline='') as file:
                    rows = list(csv.DictReader(file))
                self.assertEqual(len(rows),1)
                self.assertEqual(rows[0]['dl_mbps'],'8')

    def test_selftest_reports_client_import_failure(self):
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp)/'result.json'
            with patch.object(sd,'StarlinkClient',side_effect=ImportError('missing pb2')):
                self.assertEqual(sd.protocol_selftest(output),1)
            self.assertFalse(json.loads(output.read_text())['ok'])

    def test_packaging_protocol_gate_exercises_rpc_and_rejects_broken_client(self):
        check_protocol([sys.executable,str(Path(sd.__file__).resolve())])
        script = ('import starlink_dashboard as s; '
                  's.StarlinkClient=lambda: (_ for _ in ()).throw(ImportError("missing pb2")); s.main()')
        with self.assertRaisesRegex(AssertionError,'exited 1'):
            check_protocol([sys.executable,'-c',script])

    def test_tle_download_accepts_good_records_among_bad(self):
        good = ('TEST\n'
                '1 25544U 98067A   26319.50000000  .00016717  00000-0  10270-3 0  9999\n'
                '2 25544  51.6416  77.2154 0006703 130.5360 325.0288 15.50000000400000\n')
        bad = good.replace('15.50000000','00.00000000')
        data = good*50 + bad
        with tempfile.TemporaryDirectory() as tmp:
            matcher = sd.SatelliteMatcher()
            matcher.CACHE = Path(tmp)/'catalogue.txt'
            with patch('urllib.request.urlopen') as request:
                request.return_value.read.return_value = data.encode()
                ok,_ = matcher.load()
            self.assertTrue(ok)
            self.assertEqual(len(matcher._names),50)
            self.assertTrue(matcher.CACHE.exists())

    def test_border_culling_and_wrap_preserve_polylines(self):
        near = visible_border_paths([-123,-122,-121],[47,48,47],(-123,47,-121,48),-122,5,45,50)
        self.assertEqual(len(near),1)
        self.assertEqual(len(near[0]),3)
        self.assertEqual(visible_border_paths([10,11],[47,48],(10,47,11,48),-122,5,45,50),[])
        wrapped = visible_border_paths([178,179,-179,-178],[0]*4,(-179,0,179,0),179,5,-5,5)
        self.assertEqual(len(wrapped),1)
        self.assertEqual([p[0] for p in wrapped[0]],[-1,0,2,3])
        antipodal = visible_border_paths([178,179,-179,-178],[0]*4,(-179,0,179,0),0,5,-5,5)
        self.assertEqual(antipodal,[])

    def test_pump_survives_maintenance_exception(self):
        d = dashboard()
        d.root = Mock()
        d._events = queue.Queue()
        d._expire_gps = Mock(side_effect=RuntimeError('maintenance failure'))
        d._drain_events()
        d.root.after.assert_called_once_with(100,d._drain_events)
        d.root.report_callback_exception.assert_called_once()
        callback = Mock()
        d._events.put((callback,()))
        d._expire_gps.side_effect = None
        d._drain_events()
        callback.assert_called_once()

    def test_pump_rearms_when_worker_start_fails(self):
        d = dashboard()
        d.root = Mock()
        d._events = queue.Queue()
        d._sat_on = True
        d._sat_loading = False
        d._sat_refresh_at = 0
        with patch.object(sd.threading,'Thread') as worker:
            worker.return_value.start.side_effect = RuntimeError('thread unavailable')
            d._drain_events()
        self.assertFalse(d._sat_loading)
        d.root.after.assert_called_once_with(100,d._drain_events)

    def test_csv_match_uses_current_sample_pointing(self):
        d = dashboard()
        d._sat_on = d._sat_loaded = True
        d._sat_status_var = Mock()
        d._sat_base_status = ''
        d.location_panel._dish_lat,d.location_panel._dish_lon = 48,11
        d._last_boresight = (10,20)
        d._sat_matcher = Mock()
        d._sat_matcher.match.side_effect = lambda lat,lon,az,el,**kw: (f'AT-{az}',1,az,el)
        current = status()
        current.boresight_azimuth_deg = 123
        match = d._maybe_match_satellite(current,100)
        d._apply_status(current,100,match)
        self.assertEqual(d._logger.log.call_args.args[0]['likely_sat'],'AT-123')
        current.boresight_azimuth_deg = 234
        match = d._maybe_match_satellite(current,102)
        d._apply_status(current,102,match)
        self.assertEqual(d._logger.log.call_args.args[0]['likely_sat'],'AT-234')

    def test_frozen_path_is_not_appended_on_each_reconnect(self):
        before = sys.path[:]
        try:
            with patch.object(sd,'FROZEN',True):
                for _ in range(10): sd.ensure_proto_compiled()
            self.assertLessEqual(len(sys.path),len(before)+1)
        finally:
            sys.path[:] = before
