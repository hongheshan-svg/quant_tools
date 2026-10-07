"""告警配置来源与有效数量摘要，不返回环境变量内容。"""
from src.config_loader import env_overrides
from src.services.alert_service import validate_rule
from src.settings_store import read_settings


def rule_sources(config):
    file_config = read_settings()
    overrides = env_overrides(base=config)
    overridden = 'rules' in (overrides.get('alerts') or {})
    raw = (config.get('alerts') or {}).get('rules') or []
    normalized, invalid = [], 0
    if not isinstance(raw, list):
        invalid = 1
        raw = []
    for rule in raw:
        try: normalized.append(validate_rule(rule))
        except (ValueError, TypeError): invalid += 1
    file_rules = (file_config.get('alerts') or {}).get('rules') or []
    return {'rules': normalized, 'summary': {
        'source': 'environment' if overridden else 'file_or_default', 'readonly': overridden,
        'configured': len(raw), 'valid': len(normalized), 'invalid': invalid,
        'disabled': sum(not rule['enabled'] for rule in normalized), 'effective': sum(rule['enabled'] for rule in normalized),
        'file_configured': len(file_rules) if isinstance(file_rules, list) else 0,
        'environment_override': overridden,
    }}
