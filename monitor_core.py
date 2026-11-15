"""Pure telemetry transformations shared by the UI and regression tests."""
import math


def finite(value):
    return value is not None and math.isfinite(value)


def formatted(value, spec):
    return format(value, spec) if finite(value) else ""


def history_samples(history, interval=2, now=0):
    """Unwrap the dish ring, exclude uninitialized slots, and return timed rows.

    Each tuple is (timestamp, download Mbps, upload Mbps, latency, loss percent).
    Select newest samples on a consistent cadence, including the latest slot.
    """
    fields = [history.downlink_throughput_bps, history.uplink_throughput_bps,
              history.pop_ping_latency_ms, history.pop_ping_drop_rate]
    size = len(fields[3])
    current = int(history.current)
    count = min(current, size)
    if not count:
        return []
    step = max(1, int(interval))
    rows = []
    for counter in range(current - 1, current - count - 1, -step):
        i = counter % size
        values = [f[i] if i < len(f) and finite(f[i]) else None for f in fields]
        rows.append((now - (current - 1 - counter),
                     values[0] / 1e6 if values[0] is not None else None,
                     values[1] / 1e6 if values[1] is not None else None,
                     values[2], values[3] * 100 if values[3] is not None else None))
    return rows[::-1]


def longitude_delta(lon, origin):
    return (lon - origin + 180) % 360 - 180


def visible_border_paths(lons, lats, bbox, origin, half_width, south, north):
    """Cull geographically, preserving polylines and splitting only wrap jumps."""
    if bbox[3] < south or bbox[1] > north:
        return []
    span = bbox[2] - bbox[0]
    if span <= 180:
        center = longitude_delta((bbox[0] + bbox[2]) / 2, origin)
        if center + span/2 < -half_width or center - span/2 > half_width:
            return []
    paths, current = [], []
    for lon, lat in zip(lons, lats):
        delta = longitude_delta(lon, origin)
        if current and abs(delta-current[-1][0]) > 180:
            paths.append(current)
            current = []
        current.append((delta, lat))
    paths.append(current)
    return [path for path in paths if len(path) >= 2
            and min(p[0] for p in path) <= half_width
            and max(p[0] for p in path) >= -half_width
            and min(p[1] for p in path) <= north
            and max(p[1] for p in path) >= south]


def draw_segments(canvas, points, **kwargs):
    """Draw finite runs, leaving visible gaps for missing observations."""
    run = []
    for point in list(points) + [None]:
        if point is None:
            if len(run) >= 4:
                canvas.create_line(*run, **kwargs)
            run = []
        else:
            run.extend(point)
