"""交互式初始化 Longbridge OAuth；后台服务不启动此授权流程。"""
import argparse
import base64
from pathlib import Path
import sys
import webbrowser

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.services.longbridge_auth import cache_path, validate_client_id, valid_cache, _atomic_private_write


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--client-id', required=True, type=validate_client_id)
    parser.add_argument('--export-file', type=Path, help='可选：把 Base64 缓存保存为仅当前用户可读的文件；已有文件不覆盖')
    args = parser.parse_args()
    if args.export_file and args.export_file.exists():
        parser.error('导出文件已存在，请指定一个新路径')
    try:
        from longbridge.openapi import OAuthBuilder
        def open_url(url):
            print('请在浏览器中完成 Longbridge 授权。若未自动打开，请手动访问：', url)
            webbrowser.open(url)
        OAuthBuilder(args.client_id).build(open_url)
        path = cache_path(args.client_id)
        if not valid_cache(path):
            raise RuntimeError('cache_missing')
        print('OAuth 缓存已就绪。在扩展接入中填写相同的 OAuth Client ID。')
        if args.export_file:
            _atomic_private_write(args.export_file.absolute(), base64.b64encode(path.read_bytes()))
            print('部署缓存已保存。将文件内容作为部署环境的 OAuth 缓存密钥，不要提交到 Git。')
        return 0
    except Exception:
        print('授权未完成。请检查 SDK 是否支持 OAuth、Client ID 和回调地址配置。', file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
