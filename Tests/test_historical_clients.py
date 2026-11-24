"""Exercise actual historical Swift validators against the compatibility feed."""
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
import build_feeds

class HistoricalClientTests(unittest.TestCase):
    def check_client(self, commit, names):
        source = subprocess.check_output(['git', 'show', commit + ':Ordina/PluginUpdates.swift'], cwd=ROOT)
        _, legacy, _ = build_feeds.documents(build_feeds.load_source())
        harness = r'''
import Foundation
import CryptoKit
enum OptionalPlugin: String { case minutes, multicam, yueliu; var id: String { rawValue } }
@main struct Check {
 static func main() throws {
  let key = Curve25519.Signing.PrivateKey()
  let payload = try Data(contentsOf: URL(fileURLWithPath: CommandLine.arguments[1]))
  let sig = try key.signature(for: payload)
  let envelope = try JSONSerialization.data(withJSONObject: ["payload": payload.base64EncodedString(), "signature": sig.base64EncodedString()])
  for plugin in PLUGINS {
   _ = try PluginUpdates.verifiedEntry(envelope, plugin: plugin, publicKey: key.publicKey.rawRepresentation)
  }
 }
}
'''
        with tempfile.TemporaryDirectory(prefix='ordina-history-') as temp:
            path = Path(temp)
            (path/'Old.swift').write_bytes(source)
            (path/'Check.swift').write_text(harness.replace('PLUGINS', '[' + ', '.join('OptionalPlugin.' + n for n in names) + ']'))
            (path/'payload.json').write_text(json.dumps(legacy))
            subprocess.run(['/usr/bin/xcrun','swiftc',str(path/'Old.swift'),str(path/'Check.swift'),'-o',str(path/'check')],check=True,cwd=ROOT)
            subprocess.run([str(path/'check'),str(path/'payload.json')],check=True,cwd=ROOT)

    def test_192(self):
        self.check_client('1cebcf0', ['multicam'])

    def test_194(self):
        self.check_client('39a8f00', ['minutes', 'multicam'])

    def test_195(self):
        self.check_client('679c940', ['minutes', 'multicam'])
