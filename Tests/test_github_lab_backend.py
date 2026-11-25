import hashlib
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'scripts'))
from github_lab_backend import GitHubLabBackend

class LabBackendTests(unittest.TestCase):
 def setUp(self):
  self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
  self.source=Path(self.temp.name)/'plugin.txt';self.source.write_bytes(b'published asset')
  self.backend=GitHubLabBackend('BrOKenAG/ordina-release-lab-20261124-main','BrOKenAG/ordina-release-lab-20261124-plugin','lab-tag','plugin.txt')
  self.asset={'size':self.source.stat().st_size,'sha256':hashlib.sha256(self.source.read_bytes()).hexdigest(),'source':str(self.source)}
 def test_formal_repositories_are_forbidden(self):
  with self.assertRaises(ValueError):
   GitHubLabBackend('BrOKenAG/ordina-releases','BrOKenAG/ordina-plugin-minutes','v1','file.py')
 def test_write_rejects_lost_remote_lock(self):
  with patch.object(self.backend,'_ref',return_value='lock-head'), patch.object(self.backend,'_commit',return_value={'message':'ordina-lock:other'}):
   with self.assertRaisesRegex(RuntimeError, "lock"):
    self.backend.write('BrOKenAG/ordina-release-lab-20261124-main','old',b'new','our-token')
 def test_new_release_asset_is_created_then_verified(self):
  with patch.object(self.backend,'_release_assets',side_effect=[None,{'plugin.txt'}]), patch.object(self.backend,'_create_release') as create, patch.object(self.backend,'_download_asset',return_value=b'published asset'):
   self.backend.verify_assets({'asset':self.asset})
   create.assert_called_once()
 def test_existing_different_asset_never_overwritten(self):
  with patch.object(self.backend,'_release_assets',return_value={'plugin.txt'}), patch.object(self.backend,'_create_release') as create, patch.object(self.backend,'_download_asset',return_value=b'wrong'):
   with self.assertRaises(RuntimeError):self.backend.verify_assets({'asset':self.asset})
   create.assert_not_called()
 def test_missing_asset_in_existing_release_is_uploaded_and_verified(self):
  with patch.object(self.backend,'_release_assets',return_value=set()), patch.object(self.backend,'_upload_asset') as upload, patch.object(self.backend,'_download_asset',return_value=b'published asset'):
   self.backend.verify_assets({'asset':self.asset})
   upload.assert_called_once()

class MultiFileTests(unittest.TestCase):
 def test_round_trip_snapshot_preserves_missing_and_bytes(self):
  import json,base64
  from github_lab_backend import GitHubLabMultiBackend as Multi
  snapshot=json.loads(Multi.encode_files({'manifest.json':b'abc','dir/appcast.xml':None}))
  self.assertEqual(base64.b64decode(snapshot['manifest.json']),b'abc')
  self.assertIsNone(snapshot['dir/appcast.xml'])
 def test_path_validation_rejects_escape(self):
  from github_lab_backend import GitHubLabMultiBackend as Multi
  for value in ('../appcast.xml','/manifest.json','plugins/../../secret','plugins/.hidden','plugins/a\\b'):
   with self.subTest(value=value):self.assertFalse(Multi._safe_path(value))
  self.assertTrue(Multi._safe_path('plugins/manifest-v2.json'))
  self.assertTrue(Multi._safe_path('Integrations/OptionalPlugins/Multicam Tools.py'))
  self.assertTrue(Multi._safe_path('素材/插件.py'))
