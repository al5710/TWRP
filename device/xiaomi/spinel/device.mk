LOCAL_PATH := device/xiaomi/spinel

PRODUCT_COPY_FILES += \
    $(LOCAL_PATH)/recovery/root/init.recovery.mt6789.rc:root/init.recovery.mt6789.rc

PRODUCT_COPY_FILES += \
    $(LOCAL_PATH)/recovery/root/system/etc/recovery.fstab:root/system/etc/recovery.fstab
