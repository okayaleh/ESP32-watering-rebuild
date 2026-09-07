"""Apply the small, explicit Planter platform patch to pinned MicroPython 1.28."""
from pathlib import Path
import os

ROOT = Path(os.environ.get("PLANTER_FIRMWARE_BUILD_ROOT", Path(__file__).resolve().parents[2] / ".tools/firmware-build"))
MP = ROOT / "micropython"
PORT = MP / "ports/esp32"
BOARD = ROOT / "board"
BOARD.mkdir(exist_ok=True)

def replace(path, before, after):
    text = path.read_text()
    if after in text:
        return
    if text.count(before) != 1:
        raise ValueError("Upstream patch context changed: " + str(path))
    path.write_text(text.replace(before, after), newline="\n")

replace(PORT / "gccollect.c", """size_t gc_get_max_new_split(void) {
    return heap_caps_get_largest_free_block(MALLOC_CAP_DEFAULT);
}""", """size_t gc_get_max_new_split(void) {
    // Planter: Python heap growth must leave a contiguous native allocation
    // margin for WiFi/lwIP. Otherwise stock split-heap doubling takes the
    // entire last native block and socket creation fails with ENOBUFS.
    const size_t native_reserve = 32 * 1024 + 64;
    size_t largest = heap_caps_get_largest_free_block(MALLOC_CAP_DEFAULT);
    return largest > native_reserve ? largest - native_reserve : 0;
}""")

replace(PORT / "esp32_partition.c", """static esp32_partition_obj_t esp32_partition_romfs_obj = {
    .base = { .type = NULL },
    .part = NULL,
    .cache = NULL,
    .block_size = NATIVE_BLOCK_SIZE_BYTES,
};

static const void *esp32_partition_romfs_ptr = NULL;
static esp_partition_mmap_handle_t esp32_partition_romfs_handle;""", """// Planter: two independent mapped banks preserve the previous complete ROMFS
// while the immutable boot helper rebuilds the other after a filesystem OTA.
#define PLANTER_ROMFS_BANKS (2)
static const char *const esp32_partition_romfs_labels[PLANTER_ROMFS_BANKS] = { "romfs", "romfs_b" };
static esp32_partition_obj_t esp32_partition_romfs_obj[PLANTER_ROMFS_BANKS];
static const void *esp32_partition_romfs_ptr[PLANTER_ROMFS_BANKS];
static esp_partition_mmap_handle_t esp32_partition_romfs_handle[PLANTER_ROMFS_BANKS];""")

replace(PORT / "esp32_partition.c", """    if (self == &esp32_partition_romfs_obj && flags == MP_BUFFER_READ) {
        if (esp32_partition_romfs_ptr == NULL) {
            check_esp_err(esp_partition_mmap(self->part, 0, self->part->size, ESP_PARTITION_MMAP_DATA, &esp32_partition_romfs_ptr, &esp32_partition_romfs_handle));
        }
        bufinfo->buf = (void *)esp32_partition_romfs_ptr;
        bufinfo->len = self->part->size;
        bufinfo->typecode = 'B';
        return 0;
    } else {
        // Unsupported.
        return 1;
    }""", """    if (flags == MP_BUFFER_READ) {
        for (size_t bank = 0; bank < PLANTER_ROMFS_BANKS; ++bank) {
            if (self == &esp32_partition_romfs_obj[bank] && self->part != NULL) {
                if (esp32_partition_romfs_ptr[bank] == NULL) {
                    check_esp_err(esp_partition_mmap(self->part, 0, self->part->size, ESP_PARTITION_MMAP_DATA, &esp32_partition_romfs_ptr[bank], &esp32_partition_romfs_handle[bank]));
                }
                bufinfo->buf = (void *)esp32_partition_romfs_ptr[bank];
                bufinfo->len = self->part->size;
                bufinfo->typecode = 'B';
                return 0;
            }
        }
    }
    return 1; // Ordinary Partition objects are not implicitly mapped.""")

