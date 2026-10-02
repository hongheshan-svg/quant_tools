#!/bin/sh
# 放入默认配置、修复挂载目录的属主，然后以 quant 用户运行
set -eu

mkdir -p /app/config /app/data /app/logs
# 示例配置每次覆盖（新版本新增的配置项靠它提供默认值）；股票池规则只在缺失时复制，不覆盖用户改过的版本
cp /app/defaults/config/settings.yaml.example /app/config/settings.yaml.example
cp /app/defaults/config/screening_rules.yaml.example /app/config/screening_rules.yaml.example
cp /app/defaults/config/scoring_profiles.yaml.example /app/config/scoring_profiles.yaml.example
[ -f /app/config/stock_pool.yaml ] || cp /app/defaults/config/stock_pool.yaml /app/config/stock_pool.yaml

if [ "$(id -u)" = "0" ]; then
    for dir in /app/config /app/data /app/logs; do
        if [ -n "$(find "$dir" \( ! -user 1000 -o ! -group 1000 \) -print -quit 2>/dev/null)" ]; then
            chown -R 1000:1000 "$dir" || echo "WARN: 无法修改 $dir 的属主，写入可能失败" >&2
        fi
    done
    export HOME=/home/quant
    exec gosu quant:quant "$@"
fi
exec "$@"
