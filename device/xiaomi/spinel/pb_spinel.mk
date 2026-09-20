$(call inherit-product, vendor/pb/config/common.mk)
$(call inherit-product, device/xiaomi/spinel/device.mk)

PRODUCT_DEVICE := spinel
PRODUCT_NAME := pb_spinel
PRODUCT_BRAND := Redmi
PRODUCT_MODEL := Redmi Note 15 4G
PRODUCT_MANUFACTURER := Xiaomi

PRODUCT_GMS_CLIENTID_BASE := android-xiaomi

PRODUCT_BUILD_PROP_OVERRIDES += \
    PRIVATE_BUILD_DESC="alps/mivendor_mt6789/mgvi_64_armv82:16/BP2A.250605.031.A3/OS3.0.301.0.WPGEUXM:user/release-keys"

BUILD_FINGERPRINT := alps/mivendor_mt6789/mgvi_64_armv82:16/BP2A.250605.031.A3/OS3.0.301.0.WPGEUXM:user/release-keys
