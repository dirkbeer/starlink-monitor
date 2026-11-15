# Independent review of the Linux reliability fixes

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
.venv/bin/python build_exe.py --onedir --console
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
Windows is explicitly outside this fork's validation scope. Usage remains an
estimate; periods without observations are excluded, not invented.

Suggested prompt for Claude:

> Independently review this PR at its exact head commit. Read the diff and relevant
> surrounding code, run the documented tests, and challenge both implementation
> and test coverage. Focus on data integrity, failure recovery, concurrency,
> generated-code trust, and Linux packaging. Try to reproduce regressions and
> missed edge cases. Do not simply confirm the author's claims. Give actionable
> findings with severity and file/line references; distinguish demonstrated bugs
> from hardware-dependent uncertainties. Do not modify files or post comments
> unless asked.
