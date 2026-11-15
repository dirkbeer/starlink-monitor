# Independent review of the desktop reliability fixes

Compare `upstream/main` (AndyMcLeod/starlink-monitor) with this branch. Review the
implementation independently before using the list below as a coverage checklist.
Do not treat green tests or this document as proof that the implementation is right.

```bash
git fetch origin
git fetch upstream
git diff upstream/main...HEAD
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements-dev.txt
.venv/bin/python -m unittest discover -s tests -v
xvfb-run -a .venv/bin/python tests/integration.py
xvfb-run -a .venv/bin/python tests/integration.py --offline
.venv/bin/python build_exe.py --onedir
xvfb-run -a .venv/bin/python tests/smoke_build.py dist/StarlinkMonitor/StarlinkMonitor
```

If the checkout has no `upstream` remote, add it using
`git remote add upstream https://github.com/AndyMcLeod/starlink-monitor.git`.
Use the PR's exact head commit when recording review results.

The fixes address:

1. Executable protobuf files trusted in a predictable shared temporary cache.
2. Circular history ordering, uninitialized slots, reconnect duplication, and
   inconsistent historical sample timing.
3. Stale GPS fixes and zero-valued coordinates incorrectly represented in CSV.
4. Serial read failures bypassing the reconnect loop.
5. CSV rotation failures leaving an unusable writer and no visible recording error.
6. History errors blocking current status requests.
7. NMEA checksum/coordinate validation and stale satellite-count handling.
8. Wrong callback argument count when no location is available.
9. Catalogue refresh during long sessions and stale-cache visibility.
10. Disabled satellite tracking leaving stale CSV estimates or pending UI results.
11. Usage/time axes assuming fixed sampling intervals and hiding gaps.
12. Satellite and border rendering across the date line.
13. Undisclosed/default external requests and plaintext IP geolocation.
14. Non-finite telemetry poisoning chart buffers and preventing recording.

Also inspect thread shutdown, Tk event ownership, callback generations, refresh
races, source/frozen startup, and compatibility of existing CSV/config files.
Look for behavior changes that are neither necessary nor documented. Report only
verified findings with severity, exact locations, trigger/reproduction, and impact.

The integration fixture uses a real local gRPC server, real Tk widgets under Xvfb,
a pseudo-terminal with checksum-valid GPS sentences, CSV files, and synthetic
orbital elements. It includes a deliberate RPC outage and reconnection. It does
not validate a physical dish's protocol mappings or orbital identification.
Windows CI runs the same regression and GUI tests using a pyserial loopback
transport, plus native directory and single-file builds. Physical COM-device
unplug/replug and dish protocol behavior still require real hardware. Hosted CI has not executed: GitHub reports an account billing lock. Native Windows validation remains outstanding. Usage remains an
estimate; periods without observations are excluded, not invented.

Suggested prompt for Claude:

> Independently review this PR at its exact head commit. Read the diff and relevant
> surrounding code, run the documented tests, and challenge both implementation
> and test coverage. Focus on data integrity, failure recovery, concurrency,
> generated-code trust, and desktop packaging. Try to reproduce regressions and
> missed edge cases. Do not simply confirm the author's claims. Give actionable
> findings with severity and file/line references; distinguish demonstrated bugs
> from hardware-dependent uncertainties. Do not modify files or post comments
> unless asked.

Local cross-platform evidence: the regression and GUI integration checks pass
under Linux and Windows Python 3.11 under Wine. The Wine integration uses SGP4's
supported pure-Python backend because the accelerated Windows parser returned
invalid elements in that environment. CI intentionally tests the normal Windows
wheel, so a native runner must verify the accelerated path before release.

Follow-up to Claude's review of `2a1764c`: CSV headers are published atomically
and empty files reused; a real file-size-limit regression test covers failed
header writes. Packaged smoke tests require successful status/history RPCs with
a unique fixture response before checking GUI startup. TLE downloads share the
tolerant cache parser, map borders retain culled polylines, the event pump always
rearms, and CSV satellite matches use the current status sample. GPS reconnect
waits asynchronously for the old reader, and frozen imports and disabled-map
status no longer accumulate stale state. Checksums were already required by
upstream; this requirement is now explicit in the README.

The integration checks assert CSV values and ordering, wrapped history content,
and an offline startup with external requests forbidden. The regression suite
has 42 tests (the POSIX file-size-limit test is skipped on Windows). The native
CI matrices disable fail-fast so independent jobs can finish after a failure.

Dish-location follow-up: review automatic read-only `get_location` polling on a
separate connection, permission-denied backoff, missing/invalid/stale coordinate
handling, manual fallback, and explicit external-GPS selection. Run
`xvfb-run -a .venv/bin/python tests/integration.py --offline --dish-location`
for the real Tk/gRPC success-to-denial fallback scenario. No physical dish is
available yet; fixtures validate protocol handling, not access on a service plan.
Protocol field numbers are cross-checked against the generated bindings in
https://github.com/Eitol/starlink-client/blob/main/libs/golang/client/gen/spacex/api/device/device.pb.go
and the LLAPosition fields in the same project's common bindings.

Follow-up to the review at `dac05d3`: source choice now persists (legacy saved
ports migrate to external GPS), transient failures retain a fix until expiry,
and the dish-location button wakes backoff immediately. Default-zero positions
need corroborating GPS status; real hardware semantics remain unverified. CSV
fields are appended, partial writes roll back before retry, serial fragments
survive read timeouts, repeated pump errors are rate-limited, and truncated TLE
downloads preserve a larger cache. A stuck GPS shutdown reports a deadline
instead of starting a competing reader on the same port.
