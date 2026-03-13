import yaml


class ConfigError(Exception):
    pass


DEFAULTS = {
    "poll_interval": 60,
    "strategy": "median",
    "sui_bin": "sui",
}


def load_config(path: str) -> dict:
    try:
        with open(path, "r") as f:
            data = yaml.safe_load(f)
    except FileNotFoundError:
        raise ConfigError(f"Config file not found: {path}")
    except yaml.YAMLError as e:
        raise ConfigError(f"Invalid YAML in config: {e}")

    if not isinstance(data, dict):
        raise ConfigError("Config must be a YAML mapping")

    for key, default in DEFAULTS.items():
        data.setdefault(key, default)

    _validate(data)
    return data


def _validate(cfg: dict):
    # rpc_url
    rpc_url = cfg.get("rpc_url", "")
    if not rpc_url or not isinstance(rpc_url, str):
        raise ConfigError("rpc_url must be a non-empty string")
    if not rpc_url.startswith(("http://", "https://")):
        raise ConfigError(f"rpc_url must start with http:// or https://, got '{rpc_url}'")

    # trusted_validators
    validators = cfg.get("trusted_validators", [])
    if not isinstance(validators, list) or len(validators) == 0:
        raise ConfigError("trusted_validators must be a non-empty list")

    # min_quorum
    quorum = cfg.get("min_quorum", 0)
    if not isinstance(quorum, int) or quorum < 1:
        raise ConfigError("min_quorum must be >= 1")
    if quorum > len(validators):
        raise ConfigError(
            f"min_quorum ({quorum}) exceeds trusted_validators count ({len(validators)})"
        )

    # strategy
    strategy = cfg.get("strategy", "")
    if strategy not in ("median", "average"):
        raise ConfigError(f"strategy must be 'median' or 'average', got '{strategy}'")

    # telegram
    tg = cfg.get("telegram", {})
    if not isinstance(tg, dict):
        raise ConfigError("telegram must be a mapping")
    if not tg.get("bot_token"):
        raise ConfigError("telegram.bot_token must be a non-empty string")
    if not tg.get("chat_id"):
        raise ConfigError("telegram.chat_id must be a non-empty string")

    # poll_interval
    poll_interval = cfg.get("poll_interval", 60)
    if not isinstance(poll_interval, (int, float)) or poll_interval <= 0:
        raise ConfigError("poll_interval must be a positive number")
