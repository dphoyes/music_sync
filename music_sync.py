#!/usr/bin/env python3

import os
import typing
import tempfile
import subprocess
import contextlib
import dataclasses
import shutil
from pathlib import PurePath, Path


@contextlib.contextmanager
def mount_phone():
    with tempfile.TemporaryDirectory() as mount_dir:
        try:
            print("Mounting phone")
            subprocess.run(('adbfs', mount_dir), check=True)
            yield Path(mount_dir)
        finally:
            print("Unmounting phone")
            subprocess.run(('fusermount', '-u', mount_dir))


@dataclasses.dataclass
class File:
    local_path: Path
    remote_path: Path

    def convert(self):
        shutil.copyfile(self.local_path, self.remote_path)


class OpusConvertFile(File):
    def convert(self):
        subprocess.run((
            'ffmpeg', '-y',
            '-i', self.local_path,
            '-vn', '-c:a', 'libopus',
            '-b:a', '70k',
            self.remote_path,
        ), check=True)


@dataclasses.dataclass
class M3uConvertFile(File):
    LOCAL_ROOT: Path

    def convert(self):
        prefix = os.path.relpath(self.LOCAL_ROOT, self.local_path.parent) + '/'
        for char in r'\/&':
            prefix = prefix.replace(char, f'\\{char}')

        with open(self.remote_path, 'w') as f:
            subprocess.run((
                'sed', '-e', rf's/^/{prefix}/g', '-e', r's/\.flac/\.ogg/g', self.local_path
            ), check=True, stdout=f)


@dataclasses.dataclass
class Dir:
    local_path: Path
    remote_path: Path


class Sync:
    def convert_path_local_to_remote(self, path: Path):
        try:
            rel_to_playlist_dir = path.relative_to(self.LOCAL_ROOT / '.playlists')
        except ValueError:
            return self.REMOTE_ROOT / (path.relative_to(self.LOCAL_ROOT))
        else:
            return self.REMOTE_ROOT / 'Playlists' / rel_to_playlist_dir

    def scan_local(self, local_dir: Path):
        yield Dir(
            local_path=local_dir,
            remote_path=self.convert_path_local_to_remote(local_dir),
        )
        with os.scandir(local_dir) as it:
            for entry in it:
                entry = Path(entry)
                if entry.is_file():
                    if entry.suffix == '.flac':
                        yield OpusConvertFile(
                            local_path=entry,
                            remote_path=self.convert_path_local_to_remote(entry).with_suffix('.ogg'),
                        )
                    elif entry.suffix == '.m3u':
                        yield M3uConvertFile(
                            local_path=entry,
                            remote_path=self.convert_path_local_to_remote(entry),
                            LOCAL_ROOT=self.LOCAL_ROOT,
                        )
                    else:
                        yield File(
                            local_path=entry,
                            remote_path=self.convert_path_local_to_remote(entry),
                        )
                else:
                    assert entry.is_dir()
                    if entry.name == '.mediaartlocal':
                        continue
                    yield from self.scan_local(entry)

    def scan_remote(self, mount_dir: Path, relative_path: Path):
        remote_starting_point = str(PurePath('/') / relative_path)
        proc = subprocess.run(
            ('adb', 'shell', rf'find {remote_starting_point} -print0 | xargs -0 stat -c "%Y %n"'),
            capture_output=True, check=True, encoding='utf8',
        )

        for line in proc.stdout.splitlines():
            mtime, filepath = line.split(maxsplit=1)
            filepath = mount_dir / PurePath(filepath).relative_to('/')
            yield filepath, float(mtime)

    def __init__(self, mount_dir):
        self.MOUNT_DIR = mount_dir
        self.REMOTE_ROOT = mount_dir / 'sdcard' / 'Music'
        self.LOCAL_ROOT = Path.home() / 'Music'
        self.local_collection = list(self.scan_local(self.LOCAL_ROOT))

    def sync(self):
        print("Scanning remote")
        remote_file_mtimes = dict(self.scan_remote(self.MOUNT_DIR, self.REMOTE_ROOT.relative_to(self.MOUNT_DIR)))

        to_delete = sorted((set(remote_file_mtimes) - {p.remote_path for p in self.local_collection}), reverse=True)
        for f in to_delete:
            print(f"Deleting {f}")
            if f.is_dir():
                f.rmdir()
            else:
                f.unlink()

        for f in self.local_collection:
            remote_mtime = remote_file_mtimes.get(f.remote_path)
            if (
                remote_mtime is None
                or f.local_path.is_file() and remote_mtime < f.local_path.stat().st_mtime
            ):
                if isinstance(f, Dir):
                    print(f"Creating directory {f.remote_path}")
                    f.remote_path.mkdir()
                else:
                    assert isinstance(f, File)
                    print(f"Syncing {f.remote_path}")
                    if f.remote_path.exists():
                        f.remote_path.unlink()
                    f.convert()
            else:
                pass
                # print(f"Skipping {f.remote_path}")

    @classmethod
    def main(cls):
        with mount_phone() as mount_dir:
            cls(mount_dir).sync()


if __name__ == "__main__":
    Sync.main()
