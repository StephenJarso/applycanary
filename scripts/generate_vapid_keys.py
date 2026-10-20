#!/usr/bin/env python3
"""Generate VAPID keys for Web Push notifications."""

from __future__ import annotations

from cryptography.hazmat.primitives import serialization
from py_vapid import Vapid
from py_vapid.utils import b64urlencode


def generate_vapid_keys() -> tuple[str, str]:
    vapid = Vapid()
    vapid.generate_keys()

    # 32-byte raw private key
    private_val = vapid.private_key.private_numbers().private_value
    private_raw = private_val.to_bytes(32, "big")
    private_key_b64 = b64urlencode(private_raw)

    # 65-byte uncompressed public key point
    public_raw = vapid.public_key.public_bytes(
        serialization.Encoding.X962,
        serialization.PublicFormat.UncompressedPoint,
    )
    public_key_b64 = b64urlencode(public_raw)

    return private_key_b64, public_key_b64


def main() -> None:
    priv, pub = generate_vapid_keys()

    print("=================================================================")
    print(" ApplyCanary VAPID Keys for Web Push")
    print("=================================================================\n")
    print(f"VAPID_PRIVATE_KEY={priv}")
    print(f"VAPID_PUBLIC_KEY={pub}\n")
    print("-----------------------------------------------------------------")
    print("Add these variables to your .env file or environment:")
    print("-----------------------------------------------------------------")
    print(f"VAPID_PRIVATE_KEY={priv}")
    print(f"VAPID_PUBLIC_KEY={pub}")
    print("VAPID_EMAIL=mailto:admin@example.com\n")
    print("Note: VAPID_EMAIL must be a mailto: URL with a valid contact email.")
    print("=================================================================")


if __name__ == "__main__":
    main()
