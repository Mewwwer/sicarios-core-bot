"""Build-time backport for exactly the reviewed EmpireCore/websocket pins.

Run after pip install, before starting Python processes which import EmpireCore.
No runtime monkeypatch, new client, socket or credential access.
"""
from hashlib import sha256
from importlib import metadata
from pathlib import Path
import argparse

CONNECTION_SHA256 = '03729a3eba3ccdbf0b56d3241dea41b613197924285456b2005aa168b16e24e0'
DEFLATE_SHA256 = 'cf8a326cf80ac9836f981765d2049863190fc9e1402369727d2fd2b864eddf89'
IMPORT = b'from empire_core.network.framing import FrameBuffer\n'
NEW_IMPORT = b'from empire_core.network.deflate import PERMESSAGE_DEFLATE_OFFER, accept_permessage_deflate\n' + IMPORT
CONNECT = b'                ws.connect(self.url)\n'
NEW_CONNECT = (b'                ws.connect(self.url, header=[PERMESSAGE_DEFLATE_OFFER])\n'
               b'                accept_permessage_deflate(ws)\n')


def original_connection(source):
    """Recognize both the exact original file and our exact, idempotent delta."""
    if sha256(source).hexdigest() == CONNECTION_SHA256:
        return source
    original = source.replace(NEW_IMPORT, IMPORT).replace(NEW_CONNECT, CONNECT)
    if source.count(NEW_IMPORT) == 1 and source.count(NEW_CONNECT) == 1 and sha256(original).hexdigest() == CONNECTION_SHA256:
        return original
    raise ValueError('Unreviewed EmpireCore connection source; refusing backport')


def install(package_root, *, check=False):
    connection = package_root / 'network' / 'connection.py'
    target = package_root / 'network' / 'deflate.py'
    source = connection.read_bytes()
    original = original_connection(source)
    vendored = Path(__file__).with_name('deflate.py').read_bytes()
    if sha256(vendored).hexdigest() != DEFLATE_SHA256:
        raise ValueError('Unreviewed deflate backport source')
    patched = original.replace(IMPORT, NEW_IMPORT).replace(CONNECT, NEW_CONNECT)
    if target.exists() and target.read_bytes() != vendored:
        raise ValueError('Unexpected deflate module; refusing overwrite')
    if check:
        if source != patched or not target.exists():
            raise ValueError('Deflate backport not installed; run patches/apply_deflate.py after pip install')
        return
    # All source checks precede writes. A failure aborts the image build; a
    # partial local install can be safely completed by rerunning this command.
    target.write_bytes(vendored)
    connection.write_bytes(patched)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--check', action='store_true', help='verify without writing')
    args = parser.parse_args()
    if metadata.version('empire-core') != '0.49.0' or metadata.version('websocket-client') != '1.9.2':
        raise ValueError('Backport requires empire-core 0.49.0 and websocket-client 1.9.2')
    root = Path(metadata.distribution('empire-core').locate_file('empire_core'))
    install(root, check=args.check)
    print('EmpireCore 0.49.0 + permessage-deflate backport f96ab419: verified')


if __name__ == '__main__':
    main()
