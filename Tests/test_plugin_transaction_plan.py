import copy
import hashlib
import json
import sys
import unittest
from pathlib import Path
from unittest.mock import patch
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'scripts'))
import build_feeds
import plugin_transaction_plan as planner

class PluginPlanTests(unittest.TestCase):
 def setUp(self):
  self.source=json.loads((ROOT/'plugins/registry.source.json').read_text())
  self.data=b'PLUGIN_VERSION = "v2.2"\n'
  self.files={'Integrations/OptionalPlugins/Multicam Tools.py':self.data}
 def generate(self,plugins):
  registry,legacy,per=build_feeds.documents(plugins)
  def encode(doc):return json.dumps(doc,sort_keys=True).encode()
  out={'registry':(registry,encode(registry)),'legacy':(legacy,encode(legacy))}
  for name,doc in per.items():
   out['plugin:'+name]=(doc,encode(doc))
   if name in legacy['plugins']:
    old={'format':1,'plugins':{name:legacy['plugins'][name]}}
    out['compat-plugin:'+name]=(old,encode(old))
  return out
 def test_builds_all_repository_updates_from_one_source(self):
  with patch.object(planner,'generate',side_effect=self.generate):
   result=planner.build(self.source,'multicam','2.2',self.data,self.files)
  main=result['files']['BrOKenAG/ordina-releases']
  plugin=result['files']['BrOKenAG/ordina-plugin-multicam']
  self.assertIn('plugins/registry.source.json',main)
  self.assertIn('plugins/manifest.json',main)
  self.assertIn('registry.json',main)
  self.assertIn('Integrations/OptionalPlugins/Multicam Tools.py',main)
  self.assertEqual(set(plugin),{'manifest.json','manifest-v2.json'})
  new=json.loads(plugin['manifest-v2.json'])['plugins']['multicam']
  self.assertEqual(new['sha256'],hashlib.sha256(self.data).hexdigest())
  self.assertEqual(new['version'],'2.2')
  legacy=json.loads(plugin['manifest.json'])['plugins']['multicam']
  self.assertEqual(legacy['version'],'2.1')
 def test_rejects_downgrade(self):
  with self.assertRaises(ValueError):planner.build(self.source,'multicam','2.1',self.data,self.files)
 def test_rejects_source_path_escape(self):
  with self.assertRaises(ValueError):planner.build(self.source,'multicam','2.2',self.data,{'../../elsewhere':b'x'})
 def test_source_data_not_mutated(self):
  original=copy.deepcopy(self.source)
  with patch.object(planner,'generate',side_effect=self.generate):planner.build(self.source,'multicam','2.2',self.data,self.files)
  self.assertEqual(self.source,original)
