"""Configuration for Nokia G-2425G-A MAC filter controller."""

ROUTER_CONFIG = {
    "router_ip": "192.168.1.1",
    "username": "admin",
    "password": "XXXXXXXXXX",  # Last 8 chars of MAC from router label
    "cloud_api_url": "https://your-oracle-cloud.com",
    "cloud_api_key": "your-secret-api-key",
    "poll_interval": 30,
    "ssid_index": 1,  # 1 for 2.4GHz, 5 for 5GHz
    "write_timeout": 60,  # Router writes are slow (10-45 sec)
}

# Retry / backoff
MAX_RETRIES = 3
BASE_BACKOFF = 5  # seconds

# Logging
LOG_LEVEL = "INFO"
LOG_FORMAT = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
