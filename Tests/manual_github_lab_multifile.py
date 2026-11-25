"""Mutates only the two private GitHub lab repos; run explicitly from repo root.

Uses synthetic files rather than a production appcast or a production key.
"""
import argparse,hashlib,json,sys,uuid
from pathlib import Path
sys.path.insert(0,str(Path.cwd()/'scripts'))
import durable_transaction as tx
from github_lab_backend import GitHubLabMultiBackend
parser=argparse.ArgumentParser(description=__doc__)
parser.add_argument('--execute',action='store_true')
parser.add_argument('--asset',type=Path,required=True)
args=parser.parse_args()
if not args.execute:raise SystemExit('Pass --execute to run against the private lab repositories.')
main='BrOKenAG/ordina-release-lab-20261124-main';plugin='BrOKenAG/ordina-release-lab-20261124-plugin'
asset=args.asset.resolve()
paths={main:['appcast.xml','registry.json','plugins/manifest.json'],plugin:['manifest.json','manifest-v2.json']}
class Interrupt(GitHubLabMultiBackend):
 once=True
 def write(self,*args):
  result=super().write(*args)
  if self.once:self.once=False;raise ConnectionError('lost response')
  return result
remote=Interrupt(main,plugin,'lab-plugin-v2',asset.name,paths)
old={repo:remote.read(repo)[1] for repo in paths}
plan={'id':uuid.uuid4().hex,'asset':{'size':asset.stat().st_size,'sha256':hashlib.sha256(asset.read_bytes()).hexdigest(),'source':str(asset)},'feeds':{repo:remote.encode_files({name:('synthetic '+name).encode() for name in names}).decode() for repo,names in paths.items()}}
path=asset.parent/('multifile-'+plan['id']+'.json')
try:tx.publish(plan,path,remote)
except ConnectionError:print('多文件事务：远端提交后断线已模拟',flush=True)
else:raise RuntimeError('expected interruption')
tx.publish(plan,path,remote)
for repo in paths:assert remote.read(repo)[1]==plan['feeds'][repo].encode()
print('多文件跨仓库断点续发通过',flush=True)
tx.rollback(path,remote)
for repo in paths:assert remote.read(repo)[1]==old[repo]
print('多文件安全回滚通过',flush=True)
