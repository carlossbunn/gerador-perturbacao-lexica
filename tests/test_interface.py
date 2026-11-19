import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import unittest
import urllib.error
import urllib.request

ROOT = Path(__file__).resolve().parents[1]


class InterfaceTests(unittest.TestCase):
    def test_local_interface_validation_preview_and_same_folder_export(self):
        env = {**os.environ, "BROWSER": "true"}
        process = subprocess.Popen([sys.executable, "-u", str(ROOT / "interface.py")],
                                   stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=env)
        try:
            url = process.stdout.readline().strip().removeprefix("Interface: ")
            self.assertTrue(url.startswith("http://127.0.0.1:"), url)
            html = urllib.request.urlopen(url, timeout=5).read().decode()
            token = re.search(r"const token='([^']+)'", html).group(1)

            def post(action, body, authentication=token):
                request = urllib.request.Request(url + "api/" + action,
                    data=json.dumps(body).encode(), headers={"X-Local-Token": authentication,
                                                           "Content-Type": "application/json"})
                return json.load(urllib.request.urlopen(request, timeout=10))

            with self.assertRaises(urllib.error.HTTPError) as denied:
                post("browse", {}, "invalid")
            self.assertEqual(denied.exception.code, 403)
            with tempfile.TemporaryDirectory() as tmp:
                source = Path(tmp) / "casos.json"
                original = (ROOT / "exemplos/modelo_entrada.json").read_text(encoding="utf-8")
                source.write_text(original, encoding="utf-8")
                browser = post("browse", {"folder": tmp})
                self.assertEqual(browser["entries"][0]["path"], str(source))
                cfg = {"file": str(source), "kinds": ["delete", "homoglifo"], "scopes": ["1", "2", "todos"],
                       "mode": "tcc", "seed": 42, "rate": 0.08, "strict": False}
                self.assertEqual(post("validate", cfg)["count"], 6)
                preview = post("preview", cfg)
                self.assertEqual(preview["metadata"]["benchmark_item_count"], 42)
                self.assertEqual(list(Path(tmp).iterdir()), [source])
                generated = post("generate", cfg)
                path = Path(generated["saved_to"])
                self.assertEqual(path.parent, source.parent)
                self.assertEqual(json.loads(path.read_text())["metadata"], preview["metadata"])
                self.assertEqual(source.read_text(), original)
                with self.assertRaises(urllib.error.HTTPError) as invalid:
                    post("generate", {**cfg, "kinds": []})
                self.assertEqual(invalid.exception.code, 400)
            post("stop", {})
            process.wait(timeout=5)
            self.assertEqual(process.returncode, 0)
        finally:
            if process.poll() is None:
                process.terminate()
                process.wait(timeout=5)
            process.stdout.close()
            process.stderr.close()


if __name__ == "__main__":
    unittest.main()
