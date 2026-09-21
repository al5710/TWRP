#!/usr/bin/env python3

import struct
import sys

PAGE = 4096
HEADER_SIZE = 2128
ENTRY_SIZE = 108

TYPE_RECOVERY = 2

def align(value, page=PAGE):
    return (value + page - 1) // page * page

def fail(msg):
    print(f"ERROR: {msg}", file=sys.stderr)
    sys.exit(1)

if len(sys.argv) != 4:
    fail("usage: replace_vendor_boot_recovery.py stock_vendor_boot.img pbrp_vendor_boot.img output.img")

stock_path, pbrp_path, output_path = sys.argv[1:]

with open(stock_path, "rb") as f:
    stock = bytearray(f.read())

with open(pbrp_path, "rb") as f:
    pbrp = bytes(f.read())

if len(stock) != 67108864:
    fail(f"stock vendor_boot size is {len(stock)}, expected 67108864")

if stock[:8] != b"VNDRBOOT":
    fail("stock image is not vendor_boot")

header_version = struct.unpack_from("<I", stock, 8)[0]
page_size = struct.unpack_from("<I", stock, 12)[0]
vendor_ramdisk_size = struct.unpack_from("<I", stock, 24)[0]
header_size = struct.unpack_from("<I", stock, 2084)[0]
dtb_size = struct.unpack_from("<I", stock, 2088)[0]

if header_version != 4:
    fail(f"stock vendor_boot header version is {header_version}, expected 4")

if page_size != PAGE:
    fail(f"stock page size is {page_size}, expected {PAGE}")

if header_size != HEADER_SIZE:
    fail(f"stock header size is {header_size}, expected {HEADER_SIZE}")

table_size, entry_count, entry_size, bootconfig_size = struct.unpack_from(
    "<IIII", stock, 2112
)

if entry_size != ENTRY_SIZE:
    fail(f"unexpected table entry size: {entry_size}")

header_end = align(header_size)
ramdisk_start = header_end
dtb_start = ramdisk_start + align(vendor_ramdisk_size)
table_start = dtb_start + align(dtb_size)
bootconfig_start = table_start + align(table_size)

print(f"stock vendor_ramdisk_size = {vendor_ramdisk_size}")
print(f"stock entry_count         = {entry_count}")
print(f"stock table_size          = {table_size}")
print(f"stock bootconfig_size     = {bootconfig_size}")

entries = []

for i in range(entry_count):
    off = table_start + i * entry_size

    ramdisk_size, ramdisk_offset, ramdisk_type = struct.unpack_from(
        "<III", stock, off
    )

    name_raw = stock[off + 12:off + 44]
    name = name_raw.split(b"\0", 1)[0].decode("ascii", errors="replace")

    board_id = bytes(stock[off + 44:off + 108])

    data_start = ramdisk_start + ramdisk_offset
    data_end = data_start + ramdisk_size

    if data_end > len(stock):
        fail(f"fragment {i} exceeds image")

    entries.append({
        "size": ramdisk_size,
        "offset": ramdisk_offset,
        "type": ramdisk_type,
        "name": name,
        "board_id": board_id,
        "data": bytes(stock[data_start:data_end]),
    })

    print(
        f"fragment {i}: "
        f"name='{name}' type={ramdisk_type} "
        f"size={ramdisk_size} offset={ramdisk_offset}"
    )

recovery_index = None

for i, entry in enumerate(entries):
    if entry["type"] == TYPE_RECOVERY or entry["name"] == "recovery":
        if recovery_index is not None:
            fail("multiple recovery fragments found")
        recovery_index = i

if recovery_index is None:
    fail("stock RECOVERY fragment not found")

print(f"stock recovery fragment index = {recovery_index}")

# Extract RECOVERY from the PBRP-built vendor_boot.
if pbrp[:8] != b"VNDRBOOT":
    fail("PBRP image is not vendor_boot")

pbrp_version = struct.unpack_from("<I", pbrp, 8)[0]
pbrp_page = struct.unpack_from("<I", pbrp, 12)[0]
pbrp_ramdisk_size = struct.unpack_from("<I", pbrp, 24)[0]
pbrp_header_size = struct.unpack_from("<I", pbrp, 2084)[0]
pbrp_dtb_size = struct.unpack_from("<I", pbrp, 2088)[0]

