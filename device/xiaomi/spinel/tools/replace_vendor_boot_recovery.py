#!/usr/bin/env python3

import os
import struct
import sys


PAGE_SIZE = 4096
VENDOR_BOOT_SIZE = 64 * 1024 * 1024

VENDOR_BOOT_MAGIC = b"VNDRBOOT"
VENDOR_BOOT_HEADER_V4_SIZE = 2128

RAMDISK_TYPE_NONE = 0
RAMDISK_TYPE_PLATFORM = 1
RAMDISK_TYPE_RECOVERY = 2
RAMDISK_TYPE_DLKM = 3

TABLE_ENTRY_SIZE = 108


def align(value, alignment):
    return (value + alignment - 1) // alignment * alignment


def fail(message):
    print(f"ERROR: {message}", file=sys.stderr)
    sys.exit(1)


def read_vendor_boot(path):
    with open(path, "rb") as f:
        data = f.read()

    if len(data) < VENDOR_BOOT_HEADER_V4_SIZE:
        fail(f"{path}: file too small")

    if data[0:8] != VENDOR_BOOT_MAGIC:
        fail(f"{path}: invalid vendor_boot magic")

    header_version = struct.unpack_from("<I", data, 8)[0]
    if header_version != 4:
        fail(
            f"{path}: header version is {header_version}, "
            "expected 4"
        )

    page_size = struct.unpack_from("<I", data, 12)[0]
    header_size = struct.unpack_from("<I", data, 20)[0]

    if page_size != PAGE_SIZE:
        fail(
            f"{path}: page size is {page_size}, "
            f"expected {PAGE_SIZE}"
        )

    if header_size != VENDOR_BOOT_HEADER_V4_SIZE:
        fail(
            f"{path}: header size is {header_size}, "
            f"expected {VENDOR_BOOT_HEADER_V4_SIZE}"
        )

    vendor_ramdisk_size = struct.unpack_from("<I", data, 24)[0]
    dtb_size = struct.unpack_from("<I", data, 2056)[0]

    table_size = struct.unpack_from("<I", data, 2060)[0]
    table_entry_num = struct.unpack_from("<I", data, 2064)[0]
    table_entry_size = struct.unpack_from("<I", data, 2068)[0]

    if table_entry_size != TABLE_ENTRY_SIZE:
        fail(
            f"{path}: table entry size is {table_entry_size}, "
            f"expected {TABLE_ENTRY_SIZE}"
        )

    header_end = header_size

    ramdisk_start = align(header_end, page_size)
    ramdisk_end = ramdisk_start + vendor_ramdisk_size

    dtb_start = align(ramdisk_end, page_size)
    dtb_end = dtb_start + dtb_size

    table_start = align(dtb_end, page_size)
    table_end = table_start + table_size

    bootconfig_start = align(table_end, page_size)

    if bootconfig_start + 4 > len(data):
        fail(f"{path}: bootconfig area is outside image")

    bootconfig_size = struct.unpack_from(
        "<I",
        data,
        bootconfig_start,
    )[0]

    bootconfig_end = bootconfig_start + 4 + bootconfig_size

    if bootconfig_end > len(data):
        fail(f"{path}: bootconfig extends beyond image")

    if table_size != table_entry_num * table_entry_size:
        fail(
            f"{path}: invalid table size: "
            f"{table_size} != "
            f"{table_entry_num} * {table_entry_size}"
        )

    fragments = []

    for index in range(table_entry_num):
        entry_offset = table_start + index * table_entry_size

        ramdisk_size = struct.unpack_from(
            "<I",
            data,
            entry_offset,
        )[0]

        ramdisk_offset = struct.unpack_from(
            "<I",
            data,
            entry_offset + 4,
        )[0]

        ramdisk_type = struct.unpack_from(
            "<I",
            data,
            entry_offset + 8,
        )[0]

        name_raw = data[
            entry_offset + 12:
            entry_offset + 44
        ]

        name = name_raw.split(b"\0", 1)[0].decode(
            "ascii",
            errors="replace",
        )

        board_id = data[
            entry_offset + 44:
            entry_offset + 108
        ]

        fragment_start = ramdisk_start + ramdisk_offset
        fragment_end = fragment_start + ramdisk_size

        if fragment_end > len(data):
            fail(
                f"{path}: fragment {index} "
                "extends beyond image"
            )

        fragment_data = data[
            fragment_start:
            fragment_end
        ]

        fragments.append(
            {
                "size": ramdisk_size,
                "offset": ramdisk_offset,
                "type": ramdisk_type,
                "name": name,
                "board_id": board_id,
                "data": fragment_data,
            }
        )

    return {
        "data": data,
        "page_size": page_size,
        "header_size": header_size,
        "vendor_ramdisk_size": vendor_ramdisk_size,
        "dtb_size": dtb_size,
        "dtb": data[dtb_start:dtb_end],
        "table_size": table_size,
        "table_entry_num": table_entry_num,
        "table_entry_size": table_entry_size,
        "bootconfig": data[
            bootconfig_start:
            bootconfig_end
        ],
        "fragments": fragments,
    }


def find_recovery(info, path):
    matches = []

    for fragment in info["fragments"]:
        if (
            fragment["type"] == RAMDISK_TYPE_RECOVERY
            or fragment["name"] == "recovery"
        ):
            matches.append(fragment)

    if len(matches) != 1:
        fail(
            f"{path}: expected exactly one RECOVERY "
            f"fragment, found {len(matches)}"
        )

    return matches[0]


