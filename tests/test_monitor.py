import importlib
import math
import os
import queue
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import Mock, patch, MagicMock
from types import SimpleNamespace as NS

import starlink_dashboard as sd
from monitor_core import history_samples, longitude_delta
from tests.helpers import dashboard, status


def sentence(payload):
    checksum = 0
    for c in payload:
        checksum ^= ord(c)
    return f'${payload}*{checksum:02X}'


def gps():
    g = sd.GpsReader.__new__(sd.GpsReader)
    g._last_gga = 0
    g._gga_quality = g._gga_sats = 0
    g._on_update = Mock()
    return g


def history(current=10):
    raw = [8,9,2,3,4,5,6,7]
    return NS(current=current, downlink_throughput_bps=[x*1e6 for x in raw],
              uplink_throughput_bps=[0]*8, pop_ping_latency_ms=raw,
              pop_ping_drop_rate=[0]*8)


class RegressionTests(unittest.TestCase):
    def test_proto_uses_private_directory_and_generates_both_modules(self):
        sd.ensure_proto_compiled()
        self.assertNotEqual(sd.PROTO_DIR, Path(tempfile.gettempdir())/'starlink_proto')
        if os.name != "nt":
            self.assertEqual(sd.PROTO_DIR.stat().st_mode & 0o077, 0)
        for name in ('starlink_pb2', 'starlink_pb2_grpc'):
            module = importlib.import_module(name)
            self.assertEqual(Path(module.__file__).parent, sd.PROTO_DIR)

    def test_history_is_chronological_and_timed(self):
        rows = history_samples(history(),2,100)
        self.assertEqual([r[1] for r in rows], [3,5,7,9])
        self.assertEqual([r[0] for r in rows], [94,96,98,100])

    def test_history_excludes_uninitialized_slots(self):
        rows = history_samples(history(2),1,100)
        self.assertEqual([r[1] for r in rows], [8,9])
        self.assertEqual(history_samples(history(0)), [])

    def test_reseeding_replaces_overlap(self):
        d = dashboard()
        d._seed_history(history(),100)
        d._seed_history(history(),100)
        self.assertEqual(list(d._dl_history),[3,5,7,9])
        self.assertEqual(len(d._history_times),4)
        d.card_dl.spark.seed.assert_called_with([3,5,7,9],[94,96,98,100])

    def test_history_failure_does_not_block_status(self):
        d = dashboard()
        client = Mock()
        client.get_history.side_effect = RuntimeError('no history')
        client.get_status.return_value = status()
        d._error_count = 0
        d._connect = lambda: setattr(d,'_client',client)
        d._maybe_match_satellite = d._update_sky_map = Mock(return_value=None)
        d._stop = Mock()
        d._stop.is_set.side_effect = [False,True]
        d._poll_loop()
        client.get_status.assert_called_once()
        d._logger.log.assert_called_once()

    def test_lost_fix_invalidates_csv_and_matching_position(self):
        d = dashboard()
        d._apply_gps_update(48,11,1,8)
        d._apply_gps_update(None,None,0,0)
        d._apply_status(status(),100)
        row = d._logger.log.call_args.args[0]
        self.assertEqual(row['gps_quality'],0)
        self.assertEqual(row['gps_lat'],'')
        self.assertIsNone(d.location_panel._dish_lat)

    def test_silent_receiver_expires(self):
        d = dashboard()
        with patch.object(sd.time,'monotonic',return_value=100):
            d._apply_gps_update(48,11,1,8)
        with patch.object(sd.time,'monotonic',return_value=111):
            d._expire_gps()
        self.assertEqual(d._last_gps['quality'],0)

    def test_zero_coordinates_are_logged(self):
        d = dashboard()
        d._apply_gps_update(0,0,1,8)
        d._apply_status(status(),100)
        row = d._logger.log.call_args.args[0]
        self.assertEqual((row['gps_lat'],row['gps_lon']),('0.000000','0.000000'))

    def test_nmea_checksum_coordinate_and_quality_validation(self):
        g = gps()
        payload = 'GPGGA,123519,4807.038,N,01131.000,E,4,08,0.9,545.4,M,46.9,M,,'
        g._parse('$'+payload+'*00')
        g._on_update.assert_not_called()
        g._parse(sentence(payload))
        self.assertEqual(g._on_update.call_args.args[2:],(4,8))
        g._parse(sentence('GPRMC,123519,A,4807.038,N,01131.000,E,0,0,010100'))
        self.assertEqual(g._on_update.call_args.args[2:],(4,8))
        for value,hemi in [('1260','N'),('nan','N'),('9100','N'),('1200','Q')]:
            self.assertIsNone(sd._nmea_to_deg(value,hemi))

    def test_fix_satellite_counts_can_decrease(self):
        g = gps()
        for count in (12,4):
            g._parse(sentence(f'GPGGA,123519,4807.038,N,01131.000,E,1,{count:02},0.9,545.4,M,46.9,M,,'))
        self.assertEqual(g._on_update.call_args.args[3],4)

    def test_serial_failure_closes_and_reopens(self):
        import serial
        g = gps()
        g._stop = threading.Event()
        g._on_raw = None
        broken = MagicMock()
        broken.__enter__.return_value.readline.side_effect = serial.SerialException('unplugged')
        healthy = MagicMock()
        def stop_read():
            g._stop.set()
            return b''
        healthy.__enter__.return_value.readline.side_effect = stop_read
        with patch.object(serial,'Serial',side_effect=[broken,healthy]) as factory, \
             patch.object(g._stop,'wait',return_value=False):
            g._run('test',9600)
        self.assertEqual(factory.call_count,2)
        broken.__exit__.assert_called_once()
        g._on_update.assert_called_with(None,None,-2,0)

    def test_rotation_recovers_and_recording_errors_are_visible(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(sd.DataLogger,'DATA_DIR',Path(tmp)):
            logger = sd.DataLogger()
            logger.log({})
            logger._date = None
            with patch('builtins.open',side_effect=OSError('disk full')):
                with self.assertRaises(OSError): logger.log({})
            logger.log({'dl_mbps':'8'})
            logger.close()
        d = dashboard()
        d._logger.log.side_effect = OSError('disk full')
        d._apply_status(status(),100)
        self.assertIn('NOT RECORDING',d._recording_error.set.call_args.args[0])
        d._logger.log.side_effect = None
        d._apply_status(status(),102)
        d._recording_error.set.assert_called_with('')

    def test_no_location_map_and_obsolete_callbacks(self):
        d = dashboard()
        d._sat_on = d._sat_loaded = True
        d.location_panel._dish_lat = d.location_panel._dish_lon = None
        d.sky_map = sd.SkyMapPanel.__new__(sd.SkyMapPanel)
        d.sky_map._redraw = Mock()
        d._update_sky_map()
        self.assertEqual(d.sky_map._last,(None,)*7)
        d._sat_on = False
        d._deliver_sky_map(0,1,2,None,None,None,None,None)
        self.assertEqual(d.sky_map._last,(None,)*7)

    def test_disable_tracking_invalidates_csv_and_pending_matches(self):
        d = dashboard()
        d._sat_enabled = NS(get=lambda:False)
        d._sat_status_var = Mock()
        d.sky_map = Mock()
        d._last_sat_name,d._last_sat_sep = 'old','1'
        d._on_toggle_sat()
        d._deliver_sat_match(0,('old',1,1,1),None)
        d._apply_status(status(),100)
        self.assertEqual(d._logger.log.call_args.args[0]['likely_sat'],'')

    def test_tle_refresh_swaps_complete_matcher_and_retains_old_on_failure(self):
        d = dashboard()
        d._sat_on = True
        d._sat_status_var = Mock()
        old,new = object(),object()
        d._sat_matcher = old
        d._finish_sat_load(new,False,'network unavailable')
        self.assertIs(d._sat_matcher,old)
        d._finish_sat_load(new,True,'fresh')
        self.assertIs(d._sat_matcher,new)
        self.assertTrue(d._sat_loaded)
        self.assertFalse(d._sat_loading)

    def test_running_session_schedules_catalogue_refresh_once(self):
        d = dashboard()
        d.root = Mock()
        d._events = queue.Queue()
        d._sat_on = True
        d._sat_loading = False
        d._sat_refresh_at = 0
        with patch.object(sd.threading,'Thread') as worker:
            d._drain_events()
            d._drain_events()
        worker.assert_called_once()
        worker.return_value.start.assert_called_once()

    def test_invalid_cached_orbits_are_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            cache = Path(tmp)/'tle.txt'
            cache.write_text('INVALID\n'
                '1 25544U 98067A   26319.50000000  .00016717  00000-0  10270-3 0  9999\n'
                '2 25544  51.6416  77.2154 0006703 130.5360 325.0288 00.00000000400000\n')
            matcher = sd.SatelliteMatcher()
            matcher.CACHE = cache
            with patch.object(matcher,'_refresh_cache'):
                ok, message = matcher.load()
            self.assertFalse(ok)
            self.assertIn('no TLEs',message)
            self.assertIsNone(matcher._array)

    def test_stale_gps_callbacks_from_replaced_reader_are_ignored(self):
        d = dashboard()
        d._gps_generation = 2
        d._deliver_gps(1,48,11,1,8)
        self.assertEqual(d._last_gps,{})
        d._deliver_gps(2,48,11,1,8)
        self.assertEqual(d._last_gps['quality'],1)

    def test_usage_uses_measured_time_and_skips_unobserved_gaps(self):
        d = dashboard()
        d._apply_status(status(),100)
        d._apply_status(status(),103)
        self.assertAlmostEqual(d._cum_dl_gb,0.003)
        d._apply_status(status(),130)
        self.assertAlmostEqual(d._cum_dl_gb,0.003)
        self.assertIsNone(d._dl_history[-2])
        self.assertEqual(list(d._history_times),[100,103,105,130])

    def test_dateline_keeps_nearby_satellites_visible(self):
        self.assertEqual(longitude_delta(-179,179),2)
        panel = sd.SkyMapPanel.__new__(sd.SkyMapPanel)
        panel.canvas = Mock()
        panel.canvas.winfo_width.return_value = 900
        panel.canvas.winfo_height.return_value = 600
        panel.enabled = True
        panel.borders = None
        panel._last = (0,179,dict(name=['TEST'],sublat=[0],sublon=[-179]),'', '',None,None)
        panel._redraw_impl()
        texts = [c.kwargs.get('text','') for c in panel.canvas.create_text.call_args_list]
        self.assertIn('1 sats shown · 450 km L/R',texts)

    def test_nonfinite_samples_do_not_poison_logging_or_usage(self):
        d = dashboard()
        value = status()
        value.downlink_throughput_bps = float('inf')
        value.pop_ping_latency_ms = float('nan')
        d._apply_status(value,100)
        row = d._logger.log.call_args.args[0]
        self.assertEqual(row['dl_mbps'],'')
        self.assertEqual(row['latency_ms'],'')
        self.assertIsNone(d._dl_history[-1])
        self.assertEqual(d._cum_dl_gb,0)
        self.assertEqual(sd._nice_ticks(0,float('inf')),[])
        d._apply_status(status(),102)
        self.assertEqual(d._logger.log.call_args.args[0]['dl_mbps'],'8.000')


if __name__ == '__main__':
    unittest.main()
