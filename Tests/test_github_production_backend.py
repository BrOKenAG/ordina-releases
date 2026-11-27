import sys,unittest
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'scripts'))
from github_production_backend import ProductionBackend

class ProductionBackendTests(unittest.TestCase):
 def test_public_download_resumes_after_timeout(self):
  import subprocess
  from unittest.mock import patch
  calls=[]
  def download(args, **kwargs):
   calls.append(args)
   target=Path(args[args.index('-o')+1])
   if len(calls)==1:
    target.write_bytes(b'part')
    raise subprocess.CalledProcessError(28,args)
   self.assertIn('--continue-at',args)
   with target.open('ab') as stream: stream.write(b'ial')
  with patch('github_production_backend.subprocess.run',side_effect=download), patch('github_production_backend.time.sleep'):
   self.assertEqual(ProductionBackend._fetch_public('https://example.test/asset'),b'partial')
  self.assertEqual(len(calls),2)

 def test_public_download_stops_after_bounded_failures(self):
  import subprocess
  from unittest.mock import patch
  with patch('github_production_backend.subprocess.run',side_effect=subprocess.CalledProcessError(28,'curl')) as run, patch('github_production_backend.time.sleep'):
   with self.assertRaises(subprocess.CalledProcessError): ProductionBackend._fetch_public('https://example.test/asset')
   self.assertEqual(run.call_count,3)

 def test_plugin_backend_rejects_lab_mode_before_lock_write(self):
  import os
  from unittest.mock import patch
  from github_lab_backend import GitHubLabMultiBackend
  main='BrOKenAG/ordina-releases'; repo='BrOKenAG/ordina-plugin-demo'
  backend=ProductionBackend(main,repo,'v1','a.json',{main:['registry.json']},{main,repo})
  with patch.dict(os.environ,{'ORDINA_DEV_LAB':'1'}), patch.object(GitHubLabMultiBackend,'acquire') as acquire:
   with self.assertRaises(Exception): backend.acquire('token')
   acquire.assert_not_called()

 def test_direct_release_creation_rejects_lab_before_gh(self):
  import os
  from unittest.mock import patch
  from release_transaction import LocalGateway
  with patch.dict(os.environ,{'ORDINA_DEV_LAB':'1'}), patch('release_transaction.subprocess.run') as run:
   with self.assertRaises(Exception):
    LocalGateway().create_release('BrOKenAG/ordina-plugin-demo','v1',Path('/tmp/a'),'title','notes')
   run.assert_not_called()
 def test_rollback_snapshot_is_verified_against_git_commit(self):
  from unittest.mock import patch
  import base64
  repo='BrOKenAG/ordina-releases'
  backend=ProductionBackend(repo,repo,'v1','a.json',{repo:['registry.json']},{repo})
  head='a'*40
  original=backend.encode_files({'registry.json':b'original'})
  with patch('github_lab_backend.api',return_value={'content':base64.b64encode(b'original').decode()}):
   backend.verify_rollback(repo,head,original)
   with self.assertRaises(ValueError):
    backend.verify_rollback(repo,head,backend.encode_files({'registry.json':b'forged'}))
 def test_only_explicit_ordina_repositories_allowed(self):
  valid={'BrOKenAG/ordina-releases','BrOKenAG/ordina-plugin-minutes'}
  self.assertTrue(ProductionBackend.valid_allowlist(valid))
  for repo in ('Other/ordina-plugin-minutes','BrOKenAG/private-data','BrOKenAG/ordina-plugin-../evil'):
   self.assertFalse(ProductionBackend.valid_allowlist(valid | {repo}))
 def test_asset_url_must_match_authorized_github_release(self):
  allowed={'BrOKenAG/ordina-releases','BrOKenAG/ordina-plugin-minutes'}
  self.assertTrue(ProductionBackend.valid_asset_url('https://github.com/BrOKenAG/ordina-plugin-minutes/releases/download/v1/a.json',allowed))
  for url in ('https://example.com/a','http://github.com/BrOKenAG/ordina-releases/releases/download/x/a','https://github.com/BrOKenAG/private-data/releases/download/v1/a','https://github.com/BrOKenAG/ordina-releases/releases/download/v1/a?token=secret'):
   self.assertFalse(ProductionBackend.valid_asset_url(url,allowed))
  self.assertFalse(ProductionBackend.valid_asset_url('https://github.com/BrOKenAG/ordina-plugin-minutes/releases/download/v1/%252e%252e',allowed))
 def test_referenced_assets_checked_before_publication(self):
  from unittest.mock import patch
  import base64,hashlib,json
  allowed={'BrOKenAG/ordina-releases','BrOKenAG/ordina-plugin-minutes'}
  backend=ProductionBackend('BrOKenAG/ordina-releases','BrOKenAG/ordina-plugin-minutes','v1','a.json',{'BrOKenAG/ordina-releases':['plugins/manifest.json']},allowed)
  url='https://github.com/BrOKenAG/ordina-plugin-minutes/releases/download/v1/a.json'
  signed=b'signed-feed'
  snapshot=json.dumps({'plugins/manifest.json':base64.b64encode(signed).decode()})
  document={'format':1,'plugins':{'minutes':{'url':url,'sha256':hashlib.sha256(b'abc').hexdigest()}}}
  plan={'asset':{'size':3,'sha256':hashlib.sha256(b'abc').hexdigest()},'feeds':{'BrOKenAG/ordina-releases':snapshot}}
  with patch('github_lab_backend.GitHubLabBackend.verify_assets'), patch('feed_signing.verify_envelope',return_value=document), patch.object(backend,'validate_transitions'), patch.object(backend,'_fetch_public',return_value=b'bad'):
   with self.assertRaises(RuntimeError):backend.verify_assets(plan)

 def test_unexpected_registry_change_fails_closed(self):
  from unittest.mock import patch
  import base64,json
  allowed={'BrOKenAG/ordina-releases'}
  repo='BrOKenAG/ordina-releases'
  backend=ProductionBackend(repo,repo,'v1','a.json',{repo:['registry.json']},allowed)
  old=json.dumps({'registry.json':base64.b64encode(b'old').decode()}).encode()
  new=json.dumps({'registry.json':base64.b64encode(b'new').decode()})
  with patch.object(backend,'read',return_value=('head',old)), patch('feed_signing.verify_envelope',side_effect=[{'format':2,'plugins':{}},{'format':2,'plugins':{'extra':{}}}]):
   with self.assertRaises(ValueError):backend.validate_transitions({'feeds':{repo:new}})

 def test_plugin_update_registry_is_bound_to_signed_manifest_and_asset(self):
  import base64,copy,json
  from unittest.mock import patch
  import build_feeds
  main='BrOKenAG/ordina-releases'; plugin='BrOKenAG/ordina-plugin-minutes'
  source=json.loads((ROOT/'plugins/registry.source.json').read_text())
  registry,_,manifests=build_feeds.documents(source['plugins'])
  old=copy.deepcopy(registry)
  old['plugins']['minutes']['version']='0.2.0'
  entry=manifests['minutes']['plugins']['minutes']
  plan={'asset':{'url':entry['url'],'sha256':entry['sha256'],'size':entry['size']},
        'feeds':{plugin:json.dumps({'manifest-v2.json':base64.b64encode(json.dumps(manifests['minutes']).encode()).decode()})}}
  desired={'plugins/registry.source.json':base64.b64encode(json.dumps(source).encode()).decode()}
  backend=ProductionBackend(main,plugin,'v1','minutes.json',{main:['registry.json']},{main,plugin})
  with patch('feed_signing.verify_envelope',side_effect=lambda raw:json.loads(raw)):
   backend._validate_registry_transition(old,registry,desired,plan)
   bad=copy.deepcopy(registry);bad['plugins']['multicam']['repository']='BrOKenAG/ordina-plugin-evil'
   with self.assertRaises(ValueError):backend._validate_registry_transition(old,bad,desired,plan)
   bad=copy.deepcopy(plan);bad['asset']['sha256']='0'*64
   with self.assertRaises(ValueError):backend._validate_registry_transition(old,registry,desired,bad)
   bad=copy.deepcopy(old);bad['plugins']['minutes']['version']='99.0.0'
   with self.assertRaises(ValueError):backend._validate_registry_transition(bad,registry,desired,plan)
   bad=copy.deepcopy(plan);bad['feeds'][plugin]=json.dumps({'manifest-v2.json':base64.b64encode(b'{}').decode()})
   with self.assertRaises(ValueError):backend._validate_registry_transition(old,registry,desired,bad)
  with patch('feed_signing.verify_envelope',side_effect=ValueError('invalid signature')):
   with self.assertRaisesRegex(ValueError,'invalid signature'):
    backend._validate_registry_transition(old,registry,desired,plan)

 def test_pages_url_must_remain_on_expected_projects(self):
  allowed={'BrOKenAG/ordina-releases','BrOKenAG/ordina-plugin-minutes'}
  self.assertTrue(ProductionBackend.valid_pages_url('https://brokenag.github.io/ordina-plugin-minutes/manifest-v2.json',allowed))
  for url in ('https://evil.example/ordina-releases/x','https://brokenag.github.io/not-ordina/x','https://brokenag.github.io/ordina-releases/x?q=1'):
   self.assertFalse(ProductionBackend.valid_pages_url(url,allowed))
