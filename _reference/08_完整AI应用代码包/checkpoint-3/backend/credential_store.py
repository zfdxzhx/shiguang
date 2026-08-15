"""Secure local persistence for provider credentials.

The production desktop flow uses the current macOS user's Keychain. Tests can
inject an in-memory store and never touch a real Keychain.
"""

from __future__ import annotations

import json
import sys
from dataclasses import replace
from typing import Protocol

from .providers import ProviderSettings, provider_settings_from_session


KEYCHAIN_SERVICE = "com.kunbean.drawing-review.ai-provider"
KEYCHAIN_ACCOUNT = "provider-config-v1"


class CredentialStoreError(RuntimeError):
    """A safe, user-facing credential-store failure."""


class ProviderCredentialStore(Protocol):
    @property
    def available(self) -> bool: ...

    def load(self) -> ProviderSettings | None: ...

    def save(self, settings: ProviderSettings) -> None: ...

    def delete(self) -> None: ...


class MacOSKeychainProviderStore:
    """Store one provider configuration in the logged-in user's Keychain."""

    def __init__(self, *, service: str = KEYCHAIN_SERVICE, account: str = KEYCHAIN_ACCOUNT):
        self.service = service
        self.account = account
        try:
            import keyring
        except ImportError:
            self._keyring = None
        else:
            self._keyring = keyring

    @property
    def available(self) -> bool:
        if sys.platform != "darwin" or self._keyring is None:
            return False
        try:
            return float(self._keyring.get_keyring().priority) > 0
        except Exception:
            return False

    def load(self) -> ProviderSettings | None:
        if not self.available:
            return None
        try:
            raw = self._keyring.get_password(self.service, self.account)
        except Exception as exc:
            raise CredentialStoreError("无法读取本机钥匙串，请解锁登录钥匙串后重试。") from exc
        if not raw:
            return None
        try:
            payload = json.loads(raw)
            if payload.get("version") != 1:
                raise ValueError("unsupported saved credential version")
            settings = provider_settings_from_session(
                provider=str(payload["provider"]),
                model=str(payload["model"]),
                api_key=str(payload["api_key"]),
                secondary_model=str(payload.get("secondary_model") or ""),
                secondary_api_key=str(payload.get("secondary_api_key") or ""),
            )
        except Exception as exc:
            raise CredentialStoreError("本机保存的 AI 配置已损坏，请删除后重新配置。") from exc
        return replace(settings, source="keychain")

    def save(self, settings: ProviderSettings) -> None:
        if not self.available:
            raise CredentialStoreError("当前系统无法使用 macOS 钥匙串，请改用仅本次运行。")
        payload = json.dumps(
            {
                "version": 1,
                "provider": settings.provider,
                "model": settings.model,
                "api_key": settings.api_key,
                "secondary_model": settings.secondary_model,
                "secondary_api_key": settings.secondary_api_key,
            },
            ensure_ascii=False,
            separators=(",", ":"),
        )
        try:
            self._keyring.set_password(self.service, self.account, payload)
        except Exception as exc:
            raise CredentialStoreError("保存到本机钥匙串失败，请检查钥匙串权限。") from exc

    def delete(self) -> None:
        if not self.available:
            raise CredentialStoreError("当前系统无法使用 macOS 钥匙串。")
        try:
            self._keyring.delete_password(self.service, self.account)
        except self._keyring.errors.PasswordDeleteError:
            return
        except Exception as exc:
            raise CredentialStoreError("无法删除本机钥匙串中的 AI 配置。") from exc
