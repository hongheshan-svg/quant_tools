#!/usr/bin/env bash
# afterPack 阶段修复无效签名；不替代 Developer ID 签名或公证。
set -euo pipefail
artifact="${1:?请指定 app 目录}"
[[ -d "$artifact" ]] || exit 2
while IFS= read -r -d '' candidate; do
  if [[ -f "$candidate" ]] && ! file -b "$candidate" | grep -q 'Mach-O'; then
    continue
  fi
  details="$(codesign -d "$candidate" 2>&1 || true)"
  if [[ "$details" == *'code object is not signed at all'* ]]; then
    continue
  fi
  if ! codesign --verify --strict "$candidate" >/dev/null 2>&1; then
    codesign --remove-signature "$candidate"
    details="$(codesign -d "$candidate" 2>&1 || true)"
    [[ "$details" == *'code object is not signed at all'* ]] || exit 1
    echo "已清理失效签名：$candidate"
  fi
done < <(find "$artifact" -depth \( -type f -o -type d \( -name '*.app' -o -name '*.framework' -o -name '*.xpc' \) \) -print0)
