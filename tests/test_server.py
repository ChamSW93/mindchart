"""The local server: who it accepts requests from, locking, and the main API routes."""
import http.client
import json
import os
import threading
import unittest

import support
import server

PORT = int(os.environ["MINDCHART_PORT"])


class Server(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.httpd = server.http.server.ThreadingHTTPServer(("127.0.0.1", PORT), server.Handler)
        threading.Thread(target=cls.httpd.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.httpd.server_close()

    def request(self, method, path, body=None, headers=None):
        connection = http.client.HTTPConnection("127.0.0.1", PORT, timeout=30)
        all_headers = {"Host": f"localhost:{PORT}", "X-MindChart": "1", "Content-Type": "application/json"}
        all_headers.update(headers or {})
        connection.request(method, path, body=json.dumps(body) if isinstance(body, dict) else body, headers=all_headers)
        response = connection.getresponse()
        data = response.read()
        connection.close()
        return response.status, (json.loads(data) if response.getheader("Content-Type", "").startswith("application/json") else data)

    def test_requests_from_elsewhere_are_refused(self):
        self.assertEqual(self.request("GET", "/api/session", headers={"X-MindChart": ""})[0], 403)  # no MindChart header
        self.assertEqual(self.request("GET", "/api/session", headers={"Host": "evil.example:80"})[0], 403)  # DNS rebinding
        self.assertEqual(self.request("POST", "/api/session/lock", {}, headers={"Origin": "https://evil.example"})[0], 403)
        self.assertEqual(self.request("GET", "/api/session")[0], 200)

    def test_data_needs_unlocking(self):
        support.unlocked_vault().lock()
        status, body = self.request("GET", "/api/records/patients")
        self.assertEqual((status, body.get("locked")), (423, True))
        self.assertEqual(self.request("POST", "/api/session/unlock", {"password": support.PASSWORD})[0], 200)
        self.assertEqual(self.request("GET", "/api/records/patients")[0], 200)

    def test_record_routes(self):
        support.unlocked_vault()
        status, created = self.request("POST", "/api/records/patients", {"name": "Server Test (fictional)"})
        self.assertEqual(status, 200)
        status, record = self.request("POST", f"/api/records/patients/{created['id']}", {"placement": "Cottage 9"})
        self.assertEqual(record["record"]["placement"], "Cottage 9")
        self.assertEqual(self.request("GET", "/api/records/nonsense")[0], 400)
        self.assertEqual(self.request("GET", "/api/no/such/route")[0], 400)
        settings = self.request("GET", "/api/settings")[1]
        self.assertTrue(settings["audit"]["ok"])
        self.assertEqual(self.request("POST", f"/api/records/patients/{created['id']}/delete", {})[0], 200)

    def test_huge_uploads_are_refused(self):
        support.unlocked_vault()
        connection = http.client.HTTPConnection("127.0.0.1", PORT, timeout=30)
        connection.putrequest("POST", "/api/patients/PT-XXXXXX/documents?name=big.pdf")
        connection.putheader("Host", f"localhost:{PORT}")
        connection.putheader("X-MindChart", "1")
        connection.putheader("Content-Length", str(server.MAX_UPLOAD_BYTES + 1))
        connection.endheaders()
        self.assertEqual(connection.getresponse().status, 413)
        connection.close()

    def test_pages_are_served(self):
        status, page = self.request("GET", "/")
        self.assertEqual(status, 200)
        self.assertIn(b"<title>MindChart</title>", page)


if __name__ == "__main__":
    unittest.main()
