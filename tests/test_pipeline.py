import asyncio
import hashlib
import struct
import tempfile
import unittest
from pathlib import Path

from app.payload import Payload
from app.security import SourceError
from test_payload import f, MemoryRemote


def multi_fixture(count=12):
    chunks=[bytes([i+1])*4096 for i in range(count)]
    # Source operations arrive in reverse target order.
    image=b''.join(reversed(chunks)); raw=b''.join(chunks)
    ops=b''
    for i,chunk in enumerate(chunks):
        ops+=f(8,f(1,0)+f(2,i*4096)+f(3,4096)+f(6,f(1,count-i-1)+f(2,1))+f(8,hashlib.sha256(chunk).digest()))
    part=f(1,b'boot')+f(7,f(1,len(image))+f(2,hashlib.sha256(image).digest()))+ops
    manifest=f(3,4096)+f(13,part)
    return struct.pack('>4sQQI',b'CrAU',2,len(manifest),0)+manifest+raw,image


class DelayedRemote(MemoryRemote):
    def __init__(self,data):
        super().__init__(data)
        self.active=0; self.peak=0; self.requests=0; self.delay=False
        self.started=asyncio.Event(); self.corrupt=False

    async def read(self,offset,length):
        if not self.delay: return await super().read(offset,length)
        self.active+=1; self.peak=max(self.peak,self.active); self.requests+=1
        self.started.set()
        try:
            await asyncio.sleep(.03 if offset%3 else .01)
            raw=await super().read(offset,length)
            return b'X'+raw[1:] if self.corrupt else raw
        finally:
            self.active-=1


class PipelineTests(unittest.IsolatedAsyncioTestCase):
    async def test_slow_first_range_does_not_block_other_download_slots(self):
        data,image=multi_fixture(); remote=MemoryRemote(data)
        payload=await Payload(remote).load()
        original=remote.read; release=asyncio.Event(); last_started=asyncio.Event()
        async def read(offset,length):
            if offset==payload.base: await release.wait()
            if offset==payload.base+10*4096: last_started.set()
            return await original(offset,length)
        remote.read=read
        with tempfile.TemporaryDirectory() as folder:
            task=asyncio.create_task(payload.extract('boot',folder,workers=2,batch_bytes=8192))
            try:
                await asyncio.wait_for(last_started.wait(),3)
                self.assertFalse(task.done())
                release.set(); result=await task
                self.assertEqual(Path(result['path']).read_bytes(),image)
            finally:
                release.set()
                if not task.done(): task.cancel()
                await asyncio.gather(task,return_exceptions=True)

    async def test_coalesced_parallel_ranges_preserve_extents_and_checksums(self):
        data,image=multi_fixture(); remote=DelayedRemote(data)
        payload=await Payload(remote).load(); remote.delay=True
        progress=[]
        async def updated(done,total): progress.append(done)
        with tempfile.TemporaryDirectory() as folder:
            result=await payload.extract('boot',folder,updated,workers=3,batch_bytes=8192)
            self.assertEqual(Path(result['path']).read_bytes(),image)
            self.assertEqual(result['md5'],hashlib.md5(image).hexdigest())
            self.assertEqual(result['sha256'],hashlib.sha256(image).hexdigest())
            self.assertEqual(remote.requests,6)
            self.assertEqual(remote.peak,3)
            self.assertEqual(progress,sorted(progress))
            self.assertEqual(progress[-1],len(image))

    async def test_corrupt_prefetched_batch_cancels_remaining_tasks_and_cleans_partial(self):
        data,_=multi_fixture(); remote=DelayedRemote(data)
        payload=await Payload(remote).load(); remote.delay=remote.corrupt=True
        with tempfile.TemporaryDirectory() as folder:
            with self.assertRaises(SourceError):
                await payload.extract('boot',folder,workers=3,batch_bytes=8192)
            self.assertEqual(list(Path(folder).iterdir()),[])
        self.assertEqual(remote.active,0)

    async def test_cancelled_pipeline_drains_prefetch_before_cleanup(self):
        data,_=multi_fixture(); remote=DelayedRemote(data)
        payload=await Payload(remote).load(); remote.delay=True
        with tempfile.TemporaryDirectory() as folder:
            task=asyncio.create_task(payload.extract('boot',folder,workers=3,batch_bytes=8192))
            await remote.started.wait(); task.cancel()
            with self.assertRaises(asyncio.CancelledError): await task
            self.assertEqual(list(Path(folder).iterdir()),[])
        self.assertEqual(remote.active,0)

    async def test_overlapping_target_extents_are_rejected_before_download(self):
        data,_=multi_fixture(); remote=DelayedRemote(data)
        payload=await Payload(remote).load(); remote.delay=True
        payload.parts['boot']['ops'][1]['extents']=payload.parts['boot']['ops'][0]['extents']
        with tempfile.TemporaryDirectory() as folder:
            with self.assertRaises(SourceError): await payload.extract('boot',folder)
            self.assertEqual(list(Path(folder).iterdir()),[])
        self.assertEqual(remote.requests,0)
