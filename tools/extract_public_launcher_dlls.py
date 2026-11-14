"""Read the pinned Windows launcher packet as data and extract only the QA DLLs."""

from __future__ import annotations

import argparse
from contextlib import contextmanager
import ctypes
import hashlib
import json
from pathlib import Path
import re
import struct
from tempfile import TemporaryDirectory

from prepare import no_links, require, reserve_path, safe_path


HEADER = struct.Struct("<8sIIIIQQ32s")
FILE_RECORD = struct.Struct("<QQ32s")
MAX_PAYLOAD = 512 * 1024 * 1024
MANAGED_DLLS = {"LANMinecraft.dll", "CmlLib.Core.dll", "CmlLib.Core.Commons.dll"}


@contextmanager
def launcher_resource(executable):
    if not hasattr(ctypes, "WinDLL"):
        raise ValueError("Reading launcher PE resources requires Windows")
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    pointer = ctypes.c_void_p

    def native(name, returns, *parameters):
        function = getattr(kernel, name)
        function.argtypes = parameters
        function.restype = returns
        return function

    load = native("LoadLibraryExW", pointer, ctypes.c_wchar_p, pointer, ctypes.c_uint32)
    find = native("FindResourceW", pointer, pointer, pointer, pointer)
    size_of = native("SizeofResource", ctypes.c_uint32, pointer, pointer)
    load_resource = native("LoadResource", pointer, pointer, pointer)
    lock = native("LockResource", pointer, pointer)
    free = native("FreeLibrary", ctypes.c_int, pointer)
    # LOAD_LIBRARY_AS_DATAFILE maps the file without executing any of its code.
    module = load(str(executable), None, 0x02)
    if not module:
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        resource = find(module, 101, 10)  # RT_RCDATA
        if not resource:
            raise ctypes.WinError(ctypes.get_last_error())
        resource_size = size_of(module, resource)
        require(HEADER.size <= resource_size <= MAX_PAYLOAD, "Invalid launcher packet resource size")
        loaded = load_resource(module, resource)
        address = lock(loaded) if loaded else None
        if not address:
            raise ctypes.WinError(ctypes.get_last_error())
        with memoryview((ctypes.c_ubyte * resource_size).from_address(address)).cast("B") as data:
            yield data
    finally:
        free(module)


def read_packet(packet):
    magic, version, count, entry_size, reserved, table_size, data_size, digest = HEADER.unpack_from(packet)
    data_start = HEADER.size + table_size
    require(magic == b"LMHOST01" and version == 1 and reserved == 0 and 0 < count <= 4096 and
            0 < entry_size <= min(1024, table_size) and len(packet) == data_start + data_size,
            "Invalid launcher packet header")
    require(hashlib.sha256(packet[HEADER.size:]).digest() == digest, "Launcher packet SHA256 mismatch")
    position = HEADER.size + entry_size
    require(bytes(packet[HEADER.size:position]) == b"LANMinecraft.dll", "Unexpected managed launcher entry point")
    consumed = 0
    paths = {}
    extraction = {}
    for _ in range(count):
        require(position + 4 <= data_start, "Truncated launcher packet table")
        name_size, = struct.unpack_from("<I", packet, position)
        position += 4
        require(0 < name_size <= 1024 and position + name_size + FILE_RECORD.size <= data_start,
                "Invalid launcher packet file record")
        name = bytes(packet[position:position + name_size]).decode("utf-8")
        reserve_path(paths, safe_path(name))
        position += name_size
        file_offset, file_size, file_hash = FILE_RECORD.unpack_from(packet, position)
        position += FILE_RECORD.size
        require(file_offset == consumed and file_size <= data_size - consumed, "Invalid launcher packet file bounds")
        consumed += file_size
        if name in MANAGED_DLLS:
            start = data_start + file_offset
            require(file_size > 0 and hashlib.sha256(packet[start:start + file_size]).digest() == file_hash,
                    f"Launcher packet file SHA256 mismatch: {name}")
            extraction[name] = (start, file_size, file_hash.hex())
    require(position == data_start and consumed == data_size and extraction.keys() == MANAGED_DLLS,
            "Incomplete launcher packet or missing managed DLL")
    return digest.hex(), extraction


def hash_file(path):
    with path.open("rb") as source:
        return hashlib.file_digest(source, "sha256").hexdigest()


def extract(executable, expected_sha256, output):
    no_links(executable)
    no_links(output)
    require(executable.is_file() and re.fullmatch(r"[0-9a-f]{64}", expected_sha256) and
            hash_file(executable) == expected_sha256, "Launcher EXE differs from the approved SHA256")
    require(output.parent.is_dir() and not output.exists(), "Use a new extraction directory")
    with launcher_resource(executable) as packet, TemporaryDirectory(prefix=".launcher-dlls-", dir=output.parent) as work:
        payload_hash, records = read_packet(packet)
        staging = Path(work) / "managed"
        staging.mkdir()
        extracted = []
        for name, (start, size, digest) in sorted(records.items()):
            target = staging / name
            with target.open("xb") as stream:
                stream.write(packet[start:start + size])
            require(target.stat().st_size == size and hash_file(target) == digest, f"Extracted DLL differs: {name}")
            extracted.append({"name": name, "sizeBytes": size, "sha256": digest})
        require(hash_file(executable) == expected_sha256, "Launcher EXE changed during extraction")
        staging.rename(output)
    return {"executableSha256": expected_sha256, "payloadSha256": payload_hash, "extracted": extracted}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--exe", type=Path, required=True)
    parser.add_argument("--sha256", required=True, help="Approved public EXE SHA256")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        receipt = extract(args.exe.absolute(), args.sha256.lower(), args.output.absolute())
        print(json.dumps(receipt, indent=2))
    except (OSError, ValueError, struct.error) as error:
        parser.exit(1, f"{parser.prog}: {error}\n")


if __name__ == "__main__":
    main()
