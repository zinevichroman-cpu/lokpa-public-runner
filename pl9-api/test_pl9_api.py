import datetime as dt
import json
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
from pathlib import Path

import pl9_api


class PL9ApiContractTests(unittest.TestCase):
    def setUp(self):
        self.birth = {
            "name": "Example Native",
            "gender": "male",
            "date": "2000-01-01",
            "time": "12:00:00",
            "utc_offset_hours": 0,
            "dst_hours": 0,
            "longitude_east_deg": 0,
            "latitude_north_deg": 0,
            "city": "Test City",
            "state": "Test Region",
            "country": "Test Country",
        }

    def test_local_julian_day_reference(self):
        value = pl9_api.julian_day_local(dt.datetime(2000, 1, 1, 12, 0, 0))
        self.assertAlmostEqual(value, 2451545.0, places=8)

    def test_xml_uses_native_birthdata_structure_and_pl9_sign_conventions(self):
        xml = pl9_api.build_birth_xml(self.birth)
        self.assertIn(b"<!DOCTYPE BirthData>", xml)
        root = ET.fromstring(xml.split(b"\n", 2)[2])
        self.assertEqual(root.tag, "BirthData")
        self.assertEqual(root.findtext("./BirthInfo/FirstName"), "Example")
        self.assertEqual(root.findtext("./BirthInfo/LastName"), "Native")
        self.assertEqual(root.findtext("./BirthInfo/Gender"), "0")
        self.assertEqual(root.findtext("./BirthInfo/BirthDate"), "2451545.000000000000")
        self.assertAlmostEqual(float(root.findtext("./BirthInfo/Longitude")), 0, places=8)
        self.assertAlmostEqual(float(root.findtext("./BirthInfo/Latitude")), 0, places=8)
        self.assertEqual(root.findtext("./BirthInfo/TimeZone"), "0.000000000000")
        self.assertEqual(root.findtext("./BirthInfo/DST"), "0.000000000000")
        self.assertEqual(root.findtext("./BirthInfo/BDLoopUpMode"), "D")

    def test_gender_and_coordinates_are_validated(self):
        with self.assertRaises(pl9_api.RequestError):
            pl9_api.build_birth_xml(dict(self.birth, gender=True))
        with self.assertRaises(pl9_api.RequestError):
            pl9_api.build_birth_xml(dict(self.birth, latitude_north_deg=91))

    def test_http_api_creates_xml_and_reports_no_calculation_claim(self):
        with tempfile.TemporaryDirectory() as td:
            root_dir = Path(td)
            config = pl9_api.Config(
                exe=None,
                charts_dir=root_dir / "charts",
                export_dir=root_dir / "export",
                host="127.0.0.1",
                port=0,
                api_key="test-key",
            )
            server = pl9_api.make_server(config)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                url = f"http://127.0.0.1:{server.server_address[1]}/horoscope"
                payload = json.dumps({"birth": self.birth, "open": False}).encode()
                request = urllib.request.Request(
                    url, data=payload, method="POST",
                    headers={"Content-Type": "application/json", "X-API-Key": "test-key"},
                )
                with urllib.request.urlopen(request, timeout=3) as response:
                    result = json.loads(response.read())
                    self.assertEqual(response.status, 201)
                self.assertEqual(result["file_status"], "CREATED")
                self.assertFalse(result["calculation_verified"])
                chart_id = result["chart_id"]
                chart_path = config.charts_dir / f"{chart_id}.xml"
                self.assertTrue(chart_path.is_file())

                get_request = urllib.request.Request(
                    f"http://127.0.0.1:{server.server_address[1]}/charts/{chart_id}",
                    headers={"X-API-Key": "test-key"},
                )
                with urllib.request.urlopen(get_request, timeout=3) as response:
                    info = json.loads(response.read())
                self.assertEqual(info["status"], "NATIVE_PL9_XML_PRESENT")
                self.assertFalse(info["calculation_verified"])

                unauthorized = urllib.request.Request(
                    f"http://127.0.0.1:{server.server_address[1]}/health",
                    headers={"X-API-Key": "wrong"},
                )
                with self.assertRaises(urllib.error.HTTPError) as ctx:
                    urllib.request.urlopen(unauthorized, timeout=3)
                self.assertEqual(ctx.exception.code, 401)
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=2)

    def test_export_parser_preserves_33_tab_fields_and_known_groups(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "birthdat.txt"
            fields = [
                "Example Native", "0", "1", "Test City", "Test Region", "Test Country", "01:01:2000",
                "12:00:00", "2451545.0", "0", "0", "0", "0",
            ] + [str(i) for i in range(14, 34)]
            path.write_text("\t".join(fields) + "\n", encoding="utf-8")
            result = pl9_api.parse_birth_data_export(path)
            row = result["rows"][0]
            self.assertEqual(result["delimiter"], "TAB")
            self.assertEqual(row["field_count"], 33)
            self.assertEqual(row["planet_longitudes"]["Sun"], "14")
            self.assertEqual(row["planet_longitudes"]["Ketu"], "22")
            self.assertEqual(len(row["auxiliary_points_raw_fields_23_to_29"]), 7)
            self.assertEqual(row["outer_planet_longitudes"]["Pluto"], "32")
            self.assertEqual(row["ascendant_longitude"], "33")


    def test_exact_pl9_export_row_match_is_required_for_calculation_verification(self):
        fields = [
            "Example Native", "0", "1", "Test City", "Test Region", "Test Country",
            "01:01:2000", "12:00:00", "2451545.0", "0", "0", "0", "0",
        ] + [str(i) for i in range(14, 34)]
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "birthdat.txt"
            path.write_text("\t".join(fields) + "\n", encoding="utf-8")
            exported = pl9_api.parse_birth_data_export(path)
            matched = pl9_api.match_birth_data_export_row(exported, self.birth)
            self.assertIsNotNone(matched)
            self.assertTrue(matched["matched_input"])
            self.assertTrue(all(matched["verification"].values()))
            self.assertEqual(matched["ascendant_longitude"], "33")
            self.assertEqual(len(matched["auxiliary_points_raw_fields_23_to_29"]), 7)
            self.assertEqual(matched["outer_planet_longitudes"]["Uranus"], "30")
            self.assertEqual(matched["outer_planet_longitudes"]["Pluto"], "32")
            self.assertIsNone(pl9_api.match_birth_data_export_row(
                exported, dict(self.birth, longitude_east_deg=1)
            ))

    def test_calculate_endpoint_records_only_controller_verified_result(self):
        class FakeController:
            def health(self):
                return {"service": "test", "calculation_verified": False}
            def calculate_chart(self, chart_path, display_name, birth):
                return {"status": "CALCULATION_VERIFIED", "calculation_verified": True,
                        "test_fake": True, "display_name": display_name}
            def open_chart(self, chart_path, display_name):
                return {"status": "CHART_OPEN_CONFIRMED", "calculation_verified": False}
            def trigger_birth_export(self, expected_birth=None):
                return {"status": "EXPORT_FILE_CHANGED", "calculation_verified": False}
            def select_menu(self, menu_path):
                return {"status": "MENU_SELECTION_SENT", "menu_path": menu_path,
                        "calculation_verified": False}
        with tempfile.TemporaryDirectory() as td:
            root_dir = Path(td)
            config = pl9_api.Config(
                exe=None, charts_dir=root_dir / "charts", export_dir=root_dir / "export",
                host="127.0.0.1", port=0, api_key="test-key",
            )
            server = pl9_api.make_server(config)
            server.RequestHandlerClass.controller = FakeController()
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                url = f"http://127.0.0.1:{server.server_address[1]}/horoscope"
                payload = json.dumps({"birth": self.birth, "open": True, "calculate": True}).encode()
                request = urllib.request.Request(
                    url, data=payload, method="POST",
                    headers={"Content-Type": "application/json", "X-API-Key": "test-key"},
                )
                with urllib.request.urlopen(request, timeout=3) as response:
                    result = json.loads(response.read())
                    self.assertEqual(response.status, 200)
                self.assertTrue(result["calculation_verified"])
                chart_id = result["chart_id"]
                metadata = config.export_dir / ".pl9_api" / f"{chart_id}.json"
                self.assertTrue(metadata.is_file())
                saved = json.loads(metadata.read_text(encoding="utf-8"))
                self.assertTrue(saved["calculation_verified"])
                get_request = urllib.request.Request(
                    f"http://127.0.0.1:{server.server_address[1]}/charts/{chart_id}",
                    headers={"X-API-Key": "test-key"},
                )
                with urllib.request.urlopen(get_request, timeout=3) as response:
                    status = json.loads(response.read())
                self.assertTrue(status["calculation_verified"])
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=2)


if __name__ == "__main__":
    unittest.main()
