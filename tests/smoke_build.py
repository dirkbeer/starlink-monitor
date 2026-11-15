"""Verify bundled RPC transport, then verify GUI startup (including windowed builds)."""
from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import uuid

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import grpc
import starlink_dashboard as sd


def terminate(process):
    if os.name == 'nt':
        subprocess.run(['taskkill','/PID',str(process.pid),'/T','/F'],
                       capture_output=True, check=False)
    else:
        process.terminate()
    process.wait(timeout=10)


def check_protocol(command):
    sd.ensure_proto_compiled()
    import starlink_pb2 as pb
    import starlink_pb2_grpc as rpc
    token = uuid.uuid4().hex
    class Dish(rpc.DeviceServicer):
        def Handle(self, request, context):
            if request.WhichOneof('request') == 'get_status':
                return pb.Response(dish_get_status=pb.DishGetStatusResponse(
                    device_info=pb.DeviceInfo(id=token), pop_ping_latency_ms=42))
            return pb.Response(dish_get_history=pb.DishGetHistoryResponse(
                current=3,pop_ping_drop_rate=[0,0,0]))
    server = grpc.server(ThreadPoolExecutor(max_workers=2))
    rpc.add_DeviceServicer_to_server(Dish(),server)
    port = server.add_insecure_port('127.0.0.1:0')
    server.start()
    try:
        with tempfile.TemporaryDirectory() as tmp:
            result_file = Path(tmp)/'result.json'
            process = subprocess.Popen([*command,'--selftest',str(result_file),
                '--dish-host',f'127.0.0.1:{port}'],
                stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
            try:
                code = process.wait(timeout=30)
            except subprocess.TimeoutExpired:
                terminate(process)
                raise AssertionError('Bundled protocol self-test did not exit')
            assert code == 0, f'Protocol self-test exited {code}'
            result = json.loads(result_file.read_text())
            assert result == {'ok':True,'device_id':token,'latency_ms':42.0,'history_samples':3}, result
    finally:
        server.stop(0).wait()


def check_gui(binary):
    diagnostics = [binary.parent/'data'/name for name in ('console.log','crash.log')]
    offsets = {path: path.stat().st_size if path.exists() else 0 for path in diagnostics}
    with tempfile.TemporaryDirectory() as tmp:
        log = Path(tmp)/'startup.log'
        with log.open('w') as output:
            process = subprocess.Popen([str(binary)], stdout=output, stderr=subprocess.STDOUT,
                                       env={**os.environ,'STARLINK_EXTERNAL_SERVICES':'0'})
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                terminate(process)
            else:
                raise AssertionError(f'GUI exited during startup: {process.returncode}')
        text = log.read_text(errors='replace')
        for path in diagnostics:
            if path.exists():
                with path.open('rb') as diagnostic:
                    diagnostic.seek(offsets[path])
                    text += diagnostic.read().decode(errors='replace')
        assert not any(marker in text for marker in
                       ('Traceback','Failed to compile proto','Failed to execute')), text


if __name__ == '__main__':
    binary = Path(sys.argv[1]).resolve()
    check_protocol([str(binary)])
    check_gui(binary)
    print('Bundled RPC and GUI startup passed')
