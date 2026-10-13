"""Compare resumable chunk sizes on one runner; delete only our test files/folders."""
import asyncio
import base64
import hashlib
import json
import os
import sys
import tempfile
import time
import uuid
from pathlib import Path

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from app.gdrive import Drive


async def main():
    report = []
    with tempfile.TemporaryDirectory() as directory:
        folder = Path(directory)
        token = folder/'token.json'
        token.write_bytes(base64.b64decode(os.environ['GOOGLE_TOKEN_B64'],validate=True))
        token.chmod(0o600)
        path = folder/'drive-speed-test.bin'
        block = os.urandom(1024**2)
        sha, md5 = hashlib.sha256(), hashlib.md5()
        with path.open('wb') as f:
            for _ in range(512):
                f.write(block); sha.update(block); md5.update(block)
        file = {'path':str(path),'name':path.name,'size':path.stat().st_size,
                'sha256':sha.hexdigest(),'md5':md5.hexdigest()}
        for chunk in (32,128,256,32):
            task_id = uuid.uuid4().hex[:12]
            async with Drive(token,chunk_mib=chunk) as drive:
                parent = await drive.folder(task_id)
                started = time.monotonic()
                try:
                    uploaded = await drive.upload(file,task_id,1,public=False)
                    seconds = time.monotonic()-started
                    # Exact checksum/size were verified by Drive.upload.
                    await drive.api('DELETE','files/'+uploaded['id'])
                    row = {'chunk_mib':chunk,'bytes':file['size'],'seconds':round(seconds,3),
                           'mib_s':round(file['size']/1024**2/seconds,2),'verified':True,'deleted':True}
                    report.append(row)
                    print(json.dumps(row),flush=True)
                finally:
                    # This folder has our fresh random task marker, never a user task.
                    await drive.api('DELETE','files/'+parent)
    Path(os.getenv('RESULT_PATH','drive-benchmark.json')).write_text(json.dumps(report,indent=2))


if __name__=='__main__':
    asyncio.run(main())
