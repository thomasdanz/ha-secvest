"""Constants for the ABUS Secvest integration."""

DOMAIN = "secvest"

CONF_USER_CODE = "user_code"
CONF_USER_AGENT = "user_agent"
CONF_ADVANCED = "advanced"
CONF_PARTITIONS = "partitions"
CONF_SCAN_INTERVAL = "scan_interval"

# the panel's own HTTPS port; a URL with https:// and no port means 443
DEFAULT_PORT = 4433

# status interval in seconds; never below the official app's own cycle
DEFAULT_SCAN_INTERVAL = 30
MIN_SCAN_INTERVAL = 24

# tested model and firmware (ADR 0005)
TESTED_MODEL = "Secvest Touch FUAA50500"
TESTED_FIRMWARE = "v3.01.31"
