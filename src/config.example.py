# Copy to config.py. Runtime settings.json takes precedence after first boot.
# Leave credentials blank to use the phone setup hotspot.
WIFI_SSID = ""
WIFI_PASSWORD = ""
BOARD = "esp32"  # "esp32s3" for ESP32-S3-WROOM-1 N16R8
I2C_SCL_PIN = 22  # S3: choose e.g. 9
I2C_SDA_PIN = 21  # S3: choose e.g. 8
ADS1115_ADDRESS = 0x48
I2C_FREQ = 100000
I2C_TIMEOUT_US = 50000
I2C_BUS_RECOVERY = True
VALVES = [{"name": "valve1", "pin": 26, "active_high": True, "flow_meter_pin": None}]
# S3: use a safe valve GPIO such as 10 instead of 26.
ZONES = [{"name": "zone1", "channel": 0, "dry_raw": 17500, "wet_raw": 8000,
          "threshold_percent": 30, "valve": "valve1"}]
DAILY_WATER_HOUR = 6
DAILY_WATER_MINUTE = 0
DAILY_WATER_DURATION_SEC = 300
SUPPLEMENTAL_WATER_DURATION_SEC = 60
MIN_SUPPLEMENTAL_INTERVAL_SEC = 7200
POST_DAILY_LOCKOUT_SEC = 14400
MAX_VALVE_OPEN_SEC = 600
WATCHDOG_TIMEOUT_SEC = 120  # 0 only for supervised REPL development
STARTUP_GRACE_SEC = 60
MOISTURE_CHECK_INTERVAL_SEC = 15
ADS_PROBE_SEC = 60
TZ_OFFSET_SEC = -18000  # Fixed offset; change for daylight saving time.
DAILY_REBOOT_HOUR = None
WIFI_POWER_SAVE = False
WIFI_RESCUE_AFTER_SEC = 300
WIFI_HEALTH_CHECK_SEC = 900
WIFI_HEALTH_TIMEOUT_SEC = 3
NTP_RESYNC_SEC = 3600
NTP_STALE_RECYCLE_SEC = 0  # An ISP failure must not recycle a healthy LAN.
STATUS_LED_PIN = 2  # S3 onboard RGB: 48
STATUS_LED_TYPE = "plain"  # "rgb" or "auto"; see hardware guide.
WEB_DEBUG = False
WEB_SEND_DEBUG = False
UPDATE_CHECK_HOUR = 4
UPDATE_AUTO_INSTALL = False
UPDATE_MANIFEST_PATH = "build/manifest.json"
UPDATE_TIMEOUT_SEC = 15
UPDATE_BASE_URL = ""  # Set to your trusted HTTP mirror of THIS rebuild.
PULSES_PER_LITER = 450  # Configuration groundwork; watering remains timed.
