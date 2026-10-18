"""Reserve new resources without overwriting existing jobs or uploaded files."""
from pathlib import Path
import os
import shutil
import tempfile
import uuid


def unique_label(requested, existing, limit=100):
    base = ' '.join(str(requested).split())[:limit] or 'Job'
    occupied = {' '.join(str(label).split()).casefold() for label in existing}
    candidate, number = base, 2
    while candidate.casefold() in occupied:
        suffix = f' ({number})'
        candidate = base[:limit-len(suffix)] + suffix
        number += 1
    return candidate


def reserve_job_directory(root, reserved_ids=()):
    for _ in range(100):
        identifier = uuid.uuid4().hex[:10]
        if identifier in reserved_ids:
            continue
        directory = Path(root) / identifier
        try:
            directory.mkdir()
        except FileExistsError:
            continue
        return directory
    raise RuntimeError('Could not reserve a new job ID; existing jobs have been preserved')


def publish_upload(directory, filename, stream, validate=None):
    """Copy to a hidden temporary file, then publish by an exclusive hard link."""
    directory = Path(directory)
    filename = Path(filename).name
    descriptor, name = tempfile.mkstemp(dir=directory, prefix='.upload-', suffix='.partial')
    temporary = Path(name)
    try:
        with os.fdopen(descriptor, 'wb') as output:
            shutil.copyfileobj(stream, output, length=1024*1024)
            output.flush()
            os.fsync(output.fileno())
        if temporary.stat().st_size == 0:
            raise ValueError('ไฟล์ที่อัปโหลดว่าง กรุณาเลือกไฟล์แล้วลองใหม่')
        if validate:
            validate(temporary)
        original = Path(filename)
        for number in range(1, 10001):
            candidate = filename if number == 1 else f'{original.stem} ({number}){original.suffix}'
            target = directory / candidate
            try:
                # Atomic and exclusive on NTFS/POSIX; concurrent requests cannot replace each other.
                os.link(temporary, target)
            except FileExistsError:
                continue
            return target
        raise ValueError('มีไฟล์ชื่อนี้จำนวนมาก กรุณาเปลี่ยนชื่อไฟล์ก่อนอัปโหลด')
    finally:
        temporary.unlink(missing_ok=True)
