from concurrent.futures import ThreadPoolExecutor
import time
import unittest
from unittest.mock import Mock, patch
import grpc
import starlink_dashboard as sd
from tests.helpers import dashboard, status

class LocationTests(unittest.TestCase):
    def test_location_rpc_presence_validation_and_denial(self):
        sd.ensure_proto_compiled()
        import starlink_pb2 as pb
        import starlink_pb2_grpc as rpc
        class Dish(rpc.DeviceServicer):
            response = pb.Response(get_location=pb.GetLocationResponse(lla=pb.LLAPosition(lat=0,lon=0)))
            deny = False
            def Handle(self, request, context):
                if self.deny:
                    context.abort(grpc.StatusCode.PERMISSION_DENIED,'not shared')
                assert request.WhichOneof('request') == 'get_location'
                return self.response
        dish = Dish()
        server = grpc.server(ThreadPoolExecutor(max_workers=2))
        rpc.add_DeviceServicer_to_server(dish,server)
        port = server.add_insecure_port('127.0.0.1:0'); server.start()
        with patch.object(sd,'DISH_HOST',f'127.0.0.1:{port}'):
            client = sd.StarlinkClient()
        try:
            self.assertEqual(client.get_location(),(0,0))
            for response in (pb.Response(),pb.Response(get_location=pb.GetLocationResponse()),
                             pb.Response(get_location=pb.GetLocationResponse(lla=pb.LLAPosition(lat=float('nan')))),
                             pb.Response(get_location=pb.GetLocationResponse(lla=pb.LLAPosition(lat=91)))):
                dish.response = response
                with self.assertRaises(ValueError): client.get_location()
            dish.deny = True
            with self.assertRaises(grpc.RpcError) as error: client.get_location()
            self.assertEqual(error.exception.code(),grpc.StatusCode.PERMISSION_DENIED)
        finally:
            client.close(); server.stop(0).wait()

    def test_dish_location_fallback_expiry_and_generation(self):
        d = dashboard()
        d._manual_location = (12,34,'Home')
        d._apply_dish_location(0,(0,0),'Available',time.monotonic())
        self.assertEqual(d._location_source,'dish')
        d._dish_location_received -= sd.GPS_MAX_AGE+1
        d._expire_gps()
        self.assertEqual(d._location_source,'manual')
        d.location_panel.set_dish_location.assert_called_with(12,34,'Home')
        d._location_mode = 'external'
        d._apply_dish_location(0,(1,2),'Available',time.monotonic())
        self.assertEqual(d._location_source,'manual')
        d._location_mode = 'auto'; d._location_generation = 1
        d._apply_dish_location(0,(1,2),'Available',time.monotonic())
        self.assertEqual(d._location_source,'manual')

    def test_denied_location_backs_off_without_touching_telemetry(self):
        d = dashboard()
        class Denied(grpc.RpcError):
            def code(self): return grpc.StatusCode.PERMISSION_DENIED
        client = Mock(); client.get_location.side_effect = Denied()
        d._stop = Mock(); d._stop.is_set.side_effect = [False,True]
        with patch.object(sd,'StarlinkClient',return_value=client): d._location_loop()
        d._stop.wait.assert_called_once_with(60)
        self.assertEqual(d._location_source,'none')
        d._logger.log.assert_not_called()

    def test_selected_location_logged_separately_from_external_gps(self):
        d = dashboard(); d._location_source = 'dish'
        d._dish_location_received = time.monotonic()
        d.location_panel._dish_lat,d.location_panel._dish_lon = 0,0
        d._apply_status(status(),100)
        row = d._logger.log.call_args.args[0]
        self.assertEqual((row['location_source'],row['location_lat'],row['location_lon']),('dish','0.000000','0.000000'))
        self.assertEqual(row['gps_lat'],'')
