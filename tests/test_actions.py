import io
import json
import unittest
import zipfile

from app.actions import read_limited, decode_result
from app.security import SourceError


class FragmentedStream:
    def __init__(self,data): self.data=data
    async def read(self,n):
        out=self.data[:min(n,7)]
        self.data=self.data[len(out):]
        return out


class ArtifactTests(unittest.IsolatedAsyncioTestCase):
    async def test_fragmented_artifact_is_read_until_eof(self):
        buf=io.BytesIO()
        result={'id':'abcdef012345','state':'done','files':[]}
        with zipfile.ZipFile(buf,'w') as z: z.writestr('result.json',json.dumps(result))
        raw=buf.getvalue()
        received=await read_limited(FragmentedStream(raw),len(raw))
        self.assertEqual(decode_result(received,result['id']),result)

    async def test_fragmented_oversize_artifact_is_rejected(self):
        with self.assertRaises(SourceError):
            await read_limited(FragmentedStream(bytes(33)),32)
