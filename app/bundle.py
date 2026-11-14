"""Append verified images to a ZIP64 archive, removing each image after packing."""
import shutil
import zipfile
from pathlib import Path

from .payload import file_checksums
from .security import SourceError
from .threads import thread_call
from .partition_layout import OPLUS_FOLDERS, oplus_folder


class Bundle:
    def __init__(self, directory, task_id, layout=None):
        if layout not in {None, 'oplus'}:
            raise ValueError('Unknown partition archive layout')
        self.layout = layout
        self.path = Path(directory) / ('payload-' + task_id + '.zip')
        self.partial = self.path.with_suffix('.zip.partial')
        self.archive = None
        self.members = []

    def __enter__(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.archive = zipfile.ZipFile(self.partial, 'w', compression=zipfile.ZIP_DEFLATED,
                                       compresslevel=1, allowZip64=True)
        if self.layout == 'oplus':
            for folder in OPLUS_FOLDERS:
                self.archive.mkdir(folder + '/')
        return self

    async def add(self, image):
        path = Path(image['path'])
        if path.parent != self.path.parent or path.name != image['name'] or path.suffix != '.img':
            raise SourceError('Ảnh nằm ngoài thư mục tác vụ.')
        # Conservative bound: incompressible input can take its full size again in ZIP.
        if shutil.disk_usage(self.path.parent).free < image['size'] + 256*1024**2:
            raise SourceError('Runner không đủ chỗ tạo ZIP; chọn ít phân vùng hơn.')
        archive_name = (oplus_folder(path.stem) + '/' if self.layout == 'oplus' else '') + image['name']
        await thread_call(self.archive.write, path, archive_name)
        self.members.append({**{k:image[k] for k in ('name','size','sha256')}, 'name':archive_name})
        path.unlink()

    async def finish(self):
        await thread_call(self.archive.close)
        self.archive = None
        self.partial.replace(self.path)
        checksums = await thread_call(file_checksums, self.path)
        return {'path':str(self.path),'name':self.path.name,'size':self.path.stat().st_size,
                **checksums,'members':self.members}

    def __exit__(self, *args):
        if self.archive:
            self.archive.close()
        self.partial.unlink(missing_ok=True)
