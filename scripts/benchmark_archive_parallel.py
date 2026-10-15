"""Compare 4/8 IAS3 streams, then repeat the fastest with a larger file."""
import asyncio
import json
import os
import sys
import tempfile
import time
import uuid
from pathlib import Path

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
sys.path.insert(0,str(Path(__file__).resolve().parent))
from app.archive import Archive,ArchiveError,verify_download
from benchmark_archive import sample


async def main():
    report={'samples':[],'threshold_mib_s':20,'recommended':False}
    workers_list=[int(x) for x in os.getenv('BENCHMARK_WORKERS','4,8').split(',')]
    part_mib=int(os.getenv('BENCHMARK_PART_MIB','32'))
    repeat_mib=int(os.getenv('BENCHMARK_REPEAT_MIB','2048'))
    small_mib=int(os.getenv('BENCHMARK_SMALL_MIB','512'))
    if not 1<=len(workers_list)<=2 or any(not 1<=n<=16 or n*part_mib>256 for n in workers_list) or not 5<=part_mib<=64 or not 512<=small_mib<=4096 or not 1024<=repeat_mib<=4096:
        raise ValueError('Invalid benchmark bounds')
    output=Path(os.environ['RESULT_PATH']);output.parent.mkdir(parents=True,exist_ok=True)
    def save():output.write_text(json.dumps(report,indent=2))
    pending=[]
    try:
        with tempfile.TemporaryDirectory() as folder:
            file=await asyncio.to_thread(sample,Path(folder)/('multipart-test-'+str(small_mib)+'m.bin'),small_mib)
            for workers in workers_list:
                row=await measure(file,workers,part_mib,report,save);pending.append((file,row))
            rows=[r for r in report['samples'] if r.get('parts_mib_s',0)>=18]
            if rows:
                best=max(rows,key=lambda r:r['mib_s'])
                file=await asyncio.to_thread(sample,Path(folder)/('multipart-test-'+str(repeat_mib)+'m.bin'),repeat_mib)
                row=await measure(file,best['workers'],part_mib,report,save);pending.append((file,row))
                if row.get('mib_s',0)>=20 and row.get('url'):
                    async with Archive(os.environ['IA_ACCESS_KEY'],os.environ['IA_SECRET_KEY'],workers=best['workers'],part_mib=part_mib) as client:
                        try:
                            async with asyncio.timeout(360):row.update(await verify_download(client.session,file,row['url'],wait=180))
                        except Exception as error:row['verify_error']=type(error).__name__
                    print(json.dumps(row),flush=True);save()
                    report['recommended']=bool(row.get('verified') and row.get('range_ok') and row.get('mib_s',0)>=20)
            for file,row in pending:
                try:
                    async with Archive(os.environ['IA_ACCESS_KEY'],os.environ['IA_SECRET_KEY']) as client:
                        await client.cleanup(file,row['identifier'])
                    row['cleanup_requested']=True
                except Exception:row['cleanup_requested']=False
                save()
    finally:
        save();print(json.dumps(report),flush=True)


async def measure(file,workers,part_mib,report,save):
    row={'workers':workers,'part_mib':part_mib,'bytes':file['size'],'identifier':'bot-upload-test-'+uuid.uuid4().hex}
    report['samples'].append(row);save();started=time.monotonic()
    async def progress(done,total):
        print(json.dumps({'workers':workers,'uploaded_mib':done//1024**2,'total_mib':total//1024**2,
            'elapsed':round(time.monotonic()-started,1)}),flush=True)
    try:
        async with Archive(os.environ['IA_ACCESS_KEY'],os.environ['IA_SECRET_KEY'],workers=workers,part_mib=part_mib) as client:
            async with asyncio.timeout(300):
                row.update(await client.multipart(file,row['identifier'],progress))
        elapsed=time.monotonic()-started;row.update(seconds=round(elapsed,3),mib_s=round(file['size']/1024**2/elapsed,2))
    except Exception as error:row['error']=str(error) if isinstance(error,ArchiveError) else type(error).__name__
    print(json.dumps(row),flush=True);save();return row


if __name__=='__main__':
    try:asyncio.run(main())
    except Exception as error:
        print('Benchmark stopped: '+type(error).__name__,flush=True);raise SystemExit(1) from None
