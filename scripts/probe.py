"""Read a private JSON mapping brand -> URL; save verified extraction reports locally."""
import argparse
import asyncio
import json
import sys
import time
from pathlib import Path

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from app.remote import Remote
from app.payload import Payload


async def probe(brand,url,output,names):
    started=time.monotonic()
    async with Remote(url) as remote:
        payload=await Payload(remote).load()
        report={'source_bytes':remote.size,'metadata_bytes':remote.downloaded,'partitions':payload.summary(),'extracted':[]}
        for name in names:
            result=await payload.extract(name,output/brand)
            report['extracted'].append(result)
            print(brand,name,result['size'],result['sha256'],flush=True)
        report.update(downloaded=remote.downloaded,seconds=round(time.monotonic()-started,2))
        output.mkdir(parents=True,exist_ok=True)
        (output/(brand+'-report.json')).write_text(json.dumps(report,indent=2),encoding='utf-8')


async def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--sources',type=Path,default=Path('config/test-sources.json'))
    parser.add_argument('--output',type=Path,default=Path('data'))
    parser.add_argument('--partitions',default='vbmeta,init_boot,boot')
    args=parser.parse_args()
    for brand,url in json.loads(args.sources.read_text()).items():
        await probe(brand,url,args.output,args.partitions.split(','))


if __name__=='__main__': asyncio.run(main())
