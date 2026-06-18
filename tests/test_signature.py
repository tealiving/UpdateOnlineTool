"""manifest 签名测试。"""

from __future__ import annotations

import pytest

from update_online_tool import UpdateError, UpdateErrorCode
from update_online_tool.manifest import UpdateManifest
from update_online_tool.signature import (
    derive_ed25519_public_key_pem,
    generate_ed25519_private_key_pem,
    generate_hmac_key,
    load_hmac_key,
    sign_manifest_payload,
    sign_manifest_payload_with_key_file,
    verify_manifest_signature,
    verify_manifest_signature_with_key_file,
)


def test_sign_manifest_payload_round_trips(tmp_path) -> None:
    """验证 manifest 可签名并通过校验。"""
    key_path = tmp_path / "signing.key"
    key_path.write_text(generate_hmac_key() + "\n", encoding="utf-8")
    payload = _payload()

    signed = sign_manifest_payload(payload, key=load_hmac_key(key_path), key_id="release")
    manifest = UpdateManifest.from_payload(signed)
    verify_manifest_signature(signed, key=load_hmac_key(key_path))

    assert manifest.signature is not None
    assert manifest.signature.algorithm == "hmac-sha256"
    assert manifest.signature.key_id == "release"


def test_verify_manifest_signature_rejects_tampering(tmp_path) -> None:
    """验证签名后的 manifest 被篡改会校验失败。"""
    key_path = tmp_path / "signing.key"
    key_path.write_text(generate_hmac_key() + "\n", encoding="utf-8")
    signed = sign_manifest_payload(_payload(), key=load_hmac_key(key_path), key_id="release")
    signed["version"] = "9.9.9"

    with pytest.raises(UpdateError) as error:
        verify_manifest_signature(signed, key=load_hmac_key(key_path))

    assert error.value.code is UpdateErrorCode.MANIFEST_INVALID


def test_ed25519_signature_round_trips_with_public_key(tmp_path) -> None:
    """验证 Ed25519 私钥签名、公钥验签。"""
    private_key_path = tmp_path / "signing.key"
    public_key_path = tmp_path / "signing.pub"
    private_key_path.write_text(generate_ed25519_private_key_pem(), encoding="utf-8")
    public_key_path.write_text(derive_ed25519_public_key_pem(private_key_path), encoding="utf-8")

    signed = sign_manifest_payload_with_key_file(_payload(), key_path=private_key_path, key_id="release")
    manifest = UpdateManifest.from_payload(signed)
    verify_manifest_signature_with_key_file(signed, key_path=public_key_path)

    assert manifest.signature is not None
    assert manifest.signature.algorithm == "ed25519"
    assert manifest.signature.key_id == "release"


def test_ed25519_signature_rejects_tampering(tmp_path) -> None:
    """验证 Ed25519 签名后的 manifest 被篡改会失败。"""
    private_key_path = tmp_path / "signing.key"
    public_key_path = tmp_path / "signing.pub"
    private_key_path.write_text(generate_ed25519_private_key_pem(), encoding="utf-8")
    public_key_path.write_text(derive_ed25519_public_key_pem(private_key_path), encoding="utf-8")
    signed = sign_manifest_payload_with_key_file(_payload(), key_path=private_key_path, key_id="release")
    signed["version"] = "9.9.9"

    with pytest.raises(UpdateError) as error:
        verify_manifest_signature_with_key_file(signed, key_path=public_key_path)

    assert error.value.code is UpdateErrorCode.MANIFEST_INVALID


def _payload() -> dict[str, object]:
    """构造签名测试 manifest。"""
    return {
        "schema_version": 2,
        "app_id": "my-tool",
        "channel": "stable",
        "version": "1.0.0",
        "mandatory": False,
        "min_supported_version": "1.0.0",
        "published_at": "2026-06-18T00:00:00+00:00",
        "notes": "release",
        "package": {
            "url": "my-tool/v1.0.0/package.zip",
            "size": 7,
            "sha256": "0" * 64,
        },
    }
