#!/usr/bin/env python3

import struct
import sys


def align(value, alignment):
    return (value + alignment - 1) // alignment * alignment


def read_u32(data, offset):
    return struct.unpack_from("<I", data, offset)[0]


def main():
    if len(sys.argv) != 2:
        print(f"Usage: {sys.argv[0]} vendor_boot.img")
        sys.exit(1)

    path = sys.argv[1]

    with open(path, "rb") as f:
        data = f.read()

    if data[:8] != b"VNDRBOOT":
        print("ERROR: not a vendor_boot image")
        sys.exit(1)

    # Android vendor_boot v4 header offsets.
    header_version = read_u32(data, 8)
    page_size = read_u32(data, 12)
    vendor_ramdisk_size = read_u32(data, 24)

    header_size = read_u32(data, 2096)
    dtb_size = read_u32(data, 2100)

    table_size = read_u32(data, 2112)
    table_entries = read_u32(data, 2116)
    entry_size = read_u32(data, 2120)

    bootconfig_size = read_u32(data, 2124)

    print("===== VENDOR BOOT =====")
    print(f"header version:   {header_version}")
    print(f"page size:        {page_size}")
    print(f"header size:      {header_size}")
    print(f"vendor ramdisk:   {vendor_ramdisk_size}")
    print(f"DTB size:         {dtb_size}")
    print(f"table size:       {table_size}")
    print(f"table entries:    {table_entries}")
    print(f"entry size:       {entry_size}")
    print(f"bootconfig size:  {bootconfig_size}")

    if header_version != 4:
        print()
        print("WARNING: expected vendor_boot header version 4")

    if page_size == 0:
        print()
        print("ERROR: invalid page size")
        sys.exit(1)

    ramdisk_base = page_size

    dtb_base = align(
        ramdisk_base + vendor_ramdisk_size,
        page_size
    )

    table_base = align(
        dtb_base + dtb_size,
        page_size
    )

    print()
    print("===== OFFSETS =====")
    print(f"vendor ramdisk base: {ramdisk_base}")
    print(f"DTB base:            {dtb_base}")
    print(f"table base:          {table_base}")

    print()
    print("===== FRAGMENTS =====")

    recovery_found = False

    for i in range(table_entries):
        off = table_base + i * entry_size

        if off + entry_size > len(data):
            print(f"[{i}] ERROR: table entry exceeds image size")
            sys.exit(1)

        size = read_u32(data, off)
        ramdisk_offset = read_u32(data, off + 4)
        ramdisk_type = read_u32(data, off + 8)

        name_raw = data[off + 12:off + 44]
        name = name_raw.split(b"\0", 1)[0].decode(
            "ascii",
            "replace"
        )

        print(
            f"[{i}] "
            f"type={ramdisk_type} "
            f"name={name!r} "
            f"size={size} "
            f"offset={ramdisk_offset}"
        )

        fragment_start = ramdisk_base + ramdisk_offset
        fragment_end = fragment_start + size

        if fragment_end > ramdisk_base + vendor_ramdisk_size:
            print(
                f"    ERROR: fragment exceeds vendor ramdisk "
                f"({fragment_start}..{fragment_end})"
            )
            sys.exit(1)

        if ramdisk_type == 2:
            recovery_found = True

            fragment = data[fragment_start:fragment_end]

            print()
            print("===== RECOVERY FRAGMENT =====")
            print(f"offset: {fragment_start}")
            print(f"size:   {len(fragment)}")
            print(f"magic:  {fragment[:4].hex(' ')}")

            output_path = "pbrp_recovery_fragment.bin"

            with open(output_path, "wb") as f:
                f.write(fragment)

            print(f"saved:  {output_path}")

            # Basic compression/magic detection.
            if fragment.startswith(b"\x02\x21\x4c\x18"):
                print("format: LZ4 legacy")
            elif fragment.startswith(b"\x1f\x8b"):
                print("format: gzip")
            elif fragment.startswith(b"\x28\xb5\x2f\xfd"):
                print("format: Zstandard")
            elif fragment.startswith(b"\x04\x22\x4d\x18"):
                print("format: LZ4 frame")
            else:
                print("format: unknown")

    if not recovery_found:
        print()
        print("ERROR: no RECOVERY fragment found")
        sys.exit(1)

    print()
    print(f"image size: {len(data)}")

    print()
    print("===== IMAGE SIZE CHECK =====")

    if len(data) == 67108864:
        print("64 MiB vendor_boot: OK")
    else:
        print(
            f"WARNING: expected 67108864 bytes, "
            f"got {len(data)} bytes"
        )

    print()
    print("===== DONE =====")


if __name__ == "__main__":
    main()