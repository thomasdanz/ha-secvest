"""Constants for the ABUS Secvest integration."""

DOMAIN = "secvest"
MANUFACTURER = "ABUS"
# the API reports no model, serial number or firmware; the product family is
# certain, since only a Secvest speaks this API
PANEL_MODEL = "Secvest"

CONF_USER_CODE = "user_code"
CONF_USER_AGENT = "user_agent"
CONF_ADVANCED = "advanced"
# set after a 401; nothing is sent with the stored credentials until
# a reauthentication succeeds (#6, #41)
CONF_AUTH_FAILED = "auth_failed"
# the pinned SHA-256 fingerprint of a self-signed certificate, and the flag an
# untrusted certificate sets (#149)
CONF_CERT_FINGERPRINT = "cert_fingerprint"
CONF_CERTIFICATE_CHANGED = "certificate_changed"
# the installation's name as last read on request in the options; names the
# panel device (missing: the entry's title, from setup) (#137)
CONF_INSTALLATION_NAME = "installation_name"
CONF_PARTITIONS = "partitions"
CONF_SCAN_INTERVAL = "scan_interval"
CONF_LOG_INTERVAL = "log_interval"
# device class per zone id and zones without entities; set in the options
CONF_ZONE_DEVICE_CLASSES = "zone_device_classes"
CONF_EXCLUDED_ZONES = "excluded_zones"

# zone groups: config subentries, a Home Assistant concept (#67)
SUBENTRY_ZONE_GROUP = "zone_group"
# codes for arming and disarming: in the options (#141); subentries of this
# type up to 0.3, moved by the migration to 1.3
CONF_CODES = "codes"
SUBENTRY_CODE = "code"
CONF_ZONES = "zones"
CONF_HIDE_MEMBERS = "hide_members"
# area of a new group device; the device page manages it afterwards
CONF_AREA_ID = "area_id"

# offered per zone; the API has no detector type
ZONE_DEVICE_CLASSES = (
    "door",
    "window",
    "garage_door",
    "opening",
    "motion",
    "smoke",
    "moisture",
    "lock",
    "tamper",
    "vibration",
)

# the panel's own HTTPS port; a URL with https:// and no port means 443
DEFAULT_PORT = 4433

# status interval in seconds; never below the official app's own cycle
DEFAULT_SCAN_INTERVAL = 30
MIN_SCAN_INTERVAL = 24
MAX_SCAN_INTERVAL = 3600
# log interval in seconds; reading the log takes the panel several seconds,
# so it is read rarely and incrementally (#11)
DEFAULT_LOG_INTERVAL = 300
MIN_LOG_INTERVAL = 120
MAX_LOG_INTERVAL = 3600
# the first log read after setup comes with a later round, not the first
FIRST_LOG_DELAY = 60

# after failed rounds: the delay doubles up to BACKOFF_MAX; after
# PAUSE_AFTER failures in a row polling pauses for PAUSE (seconds)
BACKOFF_MAX = 300
PAUSE_AFTER = 5
PAUSE = 900
# entities show the last state through single failed rounds, but not for
# long: from this many failures in a row they are unavailable (about 3 min)
UNAVAILABLE_AFTER = 3

# tested model and firmware (ADR 0005)
TESTED_MODEL = "Secvest Touch FUAA50500"
TESTED_FIRMWARE = "v3.01.31"

# omit the open zones blocking arming once, then arm (#118)
SERVICE_OMIT_AND_ARM = "omit_and_arm"
ATTR_MODE = "mode"
