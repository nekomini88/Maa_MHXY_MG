# -*- coding: utf-8 -*-
"""MFAAvalonia 配置字段的解密（DPAPI → AES 设备密钥）。

移植自 **MaaGumballs（不思议迷宫小助手）** 的
``agent/utils/simpleEncryption.py`` + ``agent/utils/CrossPlatformProtectedData.py``
（https://github.com/KhazixW2/MaaGumballs）——那套实现长期在生产环境跑通了
「读 MFA 加密后的外部通知配置」，本模块与它保持**同一条解密链**：

1. Windows DPAPI：``CryptUnprotectData(blob, NULL, NULL, NULL, NULL, 0, &out)``。
   对应 MFA 的 ``SimpleEncryptionHelper.Encrypt`` 主路径
   （``ProtectedData.Protect(data, null, CurrentUser)`` + base64，无附加熵）。
2. AES-256-ECB + PKCS7，密钥 = ``sha256("{稳定系统描述}_{架构}_{设备UUID}_{机器名}")[:32]``。
   对应 MFA ``Encrypt`` 的 catch 分支（DPAPI 抛异常时走 ``EncryptProvider.AESEncrypt``）。
3. 同上但系统描述用**完整版本号**（legacy），兼容 MFA 早期的 ``GenerateLegacy``。

只用标准库；AES 分支需要 ``cryptography``，缺失就跳过（Windows 主线走 DPAPI，不受影响）。
本模块永不抛异常：解不开返回 None，由调用方决定怎么报错。
"""

import base64
import hashlib
import os
import platform
import re
import subprocess
import sys


# ---------------------------------------------------------------- 系统指纹

def get_stable_os_description(original_os_description: str) -> str:
    """Windows 描述里保留大版本（与 MFA 的 GetStableOSDescription 一致）。"""
    if not original_os_description:
        return original_os_description
    match = re.match(
        r"^(.*?Windows\s+(?:10|11|10\.0|11\.0))(?:\..*|\s.*)?$",
        original_os_description,
        re.IGNORECASE,
    )
    return match.group(1).strip() if match else original_os_description


def get_os_description() -> str:
    """对应 .NET 的 RuntimeInformation.OSDescription。"""
    system = platform.system()
    if system == "Windows":
        return f"Microsoft Windows {platform.win32_ver()[1]}"
    if system == "Linux":
        return f"Linux {platform.release()}"
    if system == "Darwin":
        return f"{platform.system()} {platform.release()}"
    return system


def get_os_architecture() -> str:
    """.NET 的 RuntimeInformation.OSArchitecture 是 X64/Arm64/X86 这种写法。"""
    machine = platform.machine().lower()
    if machine in ("x86_64", "amd64"):
        return "X64"
    if machine in ("aarch64", "arm64"):
        return "Arm64"
    if machine in ("i386", "x86"):
        return "X86"
    return machine.upper()


def get_machine_name() -> str:
    if platform.system() == "Windows":
        return (os.environ.get("COMPUTERNAME") or platform.node()).strip().upper()
    return platform.node().split(".")[0].upper()


def get_platform_specific_id() -> str:
    """设备唯一标识：Windows 取主板 UUID，Linux 取 DMI/机器码，macOS 取 IOPlatformUUID。"""
    try:
        system = platform.system()
        if system == "Windows":
            result = subprocess.run(
                [
                    "powershell",
                    "-NoProfile",
                    "-Command",
                    "(Get-CimInstance -ClassName Win32_ComputerSystemProduct).UUID",
                ],
                capture_output=True,
                text=True,
                check=True,
            )
            return result.stdout.strip()

        if system == "Linux":
            for path in ("/sys/class/dmi/id/product_uuid", "/etc/machine-id", "/var/lib/dbus/machine-id"):
                try:
                    if os.path.exists(path):
                        with open(path, "r", encoding="utf-8") as f:
                            value = f.read().strip()
                        if value:
                            return value
                except Exception:
                    continue
            # 最后的兜底：MAC 地址（不带分隔符）
            try:
                import uuid

                return f"{uuid.getnode():012X}"
            except Exception:
                return ""

        if system == "Darwin":
            output = subprocess.run(
                ["ioreg", "-rd1", "-c", "IOPlatformExpertDevice"],
                capture_output=True,
                text=True,
            ).stdout
            match = re.search(r'IOPlatformUUID" = "(.+?)"', output)
            return match.group(1) if match else ""
    except Exception:
        return ""
    return ""


