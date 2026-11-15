"""Run the real Tk/gRPC/serial/CSV/SGP4 stack against isolated local fixtures.

xvfb-run -a .venv/bin/python tests/integration.py
Creates artifacts only under tests/runtime-artifacts (gitignored).
"""
import csv
import datetime
import json
import math
import os
from pathlib import Path
from unittest.mock import patch
import sys
import tempfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import grpc
from PIL import ImageGrab
import starlink_dashboard as sd

artifacts = Path(__file__).resolve().parent / 'runtime-artifacts'
artifacts.mkdir(exist_ok=True)
sd.EXTERNAL_SERVICES = True
sd.APP_DIR = artifacts
sd.LOCATION_FILE = artifacts / 'location.json'
sd.DataLogger.DATA_DIR = Path(tempfile.mkdtemp(prefix='csv-',dir=artifacts))
sd.PROTO_DIR = artifacts / 'proto'
sd.PROTO_FILE = sd.PROTO_DIR / 'starlink.proto'
sd.SatelliteMatcher.CACHE = artifacts / 'tle.txt'
sd.BorderMap.CACHE = artifacts / 'borders.json'
# A controlled two-body fixture, not a claim about a real Starlink satellite.
sd.SatelliteMatcher.CACHE.write_text(
    'STARLINK-TEST\n'
    '1 25544U 98067A   26319.50000000  .00016717  00000-0  10270-3 0  9999\n'
    '2 25544  51.6416  77.2154 0006703 130.5360 325.0288 15.50000000400000\n')
sd.BorderMap.CACHE.write_text(json.dumps({'features': [{'geometry': {
    'type': 'Polygon', 'coordinates': [[[-125,45],[-120,45],[-120,50],[-125,45]]]}}]}))
sd.fetch_geolocation = lambda: dict(lat=47.6, lon=-122.3, city='Fixture',
    regionName='Fixture', isp='Test', query='127.0.0.1')
sd.ensure_proto_compiled()
import starlink_pb2 as pb
import starlink_pb2_grpc as pbgrpc

counts = {'status': 0, 'history': 0, 'outages': 0}
started = time.monotonic()
class Dish(pbgrpc.DeviceServicer):
    def Handle(self, request, context):
        if request.WhichOneof('request') == 'get_history':
            counts['history'] += 1
            return pb.Response(dish_get_history=pb.DishGetHistoryResponse(
                current=900, downlink_throughput_bps=[8e6]*900,
                uplink_throughput_bps=[1e6]*900,
                pop_ping_latency_ms=[30]*900, pop_ping_drop_rate=[0]*900))
        counts['status'] += 1
        if 5 < time.monotonic()-started < 8:
            counts['outages'] += 1
            context.abort(grpc.StatusCode.UNAVAILABLE, 'injected outage')
        return pb.Response(dish_get_status=pb.DishGetStatusResponse(
            device_info=pb.DeviceInfo(id='REVIEW', hardware_version='TEST',
                software_version=sd.KNOWN_FIRMWARE, country_code='US'),
            device_state=pb.DeviceState(uptime_s=1200),
            downlink_throughput_bps=float("nan") if counts["status"] == 2 else 8e6, uplink_throughput_bps=1e6,
            pop_ping_latency_ms=30, boresight_azimuth_deg=180,
            boresight_elevation_deg=70, is_snr_above_noise_floor=True,
            gps_stats=pb.DishGpsStats(gps_valid=True,gps_sats=8),
            ready_states=pb.DishReadyStates(cady=True,scp=True,l1l2=True,xphy=True,aap=True,rf=True),
            ned2dish_quaternion=pb.Quaternion(q_scalar=1)))

server = grpc.server(ThreadPoolExecutor(max_workers=2))
pbgrpc.add_DeviceServicer_to_server(Dish(),server)
port = server.add_insecure_port('127.0.0.1:0')
sd.DISH_HOST = f'127.0.0.1:{port}'
server.start()
if os.name == "nt":
    import serial
    serial_loop = serial.serial_for_url('loop://', baudrate=9600, timeout=0.2)
    serial_patch = patch.object(serial, 'Serial', return_value=serial_loop)
    serial_patch.start()
    gps_port = 'COM99'
    write_gps = serial_loop.write
else:
    import pty
    master, slave = pty.openpty()
    gps_port = os.ttyname(slave)
    write_gps = lambda data: os.write(master, data)
sd.save_gps_port(gps_port)
sd.save_location(48.1173,11.5167,'Fixture')
stop = threading.Event()
def gps_writer():
    while not stop.wait(0.3):
        write_gps(b'$GPGGA,123519,4807.038,N,01131.000,E,1,08,0.9,545.4,M,46.9,M,,*47\r\n')
threading.Thread(target=gps_writer,daemon=True).start()

root = sd.tk.Tk()
sd._setup_crash_logging(root)
errors = []
original_report = root.report_callback_exception
def report(exc,value,tb):
    errors.append(str(value))
    original_report(exc,value,tb)
root.report_callback_exception = report
app = sd.Dashboard(root)
observations = {}
def capture(name):
    try:
        ImageGrab.grab(xdisplay=None if os.name == 'nt' else os.environ.get('DISPLAY')).save(artifacts/name)
    except OSError as error:
        observations['screenshot_error'] = str(error)

def finish():
    try:
        observations['metric'] = app.card_dl.val_var.get()
        observations['gps'] = app._last_gps
        observations['sat_loaded'] = app._sat_loaded
        observations['counts'] = counts.copy()
        observations['gui_errors'] = errors[:]
        observations['history_size'] = len(app._dl_history)
        app._detail.geometry('900x680+650+350')
        root.update()
        capture('gui.png')
        app._detail.lift()
        root.update()
        capture('detail.png')
        matcher = app._sat_matcher
        matcher.MIN_EL = -90
        match = matcher.match(48.1173,11.5167,180,70)
        observations['sgp4_match'] = match
        assert match and all(math.isfinite(x) for x in match[1:])
        assert observations['metric'] == '8.0'
        assert app._last_gps['quality'] == 1
        assert counts['status'] >= 4 and counts['history'] >= 2 and counts['outages'] >= 1
        assert app._sat_loaded and not errors
        with next(sd.DataLogger.DATA_DIR.glob('starlink_*.csv')).open() as f:
            rows = list(csv.DictReader(f))
        observations['csv_rows'] = len(rows)
        assert len(rows) >= 3
        assert rows[-1]['dl_mbps'] == '8.000'
        assert any(row['dl_mbps'] == '' for row in rows), 'Invalid telemetry must still be recorded'
        observations['passed'] = True
    except Exception as e:
        observations['passed'] = False
        observations['failure'] = repr(e)
    finally:
        stop.set()
        app._gps_reader.stop()
        app._on_close()
root.after(14000,finish)
root.mainloop()
server.stop(0)
if os.name == "nt":
    serial_patch.stop()
    serial_loop.close()
else:
    os.close(master)
    os.close(slave)
(artifacts/'result.json').write_text(json.dumps(observations,indent=2))
print(json.dumps(observations,indent=2))
raise SystemExit(0 if observations.get('passed') else 1)
