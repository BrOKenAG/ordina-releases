"""Fail-closed, read-only production release authorization checks."""
import json
import os
import secrets
import subprocess

MAIN = 'BrOKenAG/ordina-releases'


def authorize_main_write():
    """Prove current server-side write access and the pinned signing key."""
    if os.environ.get('ORDINA_DEV_LAB') == '1' or os.environ.get('ORDINA_PLUGIN_LAB') == '1':
        raise ValueError('隔离环境不能执行生产写入。')
    try:
        result = subprocess.run(['gh', 'api', f'repos/{MAIN}'], capture_output=True,
                                text=True, timeout=15)
        info = json.loads(result.stdout) if result.returncode == 0 else {}
        permissions = info.get('permissions') if isinstance(info, dict) else None
        if not isinstance(permissions, dict) or permissions.get('push') is not True:
            raise ValueError('无法确认官方仓库写入权限。')
    except (OSError, ValueError, subprocess.SubprocessError) as error:
        raise ValueError('无法确认官方仓库写入权限。') from error
    try:
        from feed_signing import sign_document
        sign_document({'purpose': 'ordina-main-release-authorization',
                       'nonce': secrets.token_hex(32)})
    except (ValueError, OSError, subprocess.SubprocessError, ImportError) as error:
        raise ValueError('无法验证可信发布签名能力。') from error
