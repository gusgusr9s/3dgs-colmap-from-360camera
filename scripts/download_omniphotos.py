"""Fetch original stitched videos from the CC BY 4.0 OmniPhotos archive.

HTTP ranges avoid downloading the entire 18 GB ZIP. ZIP CRC is checked by
zipfile, and only the explicitly selected MP4 entries are extracted.
"""
import argparse
import hashlib
import io
import json
from pathlib import Path
import re
import urllib.request
import zipfile

ARCHIVE = 'https://researchdata.bath.ac.uk/948/6/Unprocessed.zip'
ROOT = Path(__file__).resolve().parents[1]


class RemoteZip(io.RawIOBase):
    def __init__(self, url):
        self.url, self.position = url, 0
        with urllib.request.urlopen(urllib.request.Request(url, headers={'Range': 'bytes=0-0'}), timeout=60) as response:
            if response.status != 206:
                raise ValueError('Server must support HTTP byte ranges')
            self.size = int(response.headers['Content-Range'].split('/')[-1])

    def seekable(self):
        return True

    def seek(self, offset, whence=0):
        self.position = offset if whence == 0 else self.position + offset if whence == 1 else self.size + offset
        if self.position < 0:
            raise ValueError('Negative offset')
        return self.position

    def tell(self):
        return self.position

    def read(self, size=-1):
        size = min(self.size-self.position, size if size >= 0 else self.size)
        if size <= 0:
            return b''
        start, end = self.position, self.position+size-1
        for attempt in range(3):
            try:
                with urllib.request.urlopen(urllib.request.Request(self.url, headers={'Range': f'bytes={start}-{end}'}), timeout=120) as response:
                    if response.status != 206 or response.headers.get('Content-Range') != f'bytes {start}-{end}/{self.size}':
                        raise ValueError('Incorrect archive byte range')
                    data = response.read()
                if len(data) != size:
                    raise IOError('Incomplete archive byte range')
                self.position += size
                return data
            except OSError:
                if attempt == 2:
                    raise


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--scene', action='append', default=[])
    args = parser.parse_args()
    with zipfile.ZipFile(RemoteZip(ARCHIVE)) as archive:
        videos = [entry for entry in archive.infolist() if entry.filename.lower().endswith('.mp4')]
        for entry in videos:
            if not args.scene:
                print(f'{entry.filename}\t{entry.file_size/1024/1024:.1f} MB', flush=True)
                continue
            parts = entry.filename.split('/')
            scene = parts[1] if len(parts) > 2 else parts[0]
            if scene not in args.scene:
                continue
            filename = 'omniphotos_' + re.sub(r'[^a-z0-9]+', '_', scene.lower()).strip('_') + '.mp4'
            target = ROOT/'data'/'raw'/filename
            record_path = target.with_suffix('.source.json')
            if target.exists() and record_path.exists():
                print('Already downloaded: '+filename, flush=True)
                continue
            temporary = target.with_suffix('.mp4.partial')
            digest = hashlib.sha256()
            print('Downloading '+entry.filename, flush=True)
            with archive.open(entry) as source, temporary.open('wb') as output:
                while chunk := source.read(8*1024*1024):
                    output.write(chunk)
                    digest.update(chunk)
            temporary.replace(target)
            record = {'source': filename, 'scene': scene, 'archive': ARCHIVE,
                      'archive_entry': entry.filename, 'size_bytes': target.stat().st_size,
                      'sha256': digest.hexdigest(), 'license': 'CC BY 4.0',
                      'reference': 'https://researchdata.bath.ac.uk/948/',
                      'creators': 'Tobias Bertel, Mingze Yuan, Reuben Lindroos, Christian Richardt',
                      'capture': 'Insta360 One X; original stitched equirectangular MP4'}
            record_path.write_text(json.dumps(record, indent=2), encoding='utf-8')
            print(json.dumps(record), flush=True)


if __name__ == '__main__':
    main()