if pbrp_version != 4 or pbrp_page != PAGE or pbrp_header_size != HEADER_SIZE:
    fail("PBRP vendor_boot has unexpected v4 header")

pbrp_table_size, pbrp_entry_count, pbrp_entry_size, pbrp_bootconfig_size = struct.unpack_from(
    "<IIII", pbrp, 2112
)

if pbrp_entry_size != ENTRY_SIZE:
    fail("unexpected PBRP table entry size")

pbrp_ramdisk_start = align(pbrp_header_size)
pbrp_dtb_start = pbrp_ramdisk_start + align(pbrp_ramdisk_size)
pbrp_table_start = pbrp_dtb_start + align(pbrp_dtb_size)

pbrp_recovery = None

for i in range(pbrp_entry_count):
    off = pbrp_table_start + i * pbrp_entry_size

    size, offset, rtype = struct.unpack_from("<III", pbrp, off)
    name_raw = pbrp[off + 12:off + 44]
    name = name_raw.split(b"\0", 1)[0].decode("ascii", errors="replace")

    if rtype == TYPE_RECOVERY or name == "recovery":
        if pbrp_recovery is not None:
            fail("multiple PBRP recovery fragments found")

        start = pbrp_ramdisk_start + offset
        end = start + size

        if end > len(pbrp):
            fail("PBRP recovery fragment exceeds image")

        pbrp_recovery = bytes(pbrp[start:end])

if pbrp_recovery is None:
    fail("PBRP RECOVERY fragment not found")

print(f"PBRP recovery size = {len(pbrp_recovery)}")

entries[recovery_index]["data"] = pbrp_recovery
entries[recovery_index]["size"] = len(pbrp_recovery)

# Rebuild vendor ramdisk section.
ramdisk_section = bytearray()
new_offsets = []

for entry in entries:
    new_offsets.append(len(ramdisk_section))
    ramdisk_section.extend(entry["data"])

new_ramdisk_size = len(ramdisk_section)

# Keep the original DTB, table metadata, board IDs and bootconfig.
dtb = stock[dtb_start:dtb_start + dtb_size]
bootconfig = stock[bootconfig_start:bootconfig_start + bootconfig_size]

new_table_size = entry_count * ENTRY_SIZE
new_table = bytearray(new_table_size)

for i, entry in enumerate(entries):
    off = i * ENTRY_SIZE

    struct.pack_into(
        "<III",
        new_table,
        off,
        len(entry["data"]),
        new_offsets[i],
        entry["type"],
    )

    name_bytes = entry["name"].encode("ascii")[:32]
    new_table[off + 12:off + 44] = name_bytes.ljust(32, b"\0")
    new_table[off + 44:off + 108] = entry["board_id"]

# Update vendor_ramdisk_size.
struct.pack_into("<I", stock, 24, new_ramdisk_size)

# Construct the complete v4 image.
output = bytearray()

output.extend(stock[:header_end])
output.extend(ramdisk_section)
output.extend(b"\0" * (align(new_ramdisk_size) - new_ramdisk_size))
output.extend(dtb)
output.extend(b"\0" * (align(dtb_size) - dtb_size))
output.extend(new_table)
output.extend(b"\0" * (align(new_table_size) - new_table_size))
output.extend(bootconfig)
output.extend(b"\0" * (align(bootconfig_size) - bootconfig_size))

# Preserve exact vendor_boot partition size.
if len(output) > len(stock):
    fail(
        f"repacked image is too large: {len(output)} bytes "
        f"(partition is {len(stock)})"
    )

output.extend(b"\0" * (len(stock) - len(output)))

with open(output_path, "wb") as f:
    f.write(output)

print(f"Created: {output_path}")
print(f"Final size: {len(output)}")
print("Final fragments:")

for i, entry in enumerate(entries):
    print(
        f"  {i}: name='{entry['name']}' "
        f"type={entry['type']} "
        f"size={len(entry['data'])} "
        f"offset={new_offsets[i]}"
    )