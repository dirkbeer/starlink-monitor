import threading
from collections import deque
from types import SimpleNamespace as NS
from unittest.mock import Mock
import starlink_dashboard as sd

def dashboard():
    d = sd.Dashboard.__new__(sd.Dashboard)
    for name in ('card_latency', 'card_drop', 'card_dl', 'card_ul',
                 'card_obstr', 'info_panel', 'status_panel', 'ready_panel',
                 'detail_info', 'status_bar', '_logger', 'location_panel'):
        setattr(d, name, Mock())
    d.root = NS(after=lambda delay, fn, *args: fn(*args))
    d._dl_history = deque(maxlen=600)
    d._ul_history = deque(maxlen=600)
    d._draw_history = Mock()
    d._cum_dl_gb = d._cum_ul_gb = 0
    d._stop = threading.Event()
    d._post = lambda fn,*args: fn(*args)
    d._history_times = deque(maxlen=600)
    d._last_usage_time = None
    d.location_panel._dish_lat = d.location_panel._dish_lon = None
    d._location_mode = "auto"
    d._location_generation = 0
    d._manual_location = (None,None,"")
    d._dish_location_received = None
    d._dish_location_message = "Dish location unavailable"
    d._gps_received = None
    d._location_source = "manual"
    d._sat_generation = 0
    d._sat_on = False
    d._sat_loaded = False
    d._recording_error = Mock()
    d._last_gps = {}
    d._last_sat_name = d._last_sat_sep = ''
    return d


def status():
    return NS(downlink_throughput_bps=8e6, uplink_throughput_bps=0,
              boresight_elevation_deg=70, boresight_azimuth_deg=180,
              obstruction_stats=NS(fraction_obstructed=0, currently_obstructed=False),
              is_snr_above_noise_floor=True, gps_stats=NS(gps_valid=True, gps_sats=8),
              pop_ping_latency_ms=30, pop_ping_drop_rate=0, eth_speed_mbps=1000,
              device_info=NS(id='test', hardware_version='test',
                             software_version=sd.KNOWN_FIRMWARE, country_code='US'),
              device_state=NS(uptime_s=1000), ready_states=NS(),
              ned2dish_quaternion=NS(q_scalar=1, q_x=0, q_y=0, q_z=0),
              alignment_stats=NS(desired_boresight_elevation_deg=70,
                                 desired_boresight_azimuth_deg=180,
                                 attitude_uncertainty_deg=1), connected_routers=[])


