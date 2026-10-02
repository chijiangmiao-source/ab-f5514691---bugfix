"""HTTP-level tests for the review service."""

import json
import threading
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer

from app.server import Handler


class ServerTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        cls.server.daemon_threads = True
        cls.port = cls.server.server_address[1]
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()

    def url(self, path):
        return f"http://127.0.0.1:{self.port}{path}"

    def get(self, path):
        with urllib.request.urlopen(self.url(path), timeout=10) as response:
            return response.status, response.read().decode("utf-8")

    def post_review(self, payload):
        request = urllib.request.Request(
            self.url("/api/review"),
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=10) as response:
                return response.status, json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            return exc.code, json.loads(exc.read().decode("utf-8"))

    def test_health(self):
        status, text = self.get("/health")
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(text), {"status": "ok"})

    def test_index_page_served(self):
        status, text = self.get("/")
        self.assertEqual(status, 200)
        self.assertIn("复核", text)

    def test_static_assets_served(self):
        for path in ("/app.js", "/style.css"):
            status, _ = self.get(path)
            self.assertEqual(status, 200, path)

    def test_path_traversal_blocked(self):
        request = urllib.request.Request(self.url("/../app/server.py"))
        try:
            with urllib.request.urlopen(request, timeout=10) as response:
                status = response.status
        except urllib.error.HTTPError as exc:
            status = exc.code
        self.assertIn(status, (400, 404))

    def test_review_solvable_exact_bigint(self):
        status, body = self.post_review(
            {
                "variables": ["K1", "K2", "K3"],
                "matrix": [
                    ["9007199254740993", "1", "0"],
                    ["1", "3", "1"],
                    ["0", "2", "4"],
                ],
                "target": ["18014398509481989", "12", "10"],
            }
        )
        self.assertEqual(status, 200)
        self.assertTrue(body["solvable"])
        self.assertEqual(body["solution"], ["2", "3", "1"])
        first = body["constraints"][0]
        self.assertEqual(first["terms"][0]["product"], str(9007199254740993 * 2))
        self.assertEqual(first["sum"], "18014398509481989")
        self.assertTrue(all(c["satisfied"] for c in body["constraints"]))

    def test_review_solvable_oversized_coefficient_correction_two(self):
        # Coupled displacement constraints: the oversized coefficient sits on
        # K1, whose correction is exactly 2.  Every value in the per-constraint
        # audit must be exact integer text that recomputes to the target.
        big = 2**64 + 3  # 18446744073709551619, far beyond the safe range
        status, body = self.post_review(
            {
                "variables": ["K1", "K2", "K3"],
                "matrix": [
                    [str(big), "1", "0"],
                    ["1", "3", "1"],
                    ["0", "2", "4"],
                ],
                "target": [str(2 * big + 3), "12", "10"],
            }
        )
        self.assertEqual(status, 200)
        self.assertTrue(body["solvable"])
        self.assertEqual(body["solution"], ["2", "3", "1"])
        self.assertEqual(len(body["constraints"]), 3)
        for i, constraint in enumerate(body["constraints"]):
            self.assertEqual(constraint["index"], i)
            recomputed_sum = 0
            for term in constraint["terms"]:
                # Every audit field is exact decimal integer text ...
                for key in ("coefficient", "correction", "product"):
                    self.assertIsInstance(term[key], str)
                    self.assertRegex(term[key], r"^-?\d+$")
                product = int(term["coefficient"]) * int(term["correction"])
                # ... the stated product equals coefficient × correction ...
                self.assertEqual(int(term["product"]), product)
                recomputed_sum += product
            self.assertIsInstance(constraint["sum"], str)
            self.assertIsInstance(constraint["target"], str)
            # ... and the term-by-term sum equals the stated sum and target.
            self.assertEqual(str(recomputed_sum), constraint["sum"])
            self.assertEqual(constraint["sum"], constraint["target"])
            self.assertTrue(constraint["satisfied"])
        first = body["constraints"][0]
        self.assertEqual(first["terms"][0]["coefficient"], str(big))
        self.assertEqual(first["terms"][0]["correction"], "2")
        self.assertEqual(first["terms"][0]["product"], str(2 * big))
        self.assertEqual(first["sum"], str(2 * big + 3))

    def test_review_underdetermined_free_direction(self):
        status, body = self.post_review(
            {"variables": ["x", "y"], "matrix": [["2", "3"]], "target": ["1"]}
        )
        self.assertEqual(status, 200)
        self.assertTrue(body["solvable"])
        x, y = (int(v) for v in body["solution"])
        self.assertEqual(2 * x + 3 * y, 1)
        # One free correction direction: 2a + 3b = 0 for the reported vector.
        self.assertEqual(len(body["homogeneousBasis"]), 1)
        a, b = (int(v) for v in body["homogeneousBasis"][0])
        self.assertEqual(2 * a + 3 * b, 0)
        self.assertTrue(all(c["satisfied"] for c in body["constraints"]))

    def test_review_zero_row_obstruction(self):
        status, body = self.post_review(
            {
                "variables": ["x", "y"],
                "matrix": [["1", "1"], ["2", "2"]],
                "target": ["1", "3"],
            }
        )
        self.assertEqual(status, 200)
        self.assertFalse(body["solvable"])
        obstruction = body["obstruction"]
        self.assertEqual(obstruction["type"], "zero_row")
        self.assertNotEqual(int(obstruction["transformedTarget"]), 0)
        total = sum(int(term["product"]) for term in obstruction["uRowTerms"])
        self.assertEqual(str(total), obstruction["transformedTarget"])

    def test_review_ordinary_solvable_small_integers(self):
        status, body = self.post_review(
            {
                "variables": ["a", "b"],
                "matrix": [["2", "1"], ["1", "-1"]],
                "target": ["7", "2"],
            }
        )
        self.assertEqual(status, 200)
        self.assertTrue(body["solvable"])
        self.assertEqual(body["solution"], ["3", "1"])
        self.assertTrue(all(c["satisfied"] for c in body["constraints"]))
        for constraint in body["constraints"]:
            total = sum(
                int(t["coefficient"]) * int(t["correction"])
                for t in constraint["terms"]
            )
            self.assertEqual(str(total), constraint["sum"])
            self.assertEqual(constraint["sum"], constraint["target"])

    def test_review_unsolvable_obstruction(self):
        status, body = self.post_review(
            {
                "variables": ["D1", "D2"],
                "matrix": [["2", "0"], ["0", "4"]],
                "target": ["9007199254740993", "8"],
            }
        )
        self.assertEqual(status, 200)
        self.assertFalse(body["solvable"])
        obstruction = body["obstruction"]
        self.assertEqual(obstruction["type"], "non_divisible")
        self.assertEqual(obstruction["pivot"], "2")
        self.assertEqual(obstruction["transformedTarget"], "9007199254740993")
        self.assertEqual(obstruction["remainder"], "1")
        total = sum(int(term["product"]) for term in obstruction["uRowTerms"])
        self.assertEqual(str(total), obstruction["transformedTarget"])

    def test_json_number_integers_accepted(self):
        status, body = self.post_review(
            {"variables": ["x"], "matrix": [[4]], "target": [8]}
        )
        self.assertEqual(status, 200)
        self.assertTrue(body["solvable"])
        self.assertEqual(body["solution"], ["2"])

    def test_float_coefficient_rejected(self):
        status, body = self.post_review(
            {"variables": ["x"], "matrix": [[1.5]], "target": [1]}
        )
        self.assertEqual(status, 400)
        self.assertFalse(body["ok"])

    def test_dimension_mismatch_rejected(self):
        status, body = self.post_review(
            {
                "variables": ["x", "y"],
                "matrix": [["1", "2"]],
                "target": ["1", "2"],
            }
        )
        self.assertEqual(status, 400)
        self.assertFalse(body["ok"])

    def test_unknown_route_404(self):
        request = urllib.request.Request(self.url("/nope"))
        try:
            with urllib.request.urlopen(request, timeout=10) as response:
                status = response.status
        except urllib.error.HTTPError as exc:
            status = exc.code
        self.assertEqual(status, 404)


if __name__ == "__main__":
    unittest.main()
