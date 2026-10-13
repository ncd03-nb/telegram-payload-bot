import asyncio
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock

from app.main import Bot
from app.settings import Settings


class ConcurrencyTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.folder=tempfile.TemporaryDirectory()
        self.bot=Bot(Settings(token='1:fixture',users={1},data_dir=Path(self.folder.name),max_concurrent=0))
        self.bot.tg.edit=AsyncMock()
        self.bot.tg.send=AsyncMock(return_value={'message_id':100})

    def tearDown(self):
        self.bot.store.close(); self.folder.cleanup()

    def job(self,index):
        j=self.bot.store.add(index,index,index,'https://example.com/rom.zip','boot',backend='actions')
        self.bot.store.change(j['id'],message_id=index+100)
        return self.bot.store.get(j['id'],index)

    async def test_all_accepted_jobs_dispatch_while_prior_job_is_still_running(self):
        jobs=[self.job(i) for i in range(1,7)]
        # Resume an existing runner instead of dispatching it again.
        self.bot.store.change(jobs[0]['id'],state='running',run_id=123,dispatch_at=1,url='')
        started=set(); all_started=asyncio.Event(); release=asyncio.Event()
        async def observe(job,store):
            started.add(job['id'])
            if len(started)==6: all_started.set()
            await release.wait()
            return {'state':'done','files':[],'notified':True}
        self.bot.actions.observe=observe
        self.bot.actions.dispatch=AsyncMock(return_value={})
        worker=asyncio.create_task(self.bot.worker())
        try:
            await asyncio.wait_for(all_started.wait(),3)
            self.assertEqual(len(self.bot.job_tasks),6)
            self.assertEqual(self.bot.actions.dispatch.await_count,5)
            dispatched=[call.args[0]['id'] for call in self.bot.actions.dispatch.await_args_list]
            self.assertEqual(len(dispatched),len(set(dispatched)))
            self.assertNotIn(jobs[0]['id'],dispatched)
            self.assertTrue(all(self.bot.store.get(j['id'],j['owner'])['state']=='running' for j in jobs))
        finally:
            worker.cancel(); await asyncio.gather(worker,return_exceptions=True)
        # Controller shutdown leaves runners recoverable, without marking them failed.
        self.assertEqual(self.bot.job_tasks,{})
        self.assertTrue(all(self.bot.store.get(j['id'],j['owner'])['state']=='running' for j in jobs))

    async def test_configured_limit_releases_slot_without_reclaiming_active_job(self):
        self.bot.s.max_concurrent=2
        jobs=[self.job(i) for i in range(1,4)]
        entered=set(); first_two=asyncio.Event(); third=asyncio.Event(); release=asyncio.Event()
        async def process(job):
            entered.add(job['id'])
            if len(entered)==2: first_two.set()
            if len(entered)==3: third.set()
            await release.wait()
            self.bot.store.change(job['id'],state='done')
        self.bot.process_job=process
        worker=asyncio.create_task(self.bot.worker())
        try:
            await asyncio.wait_for(first_two.wait(),3)
            self.assertFalse(third.is_set())
            self.assertEqual(self.bot.store.get(jobs[2]['id'],3)['state'],'queued')
            release.set(); await asyncio.wait_for(third.wait(),3)
            self.assertEqual(len(entered),3)
        finally:
            worker.cancel(); await asyncio.gather(worker,return_exceptions=True)

    async def test_cancelled_claim_is_not_dispatched(self):
        j=self.job(1); self.bot.store.claim(); self.bot.store.change(j['id'],state='cancelled')
        self.bot.actions.dispatch=AsyncMock()
        await self.bot.process_job(j)
        self.bot.actions.dispatch.assert_not_awaited()
