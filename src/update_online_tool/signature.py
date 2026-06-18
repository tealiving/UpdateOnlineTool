"""manifest 签名工具。"""

from __future__ import annotations

import hmac
import json
import secrets
from base64 import b64decode, b64encode
from hashlib import sha256
from pathlib import Path
from typing import Any

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey

from update_online_tool.errors import UpdateError, UpdateErrorCode

SIGNATURE_ALGORITHM = "hmac-sha256"
ED25519_SIGNATURE_ALGORITHM = "ed25519"


def generate_hmac_key() -> str:
    """生成 HMAC-SHA256 签名密钥。"""
    return secrets.token_hex(32)


def generate_ed25519_private_key_pem() -> str:
    """生成 Ed25519 私钥 PEM。"""
    private_key = Ed25519PrivateKey.generate()
    return private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    ).decode("utf-8")


def derive_ed25519_public_key_pem(private_key_path: Path) -> str:
    """从 Ed25519 私钥文件导出公钥 PEM。"""
    private_key = _load_ed25519_private_key(private_key_path)
    return private_key.public_key().public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    ).decode("utf-8")


def load_hmac_key(path: Path) -> bytes:
    """读取 HMAC 签名密钥。"""
    try:
        text = Path(path).read_text(encoding="utf-8").strip()
    except OSError as exc:
        raise UpdateError(UpdateErrorCode.SETTINGS_INVALID, f"signature key cannot be read: {path}") from exc
    if not text:
        raise UpdateError(UpdateErrorCode.SETTINGS_INVALID, "signature key must be non-empty")
    try:
        return bytes.fromhex(text)
    except ValueError:
        return text.encode("utf-8")


def sign_manifest_payload(payload: dict[str, Any], *, key: bytes, key_id: str = "") -> dict[str, Any]:
    """对 manifest payload 生成签名并返回新 payload。"""
    unsigned_payload = dict(payload)
    unsigned_payload.pop("signature", None)
    signature_value = _hmac_hex(unsigned_payload, key)
    signed_payload = dict(unsigned_payload)
    signed_payload["signature"] = {
        "algorithm": SIGNATURE_ALGORITHM,
        "key_id": str(key_id or "default").strip(),
        "value": signature_value,
    }
    return signed_payload


def sign_manifest_payload_with_key_file(
    payload: dict[str, Any],
    *,
    key_path: Path,
    key_id: str = "",
) -> dict[str, Any]:
    """按密钥文件类型对 manifest payload 签名。"""
    if _looks_like_pem(key_path):
        return _sign_ed25519_manifest_payload(payload, private_key_path=key_path, key_id=key_id)
    return sign_manifest_payload(payload, key=load_hmac_key(key_path), key_id=key_id)


def verify_manifest_signature(payload: dict[str, Any], *, key: bytes) -> None:
    """校验 manifest payload 的签名。"""
    signature = payload.get("signature")
    if not isinstance(signature, dict):
        raise UpdateError(UpdateErrorCode.MANIFEST_INVALID, "manifest signature is required")
    algorithm = signature.get("algorithm")
    if algorithm != SIGNATURE_ALGORITHM:
        raise UpdateError(UpdateErrorCode.MANIFEST_INVALID, f"unsupported signature algorithm: {algorithm}")
    value = signature.get("value")
    if not isinstance(value, str) or not value.strip():
        raise UpdateError(UpdateErrorCode.MANIFEST_INVALID, "signature.value must be a non-empty string")
    unsigned_payload = dict(payload)
    unsigned_payload.pop("signature", None)
    expected = _hmac_hex(unsigned_payload, key)
    if not hmac.compare_digest(value.strip().lower(), expected):
        raise UpdateError(UpdateErrorCode.MANIFEST_INVALID, "manifest signature mismatch")


def verify_manifest_signature_with_key_file(payload: dict[str, Any], *, key_path: Path) -> None:
    """按签名算法和密钥文件校验 manifest 签名。"""
    signature = payload.get("signature")
    if not isinstance(signature, dict):
        raise UpdateError(UpdateErrorCode.MANIFEST_INVALID, "manifest signature is required")
    algorithm = signature.get("algorithm")
    if algorithm == SIGNATURE_ALGORITHM:
        verify_manifest_signature(payload, key=load_hmac_key(key_path))
        return
    if algorithm == ED25519_SIGNATURE_ALGORITHM:
        _verify_ed25519_manifest_signature(payload, public_key_path=key_path)
        return
    raise UpdateError(UpdateErrorCode.MANIFEST_INVALID, f"unsupported signature algorithm: {algorithm}")


