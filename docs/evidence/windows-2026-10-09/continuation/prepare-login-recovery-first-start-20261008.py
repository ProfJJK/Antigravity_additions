"""Create versioned first-start sources. No deployment or credential operations."""
import hashlib
from pathlib import Path

HERE = Path(__file__).resolve().parent

def pinned(name, pin):
    raw = (HERE / name).read_bytes()
    assert hashlib.sha256(raw).hexdigest() == pin, name
    return raw.decode('utf-8-sig')

def write_new(name, text):
    with (HERE / name).open('x', encoding='utf-8', newline='') as stream:
        stream.write(text)

def main():
    auth_pin = hashlib.sha256((HERE/'worker-native-auth-status-six-r3-v2.py').read_bytes()).hexdigest()
    source = pinned('commission-first-warden-r3-v1.py', 'bd6ed9cc62bc96777d5748e819c5f780ada2e58ce0d43bf7e5f2013ca72f451c')
    for kind in ('Both', 'Six', 'StatusSix'):
        source = source.replace(f'NativeAuth{kind}4.2.7-windows-20261007-r3-v1', f'NativeAuth{kind}4.2.7-windows-20261008-r3-v2')
    source = source.replace("AUTH_HELPER_HASH = 'cd93a330a2d4c578871b4d479067cc5f65f5ddd49bf2a6154c2910f63b5d1ea5'", f"AUTH_HELPER_HASH = '{auth_pin}'")
    write_new('commission-first-warden-r3-v2.py', source)
    new_pin = hashlib.sha256((HERE/'commission-first-warden-r3-v2.py').read_bytes()).hexdigest()
    wrapper = pinned('commission-first-warden-r3-v1.ps1', '75efe248449fa9be0317a85feb277d82a961d05594ee754d11b3b9d0ec4dfd1e')
    for kind in ('Both', 'Six', 'StatusSix'):
        wrapper = wrapper.replace(f'NativeAuth{kind}4.2.7-windows-20261007-r3-v1', f'NativeAuth{kind}4.2.7-windows-20261008-r3-v2')
    # The unused commissioning destination and its action retain their existing
    # contract. Only the reviewed input source and authentication chain change.
    wrapper = wrapper.replace("$source=Join-Path $PSScriptRoot 'commission-first-warden-r3-v1.py';$sourceHash='bd6ed9cc62bc96777d5748e819c5f780ada2e58ce0d43bf7e5f2013ca72f451c'", f"$source=Join-Path $PSScriptRoot 'commission-first-warden-r3-v2.py';$sourceHash='{new_pin}'")
    write_new('commission-first-warden-r3-v2.ps1', wrapper)
    print(new_pin)

if __name__ == '__main__':
    main()
