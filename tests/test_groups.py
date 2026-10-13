import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock

from app.main import Bot
from app.actions import Actions
from app.runner import validate_identity
from app.security import SourceError
from app.settings import Settings
from app.telegram import Telegram


class GroupTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.settings = Settings(token='1:fixture', users={1}, data_dir=Path(self.folder.name),
            public=True, groups=True, public_files=True)
        self.bot = Bot(self.settings)
        self.bot.username = 'pdoplus_bot'
        self.bot.tg.send = AsyncMock(return_value={'message_id':900})

    def tearDown(self):
        self.bot.store.close()
        self.folder.cleanup()

    def message(self, text, owner=2):
        return {'message_id':12, 'message_thread_id':5, 'from':{'id':owner, 'is_bot':False},
            'chat':{'id':-100123, 'type':'supergroup'}, 'text':text}

    async def test_public_group_command_replies_in_same_topic_without_username(self):
        m=self.message('/start')
        await self.bot.handle({'update_id':10,'message':m})
        self.bot.tg.send.assert_awaited_once()
        args=self.bot.tg.send.await_args
        self.assertEqual(args.args[0],-100123)
        self.assertEqual(args.kwargs,{'reply_to':12,'thread_id':5})
        self.assertIn('ai có link',args.args[1])

    async def test_foreign_command_and_group_chatter_are_ignored(self):
        for text in ('hello everyone','/start@another_bot','/unknown'):
            await self.bot.handle({'update_id':10,'message':self.message(text)})
        self.bot.tg.send.assert_not_awaited()
        await self.bot.handle({'update_id':11,'message':self.message('/start@pdoplus_bot')})
        self.bot.tg.send.assert_awaited_once()

    async def test_group_job_preserves_owner_and_topic_for_actions(self):
        self.bot.scan=AsyncMock(return_value=('rom',[{'name':'boot','supported':True,'size':8}]))
        await self.bot.handle({'update_id':15,'message':self.message('/extract https://example.com/rom.zip boot')})
        job=self.bot.store.recent(2)[0]
        self.assertEqual((job['owner'],job['chat'],job['thread_id'],job['message_id']),(2,-100123,5,900))
        self.bot.actions.request=AsyncMock(return_value={})
        await self.bot.actions.dispatch(job)
        inputs=self.bot.actions.request.await_args.args[2]['inputs']
        self.assertEqual((inputs['owner'],inputs['chat'],inputs['thread_id']),('2','-100123','5'))
        validate_identity(self.settings,job['id'],2,-100123,900,5)

    async def test_result_and_cancel_remain_scoped_to_requesting_user(self):
        file={'name':'boot.img','size':8,'url':'https://drive.google.com/file'}
        with self.bot.store.db:
            self.bot.store.db.execute('INSERT INTO outputs VALUES(?,?,?)',('abcdef012345',1,json.dumps([file])))
        with self.assertRaises(SourceError):
            await self.bot.handle({'update_id':16,'message':self.message('/files abcdef012345')})
        job=self.bot.store.add(17,1,-100123,'https://example.com/rom.zip','boot',backend='actions')
        with self.assertRaises(SourceError):
            await self.bot.handle({'update_id':18,'message':self.message('/cancel '+job['id'])})
        self.assertEqual(self.bot.store.get(job['id'],1)['state'],'queued')

    def test_public_and_private_modes_reject_invalid_senders(self):
        m=self.message('/start')
        self.assertTrue(self.bot.accepts(m))
        self.settings.public=False
        self.assertFalse(self.bot.accepts(m))
        m['from']['id']=1
        self.assertTrue(self.bot.accepts(m))
        self.settings.groups=False
        self.assertFalse(self.bot.accepts(m))
        self.settings.public=True; self.settings.groups=True
        m['from']['is_bot']=True
        self.assertFalse(self.bot.accepts(m))
        for chat in (0,3):
            with self.assertRaises(ValueError): validate_identity(self.settings,'abcdef012345',2,chat,1)

    async def test_telegram_reply_parameters_include_topic(self):
        tg=Telegram('1:fixture'); tg.call=AsyncMock()
        await tg.send(-100123,'hello',reply_to=12,thread_id=5)
        args=tg.call.await_args.kwargs
        self.assertEqual(args['reply_parameters']['message_id'],12)
        self.assertEqual(args['message_thread_id'],5)