def canonical_manifest_bytes(payload: dict[str, Any]) -> bytes:
    """生成稳定 JSON 字节，用于签名。"""
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _hmac_hex(payload: dict[str, Any], key: bytes) -> str:
    """计算 manifest payload 的 HMAC-SHA256。"""
    return hmac.new(key, canonical_manifest_bytes(payload), sha256).hexdigest()


def _sign_ed25519_manifest_payload(
    payload: dict[str, Any],
    *,
    private_key_path: Path,
    key_id: str,
) -> dict[str, Any]:
    """使用 Ed25519 私钥签名 manifest payload。"""
    unsigned_payload = dict(payload)
    unsigned_payload.pop("signature", None)
    signature_value = b64encode(
        _load_ed25519_private_key(private_key_path).sign(canonical_manifest_bytes(unsigned_payload))
    ).decode("ascii")
    signed_payload = dict(unsigned_payload)
    signed_payload["signature"] = {
        "algorithm": ED25519_SIGNATURE_ALGORITHM,
        "key_id": str(key_id or "default").strip(),
        "value": signature_value,
    }
    return signed_payload


def _verify_ed25519_manifest_signature(payload: dict[str, Any], *, public_key_path: Path) -> None:
    """使用 Ed25519 公钥校验 manifest 签名。"""
    signature = payload.get("signature")
    if not isinstance(signature, dict):
        raise UpdateError(UpdateErrorCode.MANIFEST_INVALID, "manifest signature is required")
    value = signature.get("value")
    if not isinstance(value, str) or not value.strip():
        raise UpdateError(UpdateErrorCode.MANIFEST_INVALID, "signature.value must be a non-empty string")
    unsigned_payload = dict(payload)
    unsigned_payload.pop("signature", None)
    try:
        signature_bytes = b64decode(value.strip(), validate=True)
        _load_ed25519_public_key(public_key_path).verify(signature_bytes, canonical_manifest_bytes(unsigned_payload))
    except (ValueError, InvalidSignature) as exc:
        raise UpdateError(UpdateErrorCode.MANIFEST_INVALID, "manifest signature mismatch") from exc


def _looks_like_pem(path: Path) -> bool:
    """判断密钥文件是否为 PEM。"""
    try:
        prefix = Path(path).read_text(encoding="utf-8", errors="ignore").lstrip()[:64]
    except OSError as exc:
        raise UpdateError(UpdateErrorCode.SETTINGS_INVALID, f"signature key cannot be read: {path}") from exc
    return prefix.startswith("-----BEGIN ")


def _load_ed25519_private_key(path: Path) -> Ed25519PrivateKey:
    """读取 Ed25519 私钥 PEM。"""
    try:
        data = Path(path).read_bytes()
        key = serialization.load_pem_private_key(data, password=None)
    except (OSError, ValueError) as exc:
        raise UpdateError(UpdateErrorCode.SETTINGS_INVALID, f"Ed25519 private key cannot be read: {path}") from exc
    if not isinstance(key, Ed25519PrivateKey):
        raise UpdateError(UpdateErrorCode.SETTINGS_INVALID, "signature private key must be Ed25519")
    return key


def _load_ed25519_public_key(path: Path) -> Ed25519PublicKey:
    """读取 Ed25519 公钥 PEM；如果传入私钥，则导出其公钥。"""
    try:
        data = Path(path).read_bytes()
    except OSError as exc:
        raise UpdateError(UpdateErrorCode.SETTINGS_INVALID, f"Ed25519 public key cannot be read: {path}") from exc
    try:
        public_key = serialization.load_pem_public_key(data)
        if isinstance(public_key, Ed25519PublicKey):
            return public_key
    except ValueError:
        pass
    try:
        private_key = serialization.load_pem_private_key(data, password=None)
    except ValueError as exc:
        raise UpdateError(UpdateErrorCode.SETTINGS_INVALID, "signature public key must be Ed25519") from exc
    if not isinstance(private_key, Ed25519PrivateKey):
        raise UpdateError(UpdateErrorCode.SETTINGS_INVALID, "signature public key must be Ed25519")
    return private_key.public_key()
