"""Build two disposable Git repositories from public synthetic source fixtures."""
import json
import shutil
import subprocess
from pathlib import Path


def git(repo,*args):
    return subprocess.run(['git','-c','core.hooksPath=/dev/null','-c','core.fsmonitor=false',
                           '-c','commit.gpgsign=false','-c','user.name=Ticket Demo',
                           '-c','user.email=demo@example.invalid','-C',str(repo),*args],
                          capture_output=True,text=True,check=True,timeout=10).stdout.strip()


def build(folder):
    folder=Path(folder)
    if folder.exists() and any(folder.iterdir()):raise ValueError('fixture directory must be empty')
    folder.mkdir(parents=True,exist_ok=True)
    source=Path(__file__).resolve().parents[1]/'fixtures/source'
    repositories={};deployed={};latest={}
    for name in ('frontend','backend'):
        repo=folder/name;repo.mkdir();git(repo,'init','-b','main')
        revisions={}
        for version in ('v1','v2'):
            for path in (source/name/version).glob('*.mjs'):shutil.copy2(path,repo/path.name)
            git(repo,'add','--','.')
            git(repo,'commit','-m',f'Synthetic {name} {version}')
            revisions[version]=git(repo,'rev-parse','HEAD')
        repositories[name]={'path':str(repo.resolve()),'allowed_files':sorted(p.name for p in (source/name/'v1').glob('*.mjs'))}
        deployed[name]=revisions['v1'];latest[name]=revisions['v2']
    manifest={'synthetic':True,'repositories':repositories,'deployments':[
        {'brand':'demo-brand','store':'store-001','valid_from':'2026-09-18T00:00:00+08:00',
         'valid_to':'2026-09-19T00:00:00+08:00','revisions':deployed},
        {'brand':'demo-brand','store':'store-002','valid_from':'2026-09-18T00:00:00+08:00',
         'valid_to':'2026-09-19T00:00:00+08:00','revisions':latest}]}
    (folder/'deployments.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2))
    return manifest