def build_vendor_boot(stock, new_recovery):
    fragments = []

    replaced = False

    for fragment in stock["fragments"]:
        if (
            fragment["type"] == RAMDISK_TYPE_RECOVERY
            or fragment["name"] == "recovery"
        ):
            fragments.append(
                {
                    "size": len(new_recovery),
                    "type": fragment["type"],
                    "name": fragment["name"],
                    "board_id": fragment["board_id"],
                    "data": new_recovery,
                }
            )

            replaced = True
        else:
            fragments.append(
                {
                    "size": len(fragment["data"]),
                    "type": fragment["type"],
                    "name": fragment["name"],
                    "board_id": fragment["board_id"],
                    "data": fragment["data"],
                }
            )

    if not replaced:
        fail("stock vendor_boot has no RECOVERY fragment")

    ramdisk = bytearray()

    table_entries = []

    for fragment in fragments:
        offset = len(ramdisk)

        ramdisk.extend(fragment["data"])

        entry = bytearray(TABLE_ENTRY_SIZE)

        struct.pack_into(
            "<I",
            entry,
            0,
            len(fragment["data"]),
        )

        struct.pack_into(
            "<I",
            entry,
            4,
            offset,
        )

        struct.pack_into(
            "<I",
            entry,
            8,
            fragment["type"],
        )

        name = fragment["name"].encode(
            "ascii",
            errors="ignore",
        )[:32]

        entry[12:12 + len(name)] = name

        board_id = fragment["board_id"]

        if len(board_id) != 64:
            fail("invalid board_id size")

        entry[44:108] = board_id

        table_entries.append(entry)

    new_ramdisk_size = len(ramdisk)

    table = b"".join(table_entries)

    header = bytearray(
        stock["data"][:stock["header_size"]]
    )

    struct.pack_into(
        "<I",
        header,
        24,
        new_ramdisk_size,
    )

    ramdisk_start = align(
        len(header),
        stock["page_size"],
    )

    ramdisk_end = ramdisk_start + new_ramdisk_size

    dtb_start = align(
        ramdisk_end,
        stock["page_size"],
    )

    dtb_end = dtb_start + len(stock["dtb"])

    table_start = align(
        dtb_end,
        stock["page_size"],
    )

    table_end = table_start + len(table)

    bootconfig_start = align(
        table_end,
        stock["page_size"],
    )

    output = bytearray(VENDOR_BOOT_SIZE)

    output[0:len(header)] = header

    output[
        ramdisk_start:
        ramdisk_end
    ] = ramdisk

    output[
        dtb_start:
        dtb_end
    ] = stock["dtb"]

    output[
        table_start:
        table_end
    ] = table

    output[
        bootconfig_start:
        bootconfig_start + len(stock["bootconfig"])
    ] = stock["bootconfig"]

    return bytes(output)


def main():
    if len(sys.argv) != 4:
        print(
            "Usage:\n"
            "  replace_vendor_boot_recovery.py "
            "<stock_vendor_boot> "
            "<pbrp_vendor_boot> "
            "<output_vendor_boot>"
        )
        sys.exit(2)

    stock_path = sys.argv[1]
    pbrp_path = sys.argv[2]
    output_path = sys.argv[3]

    print("Reading stock vendor_boot...")
    stock = read_vendor_boot(stock_path)

    print("Reading PBRP vendor_boot...")
    pbrp = read_vendor_boot(pbrp_path)

    stock_recovery = find_recovery(
        stock,
        stock_path,
    )

    pbrp_recovery = find_recovery(
        pbrp,
        pbrp_path,
    )

    print()
    print("===== STOCK =====")
    print(
        f"vendor ramdisk size: "
        f"{stock['vendor_ramdisk_size']}"
    )
    print(
        f"fragments: "
        f"{len(stock['fragments'])}"
    )

    for i, fragment in enumerate(stock["fragments"]):
        print(
            f"  [{i}] "
            f"type={fragment['type']} "
            f"name={fragment['name']!r} "
            f"size={len(fragment['data'])}"
        )

    print()
    print("===== PBRP =====")
    print(
        f"vendor ramdisk size: "
        f"{pbrp['vendor_ramdisk_size']}"
    )
    print(
        f"fragments: "
        f"{len(pbrp['fragments'])}"
    )

    for i, fragment in enumerate(pbrp["fragments"]):
        print(
            f"  [{i}] "
            f"type={fragment['type']} "
            f"name={fragment['name']!r} "
            f"size={len(fragment['data'])}"
        )

    print()
    print(
        "Replacing stock RECOVERY fragment:"
    )
    print(
        f"  old size: "
        f"{len(stock_recovery['data'])}"
    )
    print(
        f"  new size: "
        f"{len(pbrp_recovery['data'])}"
    )

    result = build_vendor_boot(
        stock,
        pbrp_recovery["data"],
    )

    if len(result) != VENDOR_BOOT_SIZE:
        fail(
            f"output size is {len(result)}, "
            f"expected {VENDOR_BOOT_SIZE}"
        )

    with open(output_path, "wb") as f:
        f.write(result)

    print()
    print("===== OUTPUT =====")
    print(f"output: {output_path}")
    print(f"size: {len(result)}")
    print("SUCCESS")


if __name__ == "__main__":
    main()