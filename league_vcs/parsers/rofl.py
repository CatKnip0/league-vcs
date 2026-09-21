import json
import struct

from league_vcs.exceptions import UserInputException


class ROFLParser:
    MAGIC = b'RIOT'

    def __init__(self, path):
        try:
            with open(path, 'rb') as f:
                magic = f.read(4)
                if magic != self.MAGIC:
                    raise ValueError('Not a ROFL file')

                fmt_version = struct.unpack('<H', f.read(2))[0]

                if fmt_version >= 2:
                    self.version = self._parse_v2(f)
                else:
                    self.version = self._parse_v1(f)
        except (UserInputException, Exception):
            raise UserInputException(f'Invalid replay file at {path}!')

    @staticmethod
    def _parse_v2(f):
        f.seek(0x0F)
        ver = b''
        for _ in range(64):
            b = f.read(1)
            if not b or b[0] < 0x20 or b[0] > 0x7e:
                break
            ver += b
        if not ver:
            raise ValueError('No version string found')
        return ver.decode('ascii')

    @staticmethod
    def _parse_v1(f):
        f.seek(262)
        buf = f.read(26)
        metadata_offset = int.from_bytes(buf[6:10], byteorder='little', signed=False)
        metadata_length = int.from_bytes(buf[10:14], byteorder='little', signed=False)
        f.seek(metadata_offset)
        metadata = json.loads(f.read(metadata_length))
        return metadata['gameVersion']
