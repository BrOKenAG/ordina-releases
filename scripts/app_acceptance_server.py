#!/usr/bin/env python3
"""Loopback server for the isolated real-App acceptance environment.

It serves the developer-tool channel statically and derives the plugin channel
dynamically from what the isolated publishing backend has actually written:
no production URL is ever contacted. Signed registry / plugin manifests are
re-signed with the throwaway lab key after their URLs are rewritten to this
loopback server, exactly like a Pages mirror would be in a test lab.

    python3 scripts/app_acceptance_server.py --root <acceptance-dir> --port 18765
"""
import argparse
import base64
import json
import re
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

sys.path.insert(0, str(Path(__file__).resolve().parent))
import lab_signing                                            # noqa: E402


def canonical(document):
    return json.dumps(document, ensure_ascii=False, sort_keys=True,
                      separators=(',', ':')).encode('utf-8')


def envelope(document, seed):
    data = canonical(document)
    return (json.dumps({
        'payload': base64.b64encode(data).decode('ascii'),
        'signature': base64.b64encode(lab_signing.signature(seed, data)).decode('ascii'),
    }, separators=(',', ':')) + '\n').encode('utf-8')


def decode(raw):
    obj = json.loads(raw)
    return json.loads(base64.b64decode(obj['payload'], validate=True))


class AcceptanceServer:
    def __init__(self, root, port):
        self.root = Path(root)
        self.port = port
        self.seed = lab_signing.read_seed(str(self.root / 'credentials' / 'lab-signing.key'))
        self.routes = json.loads((self.root / 'registry' / 'routes.json').read_text())
        lab = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                path = urlparse(self.path).path
                try:
                    body = lab.resolve(path)
                except (OSError, ValueError, KeyError):
                    body = None
                if body is None:
                    body, status = b'not found', 404
                else:
                    status = 200
                self.send_response(status)
                self.send_header('Content-Length', str(len(body)))
                self.send_header('Cache-Control', 'no-store')
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *args):
                pass

        self.httpd = ThreadingHTTPServer(('127.0.0.1', port), Handler)

    def lab_blob(self, repo, name):
        directory = self.root / 'labroot' / 'lab' / 'repos' / repo
        source = directory / name
        if not source.is_file():
            source = directory / 'state.json'
        if not source.is_file():
            return None
        store = json.loads(source.read_text(encoding='utf-8'))
        value = store.get(name)
        return base64.b64decode(value) if value else None

    def static(self, path):
        spec = self.routes.get(path)
        if not spec:
            return None
        if 'file' in spec:
            return (self.root / spec['file']).read_bytes()
        return base64.b64decode(spec['base64'])

    def resolve(self, path):
        # Tool channel: signed manifest and both Release assets.
        direct = self.static(path)
        if direct is not None:
            return direct

        if path == '/registry.json':
            raw = self.lab_blob('BrOKenAG/ordina-releases', 'registry.json')
            if raw is None:
                raw = self.static('/registry.json.initial')
            if raw is None:
                return None
            document = decode(raw)
            for plugin_id, item in document.get('plugins', {}).items():
                item['feed'] = (f'http://127.0.0.1:{self.port}/'
                                f'ordina-plugin-{plugin_id}/manifest-v2.json')
                item['downloadPrefix'] = ('/' + item['repository'] +
                                          '/releases/download/')
            return envelope(document, self.seed)

        match = re.fullmatch(r'/ordina-plugin-([A-Za-z0-9._-]+)/manifest-v2.json', path)
        if match:
            plugin_id = match.group(1)
            repo = 'BrOKenAG/ordina-plugin-' + plugin_id
            raw = self.lab_blob(repo, 'manifest-v2.json')
            if raw is None:
                return None
            document = decode(raw)
            for entry in document.get('plugins', {}).values():
                url = entry.get('url', '')
                asset = re.search(r'/releases/download/([^/]+)/([^/]+)$', url)
                if asset:
                    entry['url'] = (f'http://127.0.0.1:{self.port}/{repo}/'
                                    f'releases/download/{asset.group(1)}/{asset.group(2)}')
            return envelope(document, self.seed)

        match = re.fullmatch(r'/(BrOKenAG/[^/]+)/releases/download/([^/]+)/([^/]+)', path)
        if match:
            target = (self.root / 'labroot' / 'lab' / 'assets' / match.group(1)
                      / match.group(2) / match.group(3))
            return target.read_bytes() if target.is_file() else None
        return None

    def serve(self):
        print(f'隔离 Registry：http://127.0.0.1:{self.port}/  （Ctrl-C 停止）',
              flush=True)
        try:
            self.httpd.serve_forever()
        except KeyboardInterrupt:
            pass
        finally:
            self.httpd.server_close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, required=True)
    parser.add_argument('--port', type=int, default=18765)
    args = parser.parse_args()
    if not (args.root / 'registry' / 'routes.json').is_file():
        sys.exit('请先运行 prepare_app_acceptance.py 生成验收环境。')
    AcceptanceServer(args.root, args.port).serve()


if __name__ == '__main__':
    main()
