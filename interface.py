"""Interface gráfica local no navegador, usando apenas a biblioteca padrão."""
import hmac
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import secrets
import threading
from urllib.parse import urlparse
import webbrowser

from gerador import WORDS, ValidationError, generate, normalize, read_json, save_output

ROOT = Path(__file__).resolve().parent


def run():
    token = secrets.token_urlsafe(32)
    page = (ROOT / "interface.html").read_text(encoding="utf-8").replace("__TOKEN__", token)

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            pass

        def reply(self, status, data, html=False):
            body = data.encode() if html else json.dumps(data, ensure_ascii=False).encode()
            self.send_response(status)
            self.send_header("Content-Type", "text/html; charset=utf-8" if html else "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Content-Security-Policy", "default-src 'self'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; connect-src 'self'; frame-ancestors 'none'")
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            if self.headers.get("Host") != f"127.0.0.1:{self.server.server_port}":
                self.reply(403, {"error": "Host inválido"})
            elif urlparse(self.path).path == "/":
                self.reply(200, page, html=True)
            else:
                self.reply(404, {"error": "Não encontrado"})

        def do_POST(self):
            if not hmac.compare_digest(self.headers.get("X-Local-Token", ""), token):
                self.reply(403, {"error": "Sessão inválida"})
                return
            try:
                length = int(self.headers.get("Content-Length", 0))
                if not 0 < length <= 65536:
                    raise ValidationError("Requisição inválida.")
                args = json.loads(self.rfile.read(length))
                action = urlparse(self.path).path
                if action == "/api/browse":
                    folder = Path(args.get("folder") or ROOT / "exemplos").expanduser().resolve()
                    entries = sorted(folder.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower()))
                    self.reply(200, {"folder": str(folder), "parent": str(folder.parent),
                        "entries": [{"name": p.name, "path": str(p), "directory": p.is_dir(),
                                     "generated": "_perturbado" in p.stem}
                                    for p in entries if not p.name.startswith(".") and (p.is_dir() or p.suffix.lower() == ".json")]})
                elif action in ("/api/validate", "/api/preview", "/api/generate"):
                    source = Path(args["file"]).expanduser().resolve()
                    payload = read_json(source)
                    if action == "/api/validate":
                        items, warnings = normalize(payload, args.get("strict", False))
                        self.reply(200, {"count": len(items), "warnings": warnings,
                            "first_target_words": [{"index": i, "word": m.group()} for i, m in enumerate(WORDS.finditer(items[0]['target_text']))]})
                    else:
                        result = generate(payload, args["kinds"], args["scopes"], args["mode"],
                                          int(args["seed"]), float(args["rate"]), args.get("strict", False))
                        examples = []
                        for key, entry in result["datasets"].items():
                            examples.extend({"dataset": key, "id": i["id"], "prompt": i["prompt"],
                                "original": i["target_text"], "perturbed": i["perturbed_source"],
                                "changed": i["metadata"].get("changed", False)} for i in entry["data"][:3])
                        response = {"metadata": result["metadata"], "examples": examples}
                        if action == "/api/generate":
                            response["saved_to"] = str(save_output(source, result))
                        self.reply(200, response)
                elif action == "/api/stop":
                    self.reply(200, {"stopped": True})
                    threading.Thread(target=self.server.shutdown, daemon=True).start()
                else:
                    self.reply(404, {"error": "Ação desconhecida"})
            except (ValidationError, OSError, ValueError, KeyError, TypeError) as exc:
                self.reply(400, {"error": str(exc)})

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    url = f"http://127.0.0.1:{server.server_port}/"
    print(f"Interface: {url}\nMantenha este terminal aberto. Ctrl+C ou Encerrar na janela para sair.", flush=True)
    threading.Timer(0.5, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever()
    finally:
        server.server_close()


if __name__ == "__main__":
    run()
