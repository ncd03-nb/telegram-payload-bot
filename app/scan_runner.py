"""ROM metadata scanning runs on Actions; the controller stays lightweight."""
import asyncio
import os
from pathlib import Path
from .exchange import load_job_env,write_result
from .settings import Settings
from .runner import validate_identity
from .remote import Remote
from .image_source import load_source
from .security import SourceError


async def run():
    load_job_env();s=Settings.load();jid=os.environ['JOB_ID']
    owner,chat,message=(int(os.environ[k]) for k in ('JOB_OWNER','JOB_CHAT','JOB_MESSAGE_ID'))
    validate_identity(s,jid,owner,chat,message,int(os.getenv('JOB_THREAD_ID','0')),int(os.getenv('JOB_REPLY_TO','0')))
    result={'id':jid,'owner':owner,'kind':'scan','state':'failed'}
    try:
        async with asyncio.timeout(480),Remote(os.environ['JOB_SOURCE_URL']) as remote:
            payload=await load_source(remote)
            result.update(state='done',url=os.environ['JOB_SOURCE_URL'],metadata=payload.summary(),rom_version=remote.rom_version)
    except Exception as error:
        result['error']=str(error) if isinstance(error,SourceError) else 'Chưa đọc được ROM; kiểm tra link hoặc CDN.'
    finally:
        write_result(Path(os.environ['RESULT_PATH']),result,s.token)
    return 0 if result['state']=='done' else 1


if __name__=='__main__':raise SystemExit(asyncio.run(run()))
