#!/usr/bin/env python3
"""Exercise exact candidate bytes with a throwaway key; not production verification."""
import argparse,base64,hashlib,os,sys,tempfile
from pathlib import Path
root=Path(__file__).resolve().parents[1];sys.path.insert(0,str(root/'scripts'))
parser=argparse.ArgumentParser();parser.add_argument('--dmg',type=Path,required=True);args=parser.parse_args()
import lab_signing,durable_release as dr,durable_transaction as tx
from release_transaction import Plan,Asset,FeedTarget,DryRunGateway
path=args.dmg.resolve()
data=path.read_bytes();seed=os.urandom(32);pub=lab_signing.public_key(seed);sig=lab_signing.signature(seed,data);encoded=base64.b64encode(sig).decode()
assert lab_signing.verify(pub,data,sig)
assert not lab_signing.verify(pub,data+b'x',sig)
url='https://github.com/BrOKenAG/ordina-releases/releases/download/v1.10.0-build.36/ordina-1.10.0-build.36-candidate.dmg'
xml=(f'<rss xmlns:sparkle="http://www.andymatuschak.org/xml-namespaces/sparkle"><channel><item><sparkle:version>36</sparkle:version><sparkle:shortVersionString>1.10.0</sparkle:shortVersionString><enclosure url="{url}" length="{len(data)}" sparkle:edSignature="{encoded}"/></item></channel></rss>').encode()
class Gateway(DryRunGateway):
 def verify_signature(self,path,signature):return lab_signing.verify(pub,path.read_bytes(),base64.b64decode(signature))
with tempfile.TemporaryDirectory(prefix='ordina-app-dry-run-') as tmp:
 tmp=Path(tmp);plan=Plan(label='lab candidate',repository='BrOKenAG/ordina-releases',tag='v1.10.0-build.36',title='lab candidate',notes='',asset=Asset(path.name,data,url),ed_signature=encoded,feeds=[FeedTarget('appcast',xml,path=root/'appcast.xml')],commit_paths=['appcast.xml'],commit_message='lab candidate')
 staged=dr.prepare(plan,'app-lab-candidate',journal_dir=tmp/'journals');backend=dr.DryRunBackend(staged.plan['feeds']);result=dr.execute(plan,Gateway(data),backend,staged,tmp)
 tx.rollback(staged.journal,backend)
 print('ISOLATED ONLY:',result['stages'],'rollback: pass','sha256:',hashlib.sha256(data).hexdigest())