def _sha256_upper(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest().upper()


def generate() -> str:
    """MFA 的 SimpleEncryptionHelper.Generate（稳定描述版）。"""
    combined = (
        f"{get_stable_os_description(get_os_description())}_{get_os_architecture()}"
        f"_{get_platform_specific_id()}_{get_machine_name()}"
    )
    return _sha256_upper(combined)


def generate_legacy() -> str:
    """MFA 的 GenerateLegacy（完整系统版本号）。"""
    combined = (
        f"{get_os_description()}_{get_os_architecture()}"
        f"_{get_platform_specific_id()}_{get_machine_name()}"
    )
    return _sha256_upper(combined)


# ---------------------------------------------------------------- 两条解密路径

def dpapi_decrypt(value: str):
    """Windows DPAPI（CryptUnprotectData）。非 Windows / 失败返回 None。"""
    if sys.platform != "win32":
        return None
    try:
        raw = base64.b64decode(str(value).strip(), validate=True)
    except Exception:
        return None
    try:
        import ctypes
        from ctypes import wintypes

        class DATA_BLOB(ctypes.Structure):
            _fields_ = [
                ("cbData", wintypes.DWORD),
                ("pbData", ctypes.POINTER(ctypes.c_char)),
            ]

        buf = ctypes.create_string_buffer(raw, len(raw))
        blob_in = DATA_BLOB(len(raw), ctypes.cast(buf, ctypes.POINTER(ctypes.c_char)))
        blob_out = DATA_BLOB()
        ok = ctypes.windll.crypt32.CryptUnprotectData(
            ctypes.byref(blob_in),
            None,  # ppszDataDescr
            None,  # pOptionalEntropy（MFA 传的是 null）
            None,  # pvReserved
            None,  # pPromptStruct
            0,     # dwFlags
            ctypes.byref(blob_out),
        )
        if not ok:
            return None
        try:
            plain = ctypes.string_at(blob_out.pbData, blob_out.cbData)
        finally:
            ctypes.windll.kernel32.LocalFree(blob_out.pbData)
        return plain.decode("utf-8", "replace").strip() or None
    except Exception:
        return None


def aes_decrypt(value: str, key: str):
    """AES-256-ECB + PKCS7 解密（密钥取 sha256 十六进制串前 32 个字符）。

    需要 ``cryptography``；没装就返回 None（Windows 主线走 DPAPI，不受影响）。
    """
    try:
        from cryptography.hazmat.backends import default_backend
        from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
        from cryptography.hazmat.primitives.padding import PKCS7
    except Exception:
        return None
    try:
        key_bytes = key.ljust(32)[:32].encode("utf-8")
        data = base64.b64decode(str(value).strip())
        if not data or len(data) % 16:
            return None
        decryptor = Cipher(algorithms.AES(key_bytes), modes.ECB(), backend=default_backend()).decryptor()
        padded = decryptor.update(data) + decryptor.finalize()
        unpadder = PKCS7(128).unpadder()
        plain = unpadder.update(padded) + unpadder.finalize()
        return plain.decode("utf-8").rstrip("\x00").strip() or None
    except Exception:
        return None


def aes_encrypt(plain_text: str, key: str):
    """与 aes_decrypt 对称，供测试与需要回写的场景使用。"""
    try:
        from cryptography.hazmat.backends import default_backend
        from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
        from cryptography.hazmat.primitives.padding import PKCS7
    except Exception:
        return None
    try:
        key_bytes = key.ljust(32)[:32].encode("utf-8")
        padder = PKCS7(128).padder()
        padded = padder.update(plain_text.encode("utf-8")) + padder.finalize()
        encryptor = Cipher(algorithms.AES(key_bytes), modes.ECB(), backend=default_backend()).encryptor()
        return base64.b64encode(encryptor.update(padded) + encryptor.finalize()).decode("utf-8")
    except Exception:
        return None


def decrypt(value: str):
    """按 MFA 的解密顺序尝试：DPAPI → AES(设备密钥) → AES(legacy 设备密钥)。

    返回明文；全部失败返回 None（调用方据此提示用户改用明文配置）。
    """
    text = str(value or "").strip()
    if not text:
        return None

    plain = dpapi_decrypt(text)
    if plain:
        return plain

    for fingerprint in (generate(), generate_legacy()):
        plain = aes_decrypt(text, fingerprint[:32])
        if plain:
            return plain
    return None
