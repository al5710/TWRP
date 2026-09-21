#!/usr/bin/env python3

import struct
import sys


VNDRBOOT_MAGIC = b"VNDRBOOT"

HEADER_VERSION_OFFSET = 8
PAGE_SIZE_OFFSET = 12
VENDOR_RAMDISK_SIZE_OFFSET = 24

HEADER_SIZE_OFFSET = 2096
DTB_SIZE_OFFSET = 2100
TABLE_SIZE_OFFSET = 2112
TABLE_ENTRIES_OFFSET = 2116
TABLE_ENTRY_SIZE_OFFSET = 2120
BOOTCONFIG_SIZE_OFFSET = 2124

VENDOR_RAMDISK_BASE = None

RAMDISK_TYPE_PLATFORM = 1
RAMDISK_TYPE_RECOVERY = 2

RECOVERY_LZ4_MAGIC = b"\x02\x21\x4c\x18"


def read_u32(data, offset):
    return struct.unpack_from("<I", data, offset)[0]


def write_u32(data, offset, value):
    struct.pack_into("<I", data, offset, value)


def align(value, alignment):
    return (value + alignment - 1) // alignment * alignment


def die(message):
    print(f"ERROR: {message}")
    sys.exit(1)


def main():
    if len(sys.argv) != 4:
        print(
            "Usage:\n"
            f"  {sys.argv[0]} STOCK_VENDOR_BOOT PBRP_RECOVERY OUTPUT_VENDOR_BOOT"
        )
        sys.exit(1)

    stock_path = sys.argv[1]
    recovery_path = sys.argv[2]
    output_path = sys.argv[3]

    with open(stock_path, "rb") as f:
        stock = f.read()

    with open(recovery_path, "rb") as f:
        new_recovery = f.read()

    if stock[:8] != VNDRBOOT_MAGIC:
        die("stock image is not a vendor_boot image")

    header_version = read_u32(stock, HEADER_VERSION_OFFSET)
    page_size = read_u32(stock, PAGE_SIZE_OFFSET)
    vendor_ramdisk_size = read_u32(stock, VENDOR_RAMDISK_SIZE_OFFSET)
    header_size = read_u32(stock, HEADER_SIZE_OFFSET)
    dtb_size = read_u32(stock, DTB_SIZE_OFFSET)
    table_size = read_u32(stock, TABLE_SIZE_OFFSET)
    table_entries = read_u32(stock, TABLE_ENTRIES_OFFSET)
    entry_size = read_u32(stock, TABLE_ENTRY_SIZE_OFFSET)
    bootconfig_size = read_u32(stock, BOOTCONFIG_SIZE_OFFSET)

    print("===== STOCK VENDOR BOOT =====")
    print(f"image size:       {len(stock)}")
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
        die(f"expected vendor_boot header version 4, got {header_version}")

    if page_size != 4096:
        die(f"expected page size 4096, got {page_size}")

    if header_size != 2128:
        die(f"expected header size 2128, got {header_size}")

    if entry_size != 108:
        die(f"expected vendor ramdisk entry size 108, got {entry_size}")

    if table_entries < 2:
        die("expected at least PLATFORM and RECOVERY ramdisk entries")

    ramdisk_base = page_size

    old_dtb_base = align(
        ramdisk_base + vendor_ramdisk_size,
        page_size,
    )

    old_table_base = align(
        old_dtb_base + dtb_size,
        page_size,
    )

    old_bootconfig_base = align(
        old_table_base + table_size,
        page_size,
    )

    if old_bootconfig_base + bootconfig_size > len(stock):
        die("stock vendor_boot layout exceeds image size")

    platform_fragment = None
    recovery_entry_offset = None
    recovery_entry_table_offset = None

    print()
    print("===== STOCK FRAGMENTS =====")

    for i in range(table_entries):
        entry_off = old_table_base + i * entry_size

        if entry_off + entry_size > len(stock):
            die(f"ramdisk table entry {i} exceeds image")

        size = read_u32(stock, entry_off)
        ramdisk_offset = read_u32(stock, entry_off + 4)
        ramdisk_type = read_u32(stock, entry_off + 8)

        name_raw = stock[entry_off + 12:entry_off + 44]
        name = name_raw.split(b"\0", 1)[0].decode(
            "ascii",
            "replace",
        )

        fragment_start = ramdisk_base + ramdisk_offset
        fragment_end = fragment_start + size

        if fragment_end > ramdisk_base + vendor_ramdisk_size:
            die(
                f"stock fragment {i} exceeds vendor ramdisk: "
                f"{fragment_start}..{fragment_end}"
            )

        print(
            f"[{i}] "
            f"type={ramdisk_type} "
            f"name={name!r} "
            f"size={size} "
            f"offset={ramdisk_offset}"
        )

        if ramdisk_type == RAMDISK_TYPE_PLATFORM:
            if platform_fragment is not None:
                die("multiple PLATFORM fragments found")

            if ramdisk_offset != 0:
                die("PLATFORM fragment does not start at offset 0")

            platform_fragment = stock[
                fragment_start:fragment_end
            ]

        elif ramdisk_type == RAMDISK_TYPE_RECOVERY:
            if recovery_entry_offset is not None:
                die("multiple RECOVERY fragments found")

            recovery_entry_offset = ramdisk_offset
            recovery_entry_table_offset = entry_off

    if platform_fragment is None:
        die("stock PLATFORM fragment not found")

    if recovery_entry_table_offset is None:
        die("stock RECOVERY fragment not found")

    if not new_recovery:
        die("PBRP recovery fragment is empty")

    if not new_recovery.startswith(RECOVERY_LZ4_MAGIC):
        die(
            "PBRP recovery fragment does not start with "
            "legacy LZ4 magic 02 21 4c 18"
        )

    new_vendor_ramdisk = platform_fragment + new_recovery
    new_vendor_ramdisk_size = len(new_vendor_ramdisk)

    print()
    print("===== NEW VENDOR RAMDISK =====")
    print(f"PLATFORM size:       {len(platform_fragment)}")
    print(f"PBRP RECOVERY size:  {len(new_recovery)}")
    print(f"TOTAL ramdisk size:  {new_vendor_ramdisk_size}")

    new_dtb_base = align(
        ramdisk_base + new_vendor_ramdisk_size,
        page_size,
    )

    new_table_base = align(
        new_dtb_base + dtb_size,
        page_size,
    )

    new_bootconfig_base = align(
        new_table_base + table_size,
        page_size,
    )

    required_size = align(
        new_bootconfig_base + bootconfig_size,
        page_size,
    )

    print()
    print("===== NEW OFFSETS =====")
    print(f"vendor ramdisk base: {ramdisk_base}")
    print(f"DTB base:            {new_dtb_base}")
    print(f"table base:          {new_table_base}")
    print(f"bootconfig base:     {new_bootconfig_base}")
    print(f"required image size: {required_size}")
    print(f"stock image size:    {len(stock)}")

    if required_size > len(stock):
        die(
            "new vendor_boot layout does not fit inside the "
            "stock image size"
        )

    # Start with a zero-filled image of exactly the stock size.
    output = bytearray(len(stock))

    # Preserve the vendor_boot header exactly, except for the
    # vendor_ramdisk_size field which must describe the new ramdisk.
    output[:page_size] = stock[:page_size]

    write_u32(
        output,
        VENDOR_RAMDISK_SIZE_OFFSET,
        new_vendor_ramdisk_size,
    )

    # Copy the new concatenated vendor ramdisk.
    output[
        ramdisk_base:
        ramdisk_base + new_vendor_ramdisk_size
    ] = new_vendor_ramdisk

    # Copy DTB unchanged.
    old_dtb_end = old_dtb_base + dtb_size
    output[
        new_dtb_base:
        new_dtb_base + dtb_size
    ] = stock[old_dtb_base:old_dtb_end]

    # Copy ramdisk table unchanged first.
    output[
        new_table_base:
        new_table_base + table_size
    ] = stock[
        old_table_base:
        old_table_base + table_size
    ]

    # The RECOVERY fragment now starts immediately after PLATFORM.
    new_recovery_offset = len(platform_fragment)

    new_recovery_entry_table_offset = (
        new_table_base
        + (
            (
                recovery_entry_table_offset
                - old_table_base
            )
            // entry_size
        )
        * entry_size
    )

    write_u32(
        output,
        new_recovery_entry_table_offset,
        len(new_recovery),
    )

    write_u32(
        output,
        new_recovery_entry_table_offset + 4,
        new_recovery_offset,
    )

    # Copy bootconfig unchanged.
    output[
        new_bootconfig_base:
        new_bootconfig_base + bootconfig_size
    ] = stock[
        old_bootconfig_base:
        old_bootconfig_base + bootconfig_size
    ]

    with open(output_path, "wb") as f:
        f.write(output)

    print()
    print("===== RESULT =====")
    print(f"output:              {output_path}")
    print(f"output size:         {len(output)}")
    print(f"PLATFORM size:       {len(platform_fragment)}")
    print(f"RECOVERY size:       {len(new_recovery)}")
    print(f"RECOVERY offset:     {new_recovery_offset}")
    print(f"vendor ramdisk:      {new_vendor_ramdisk_size}")

    if len(output) != len(stock):
        die("output size changed unexpectedly")

    print()
    print("===== DONE =====")


if __name__ == "__main__":
    main()