"""Constants for the ABUS Secvest integration."""

DOMAIN = "secvest"

CONF_USER_CODE = "user_code"
CONF_USER_AGENT = "user_agent"
CONF_ADVANCED = "advanced"
# set after a 401; nothing is sent with the stored credentials until
# a reauthentication succeeds (#6, #41)
CONF_AUTH_FAILED = "auth_failed"
CONF_PARTITIONS = "partitions"
CONF_SCAN_INTERVAL = "scan_interval"

# the panel's own HTTPS port; a URL with https:// and no port means 443
DEFAULT_PORT = 4433

# status interval in seconds; never below the official app's own cycle
DEFAULT_SCAN_INTERVAL = 30
MIN_SCAN_INTERVAL = 24

# after failed rounds: the delay doubles up to BACKOFF_MAX; after
# PAUSE_AFTER failures in a row polling pauses for PAUSE (seconds)
BACKOFF_MAX = 300
PAUSE_AFTER = 5
PAUSE = 900

# tested model and firmware (ADR 0005)
TESTED_MODEL = "Secvest Touch FUAA50500"
TESTED_FIRMWARE = "v3.01.31"