replace(PORT / "esp32_partition.c", """    if (esp32_partition_romfs_obj.base.type == NULL) {
        esp32_partition_romfs_obj.base.type = &esp32_partition_type;
        // Get the romfs partition.
        // TODO: number of segments ioctl can be used if there is more than one romfs.
        esp_partition_iterator_t iter = esp_partition_find(ESP_PARTITION_TYPE_DATA, ESP_PARTITION_SUBTYPE_ANY, "romfs");
        if (iter != NULL) {
            esp32_partition_romfs_obj.part = esp_partition_get(iter);
        }
        esp_partition_iterator_release(iter);
    }

    switch (mp_obj_get_int(args[0])) {
        case MP_VFS_ROM_IOCTL_GET_NUMBER_OF_SEGMENTS:
            if (esp32_partition_romfs_obj.part == NULL) {
                return MP_OBJ_NEW_SMALL_INT(0);
            } else {
                return MP_OBJ_NEW_SMALL_INT(1);
            }
        case MP_VFS_ROM_IOCTL_GET_SEGMENT:
            if (esp32_partition_romfs_obj.part == NULL) {
                return MP_OBJ_NEW_SMALL_INT(-MP_EINVAL);
            } else {
                return MP_OBJ_FROM_PTR(&esp32_partition_romfs_obj);
            }
        default:
            return MP_OBJ_NEW_SMALL_INT(-MP_EINVAL);
    }""", """    size_t count = 0;
    for (size_t bank = 0; bank < PLANTER_ROMFS_BANKS; ++bank) {
        esp32_partition_obj_t *rom = &esp32_partition_romfs_obj[bank];
        if (rom->base.type == NULL) {
            rom->base.type = &esp32_partition_type;
            rom->block_size = NATIVE_BLOCK_SIZE_BYTES;
            rom->part = esp_partition_find_first(ESP_PARTITION_TYPE_DATA, ESP_PARTITION_SUBTYPE_ANY, esp32_partition_romfs_labels[bank]);
        }
        if (rom->part == NULL) {
            break;
        }
        ++count;
    }
    switch (mp_obj_get_int(args[0])) {
        case MP_VFS_ROM_IOCTL_GET_NUMBER_OF_SEGMENTS:
            return MP_OBJ_NEW_SMALL_INT(count);
        case MP_VFS_ROM_IOCTL_GET_SEGMENT: {
            mp_int_t bank = n_args >= 2 ? mp_obj_get_int(args[1]) : -1;
            if (bank < 0 || (size_t)bank >= count) {
                return MP_OBJ_NEW_SMALL_INT(-MP_EINVAL);
            }
            return MP_OBJ_FROM_PTR(&esp32_partition_romfs_obj[bank]);
        }
        default:
            return MP_OBJ_NEW_SMALL_INT(-MP_EINVAL);
    }""")

(BOARD / "mpconfigboard.h").write_text('''#define MICROPY_HW_BOARD_NAME "Planter ESP32 ROMFS"
#define MICROPY_HW_MCU_NAME "ESP32"
#define MICROPY_VFS_ROM (1)
#define MICROPY_VFS_ROM_IOCTL (1)
#define MICROPY_PY_BLUETOOTH (0)
#define MICROPY_PY_NETWORK_LAN (0)
''')
(BOARD / "mpconfigboard.cmake").write_text('''set(IDF_TARGET esp32)
set(SDKCONFIG_DEFAULTS boards/sdkconfig.base "${MICROPY_BOARD_DIR}/sdkconfig.planter")
''')
(BOARD / "sdkconfig.planter").write_text('''CONFIG_BT_ENABLED=n
CONFIG_ETH_ENABLED=n
CONFIG_PARTITION_TABLE_CUSTOM=y
CONFIG_PARTITION_TABLE_CUSTOM_FILENAME="%s"
CONFIG_ESPTOOLPY_FLASHSIZE_4MB=y
''' % (BOARD / "partitions.csv").as_posix())
(BOARD / "partitions.csv").write_text('''# Name, Type, SubType, Offset, Size, Flags
nvs, data, nvs, 0x9000, 0x6000,
phy_init, data, phy, 0xf000, 0x1000,
factory, app, factory, 0x10000, 0x1f0000,
romfs, data, 0x40, 0x200000, 0x40000,
romfs_b, data, 0x40, 0x240000, 0x40000,
vfs, data, fat, 0x280000, 0x180000,
''')
print("Prepared dual ROMFS banks and native heap reserve")
