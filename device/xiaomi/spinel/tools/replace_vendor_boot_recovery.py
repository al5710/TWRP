#!/usr/bin/env python3

import struct
import sys


PAGE_SIZE = 4096
VENDOR_BOOT_SIZE = 64 * 1024 * 1024

VENDOR_BOOT_MAGIC = b"VNDRBOOT"
VENDOR_BOOT_HEADER_SIZE = 2128

RAMDISK_TYPE_PLATFORM = 1
RAMDISK_TYPE_RECOVERY = 2

TABLE_ENTRY_SIZE = 108


def align(value, alignment):
    return (value + alignment - 1) // alignment * alignment


def fail(message):
    print(f"ERROR: {message}", file=sys.stderr)
    sys.exit(1)


def read_u32(data, offset):
    return struct.unpack_from("<I", data, offset)[0]


def read_vendor_boot(path):
    with open(path, "rb") as f:
        data = f.read()

    if len(data) < VENDOR_BOOT_HEADER_SIZE:
        fail(f"{path}: file is too small")

    if data[0:8] != VENDOR_BOOT_MAGIC:
        fail(f"{path}: invalid vendor_boot magic")

    # vendor_boot_img_hdr_v3/v4
    header_version = read_u32(data, 8)
    page_size = read_u32(data, 12)

    # Correct offsets from AOSP vendor_boot_img_hdr_v4:
    # 2096 = header_size
    # 2100 = dtb_size
    # 2112 = vendor_ramdisk_table_size
    # 2116 = vendor_ramdisk_table_entry_num
    # 2120 = vendor_ramdisk_table_entry_size
    # 2124 = bootconfig_size
    header_size = read_u32(data, 2096)
    dtb_size = read_u32(data, 2100)

    vendor_ramdisk_size = read_u32(data, 24)

    table_size = read_u32(data, 2112)
    table_entry_num = read_u32(data, 2116)
    table_entry_size = read_u32(data, 2120)
    bootconfig_size = read_u32(data, 2124)

    if header_version != 4:
        fail(
            f"{path}: header version is {header_version}, "
            "expected 4"
        )

    if page_size != PAGE_SIZE:
        fail(
            f"{path}: page size is {page_size}, "
            f"expected {PAGE_SIZE}"
        )

    if header_size != VENDOR_BOOT_HEADER_SIZE:
        fail(
            f"{path}: header size is {header_size}, "
            f"expected {VENDOR_BOOT_HEADER_SIZE}"
        )

    if table_entry_size != TABLE_ENTRY_SIZE:
        fail(
            f"{path}: table entry size is {table_entry_size}, "
            f"expected {TABLE_ENTRY_SIZE}"
        )

    expected_table_size = table_entry_num * table_entry_size

    if table_size != expected_table_size:
        fail(
            f"{path}: invalid table size: "
            f"{table_size} != "
            f"{table_entry_num} * {table_entry_size}"
        )

    # Header occupies one 4096-byte page in the image.
    ramdisk_start = align(header_size, page_size)
    ramdisk_end = ramdisk_start + vendor_ramdisk_size

    dtb_start = align(ramdisk_end, page_size)
    dtb_end = dtb_start + dtb_size

    table_start = align(dtb_end, page_size)
    table_end = table_start + table_size

    bootconfig_start = align(table_end, page_size)
    bootconfig_end = bootconfig_start + bootconfig_size

    if bootconfig_end > len(data):
        fail(
            f"{path}: bootconfig extends beyond image"
        )

    dtb = data[dtb_start:dtb_end]

    table = data[table_start:table_end]

    bootconfig = data[
        bootconfig_start:bootconfig_end
    ]

    fragments = []

    for index in range(table_entry_num):
        entry_offset = (
            table_start +
            index * table_entry_size
        )

        fragment_size = read_u32(
            data,
            entry_offset,
        )

        fragment_offset = read_u32(
            data,
            entry_offset + 4,
        )

        fragment_type = read_u32(
            data,
            entry_offset + 8,
        )

        name_raw = data[
            entry_offset + 12:
            entry_offset + 44
        ]

        name = name_raw.split(
            b"\0",
            1,
        )[0].decode(
            "ascii",
            errors="replace",
        )

        board_id = data[
            entry_offset + 44:
            entry_offset + 108
        ]

        fragment_start = (
            ramdisk_start +
            fragment_offset
        )

        fragment_end = (
            fragment_start +
            fragment_size
        )

        if fragment_end > ramdisk_end:
            fail(
                f"{path}: fragment {index} "
                "extends outside vendor ramdisk"
            )

        fragment_data = data[
            fragment_start:
            fragment_end
        ]

        fragments.append(
            {
                "size": fragment_size,
                "offset": fragment_offset,
                "type": fragment_type,
                "name": name,
                "board_id": board_id,
                "data": fragment_data,
            }
        )

    return {
        "data": data,
        "header": data[:header_size],
        "header_version": header_version,
        "page_size": page_size,
        "header_size": header_size,
        "vendor_ramdisk_size": vendor_ramdisk_size,
        "dtb": dtb,
        "table_size": table_size,
        "table_entry_num": table_entry_num,
        "table_entry_size": table_entry_size,
        "bootconfig": bootconfig,
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
    new_fragments = []

    replaced = False

    for fragment in stock["fragments"]:
        is_recovery = (
            fragment["type"] == RAMDISK_TYPE_RECOVERY
            or fragment["name"] == "recovery"
        )

        if is_recovery:
            new_fragments.append(
                {
                    "type": fragment["type"],
                    "name": fragment["name"],
                    "board_id": fragment["board_id"],
                    "data": new_recovery,
                }
            )

            replaced = True
        else:
            new_fragments.append(
                {
                    "type": fragment["type"],
                    "name": fragment["name"],
                    "board_id": fragment["board_id"],
                    "data": fragment["data"],
                }
            )

    if not replaced:
        fail(
            "stock vendor_boot has no RECOVERY fragment"
        )

    ramdisk = bytearray()
    table_entries = []

    for fragment in new_fragments:
        fragment_offset = len(ramdisk)
        fragment_data = fragment["data"]

        ramdisk.extend(fragment_data)

        entry = bytearray(TABLE_ENTRY_SIZE)

        struct.pack_into(
            "<I",
            entry,
            0,
            len(fragment_data),
        )

        struct.pack_into(
            "<I",
            entry,
            4,
            fragment_offset,
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

        entry[
            12:
            12 + len(name)
        ] = name

        entry[44:108] = fragment["board_id"]

        table_entries.append(entry)

    table = b"".join(table_entries)

    if len(table) != stock["table_size"]:
        fail(
            "new table size differs from stock table size"
        )

    new_ramdisk_size = len(ramdisk)

    header = bytearray(stock["header"])

    # vendor_ramdisk_size is at offset 24.
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

    ramdisk_end = (
        ramdisk_start +
        new_ramdisk_size
    )

    dtb_start = align(
        ramdisk_end,
        stock["page_size"],
    )

    dtb_end = (
        dtb_start +
        len(stock["dtb"])
    )

    table_start = align(
        dtb_end,
        stock["page_size"],
    )

    table_end = (
        table_start +
        len(table)
    )

    bootconfig_start = align(
        table_end,
        stock["page_size"],
    )

    bootconfig_end = (
        bootconfig_start +
        len(stock["bootconfig"])
    )

    if bootconfig_end > VENDOR_BOOT_SIZE:
        fail(
            "new vendor_boot contents exceed 64 MiB"
        )

    output = bytearray(VENDOR_BOOT_SIZE)

    output[
        0:
        len(header)
    ] = header

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
        bootconfig_end
    ] = stock["bootconfig"]

    return bytes(output)


def print_info(title, info):
    print()
    print(f"===== {title} =====")
    print(f"header version: {info['header_version']}")
    print(f"page size: {info['page_size']}")
    print(f"header size: {info['header_size']}")
    print(
        f"vendor ramdisk size: "
        f"{info['vendor_ramdisk_size']}"
    )
    print(f"DTB size: {len(info['dtb'])}")
    print(f"table size: {info['table_size']}")
    print(
        f"table entries: "
        f"{info['table_entry_num']}"
    )
    print(
        f"bootconfig size: "
        f"{len(info['bootconfig'])}"
    )

    print("fragments:")

    for index, fragment in enumerate(
        info["fragments"]
    ):
        print(
            f"  [{index}] "
            f"type={fragment['type']} "
            f"name={fragment['name']!r} "
            f"size={len(fragment['data'])} "
            f"offset={fragment['offset']}"
        )


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

    print_info("STOCK", stock)
    print_info("PBRP", pbrp)

    stock_recovery = find_recovery(
        stock,
        stock_path,
    )

    pbrp_recovery = find_recovery(
        pbrp,
        pbrp_path,
    )

    print()
    print("===== REPLACEMENT =====")
    print(
        f"stock RECOVERY size: "
        f"{len(stock_recovery['data'])}"
    )
    print(
        f"PBRP RECOVERY size: "
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
    print(f"path: {output_path}")
    print(f"size: {len(result)}")
    print("SUCCESS")


if __name__ == "__main__":
    main()