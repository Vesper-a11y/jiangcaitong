"""金智 KINGOSOFT 登录加密：strEnc（3DES）+ 登录参数封装。

逐行移植 `jxufe_scripts/jkingo.des.js`。⚠️ 它的 DES 不是标准库那套：
`initPermute` 按 8x8 矩阵行列转置式重排（不是标准 DES 的 IP 表），
所以 `Crypto.Cipher.DES` 算出来对不上，必须照 JS 自己实现。
这里已用原始 JS 对拍验证（python test_jxufe.py）。

算法链：
  - 密钥：getKeyBytes，每 4 个 UTF-16 字符一组 → 64 bit。
  - 明文：strToBt，每 4 个 UTF-16 字符一组 → 64 bit。
  - 每组 bit 依次经过 enc(K1) → enc(K2) → enc(K3)（本项目只一个密钥）。
  - 输出：64 bit → 16 位大写 hex。
  - 登录参数：base64(strEnc(params, deskey)) + md5(md5(params)+md5(timestamp))。
"""

from __future__ import annotations

import base64
import hashlib

# ---------------- DES 核心（照 jkingo.des.js） ----------------

_IP = [58, 50, 42, 34, 26, 18, 10, 2,
       60, 52, 44, 36, 28, 20, 12, 4,
       62, 54, 46, 38, 30, 22, 14, 6,
       64, 56, 48, 40, 32, 24, 16, 8,
       57, 49, 41, 33, 25, 17, 9, 1,
       59, 51, 43, 35, 27, 19, 11, 3,
       61, 53, 45, 37, 29, 21, 13, 5,
       63, 55, 47, 39, 31, 23, 15, 7]
_FP = [40, 8, 48, 16, 56, 24, 64, 32,
       39, 7, 47, 15, 55, 23, 63, 31,
       38, 6, 46, 14, 54, 22, 62, 30,
       37, 5, 45, 13, 53, 21, 61, 29,
       36, 4, 44, 12, 52, 20, 60, 28,
       35, 3, 43, 11, 51, 19, 59, 27,
       34, 2, 42, 10, 50, 18, 58, 26,
       33, 1, 41, 9, 49, 17, 57, 25]
_E = [32, 1, 2, 3, 4, 5, 4, 5, 6, 7, 8, 9, 8, 9, 10, 11, 12, 13,
      12, 13, 14, 15, 16, 17, 16, 17, 18, 19, 20, 21, 20, 21, 22, 23,
      24, 25, 24, 25, 26, 27, 28, 29, 28, 29, 30, 31, 32, 1]
_P = [16, 7, 20, 21, 29, 12, 28, 17, 1, 15, 23, 26, 5, 18, 31, 10,
      2, 8, 24, 14, 32, 27, 3, 9, 19, 13, 30, 6, 22, 11, 4, 25]

# 金智 JS 的 initPermute：按 8x8 行列转置展开。
# 等价于「先按 IP 表取位，但输入位序是 column-major」。
# 实测它与标准 IP 不同，直接用 JS 的循环写法最稳。
# 从 jkingo.des.js 实际导出的 0-based 置换表（和标准 DES 表顺序不同！）
_IP0 = [57, 49, 41, 33, 25, 17, 9, 1, 59, 51, 43, 35, 27, 19, 11, 3,
        61, 53, 45, 37, 29, 21, 13, 5, 63, 55, 47, 39, 31, 23, 15, 7,
        56, 48, 40, 32, 24, 16, 8, 0, 58, 50, 42, 34, 26, 18, 10, 2,
        60, 52, 44, 36, 28, 20, 12, 4, 62, 54, 46, 38, 30, 22, 14, 6]
_FP0 = [39, 7, 47, 15, 55, 23, 63, 31, 38, 6, 46, 14, 54, 22, 62, 30,
        37, 5, 45, 13, 53, 21, 61, 29, 36, 4, 44, 12, 52, 20, 60, 28,
        35, 3, 43, 11, 51, 19, 59, 27, 34, 2, 42, 10, 50, 18, 58, 26,
        33, 1, 41, 9, 49, 17, 57, 25, 32, 0, 40, 8, 48, 16, 56, 24]
_P0 = [15, 6, 19, 20, 28, 11, 27, 16, 0, 14, 22, 25, 4, 17, 30, 9,
       1, 7, 23, 13, 31, 26, 2, 8, 18, 12, 29, 5, 21, 10, 3, 24]


def _init_permute(data: list[int]) -> list[int]:
    return [data[i] for i in _IP0]


def _final_permute(data: list[int]) -> list[int]:
    return [data[i] for i in _FP0]


def _expand(right: list[int]) -> list[int]:
    out = [0] * 48
    for i in range(8):
        if i == 0:
            out[0] = right[31]
        else:
            out[i * 6] = right[i * 4 - 1]
        for j in range(1, 5):
            out[i * 6 + j] = right[i * 4 + j - 1]
        if i == 7:
            out[47] = right[0]
        else:
            out[i * 6 + 5] = right[i * 4 + 4]
    return out


_SBOX = [[[14, 4, 13, 1, 2, 15, 11, 8, 3, 10, 6, 12, 5, 9, 0, 7], [0, 15, 7, 4, 14, 2, 13, 1, 10, 6, 12, 11, 9, 5, 3, 8], [4, 1, 14, 8, 13, 6, 2, 11, 15, 12, 9, 7, 3, 10, 5, 0], [15, 12, 8, 2, 4, 9, 1, 7, 5, 11, 3, 14, 10, 0, 6, 13]], [[15, 1, 8, 14, 6, 11, 3, 4, 9, 7, 2, 13, 12, 0, 5, 10], [3, 13, 4, 7, 15, 2, 8, 14, 12, 0, 1, 10, 6, 9, 11, 5], [0, 14, 7, 11, 10, 4, 13, 1, 5, 8, 12, 6, 9, 3, 2, 15], [13, 8, 10, 1, 3, 15, 4, 2, 11, 6, 7, 12, 0, 5, 14, 9]], [[10, 0, 9, 14, 6, 3, 15, 5, 1, 13, 12, 7, 11, 4, 2, 8], [13, 7, 0, 9, 3, 4, 6, 10, 2, 8, 5, 14, 12, 11, 15, 1], [13, 6, 4, 9, 8, 15, 3, 0, 11, 1, 2, 12, 5, 10, 14, 7], [1, 10, 13, 0, 6, 9, 8, 7, 4, 15, 14, 3, 11, 5, 2, 12]], [[7, 13, 14, 3, 0, 6, 9, 10, 1, 2, 8, 5, 11, 12, 4, 15], [13, 8, 11, 5, 6, 15, 0, 3, 4, 7, 2, 12, 1, 10, 14, 9], [10, 6, 9, 0, 12, 11, 7, 13, 15, 1, 3, 14, 5, 2, 8, 4], [3, 15, 0, 6, 10, 1, 13, 8, 9, 4, 5, 11, 12, 7, 2, 14]], [[2, 12, 4, 1, 7, 10, 11, 6, 8, 5, 3, 15, 13, 0, 14, 9], [14, 11, 2, 12, 4, 7, 13, 1, 5, 0, 15, 10, 3, 9, 8, 6], [4, 2, 1, 11, 10, 13, 7, 8, 15, 9, 12, 5, 6, 3, 0, 14], [11, 8, 12, 7, 1, 14, 2, 13, 6, 15, 0, 9, 10, 4, 5, 3]], [[12, 1, 10, 15, 9, 2, 6, 8, 0, 13, 3, 4, 14, 7, 5, 11], [10, 15, 4, 2, 7, 12, 9, 5, 6, 1, 13, 14, 0, 11, 3, 8], [9, 14, 15, 5, 2, 8, 12, 3, 7, 0, 4, 10, 1, 13, 11, 6], [4, 3, 2, 12, 9, 5, 15, 10, 11, 14, 1, 7, 6, 0, 8, 13]], [[4, 11, 2, 14, 15, 0, 8, 13, 3, 12, 9, 7, 5, 10, 6, 1], [13, 0, 11, 7, 4, 9, 1, 10, 14, 3, 5, 12, 2, 15, 8, 6], [1, 4, 11, 13, 12, 3, 7, 14, 10, 15, 6, 8, 0, 5, 9, 2], [6, 11, 13, 8, 1, 4, 10, 7, 9, 5, 0, 15, 14, 2, 3, 12]], [[13, 2, 8, 4, 6, 15, 11, 1, 10, 9, 3, 14, 5, 0, 12, 7], [1, 15, 13, 8, 10, 3, 7, 4, 12, 5, 6, 11, 0, 14, 9, 2], [7, 11, 4, 1, 9, 12, 14, 2, 0, 6, 10, 13, 15, 3, 5, 8], [2, 1, 14, 7, 4, 10, 8, 13, 15, 12, 9, 0, 3, 5, 6, 11]]]
# PC1 / PC2 用金智 generateKeys 里的写法（上面截取到的 key[] 索引）
# JS generateKeys 的 PC-2：tempKey[m] = key[PC2_JS[m]]（0-based，从 JS 源码抄）
_PC2_JS = [13, 16, 10, 23, 0, 4, 2, 27, 14, 5, 20, 9,
           22, 18, 11, 3, 25, 7, 15, 6, 26, 19, 12, 1,
           40, 51, 30, 36, 46, 54, 29, 39, 50, 44, 32, 47,
           43, 48, 38, 55, 33, 52, 45, 41, 49, 35, 28, 31]
_SHIFTS = [1, 1, 2, 2, 2, 2, 2, 2, 1, 2, 2, 2, 2, 2, 2, 1]


def _generate_keys(key64: list[int]) -> list[list[int]]:
    """逐行复刻 jkingo.des.js 的 generateKeys（PC1 用它的转置循环）。"""
    key = [0] * 56
    for i in range(7):
        for j in range(8):
            k = 7 - j
            key[i * 8 + j] = key64[8 * k + i]
    out = []
    for rnd in range(16):
        for _ in range(_SHIFTS[rnd]):
            tl, tr = key[0], key[28]
            for k in range(27):
                key[k] = key[k + 1]
                key[28 + k] = key[29 + k]
            key[27], key[55] = tl, tr
        out.append([key[p] for p in _PC2_JS])
    return out


def _sbox_permute(x: list[int]) -> list[int]:
    out = [0] * 32
    for i in range(8):
        j = i * 6
        row = (x[j] << 1) | x[j + 5]
        col = (x[j + 1] << 3) | (x[j + 2] << 2) | (x[j + 3] << 1) | x[j + 4]
        v = _SBOX[i][row][col]
        for k in range(4):
            out[i * 4 + k] = (v >> (3 - k)) & 1
    return out


def _xor(a: list[int], b: list[int]) -> list[int]:
    return [x ^ y for x, y in zip(a, b)]


def _enc_block(block: list[int], key64: list[int]) -> list[int]:
    keys = _generate_keys(key64)
    ip = _init_permute(block)
    left, right = ip[:32], ip[32:]
    for rnd in range(16):
        prev = left[:]
        left = right[:]
        f = _sbox_permute(_xor(_expand(right), keys[rnd]))
        right = _xor([f[i] for i in _P0], prev)
    return _final_permute(right + left)


# ---------------- 字符串 / 密钥 ----------------

def _bits(text: str) -> list[int]:
    """strToBt：每 4 个 UTF-16 字符一组，字符 16 位高位在前。"""
    out = [0] * 64
    for i in range(4):
        ch = ord(text[i]) if i < len(text) else 0
        for j in range(16):
            out[i * 16 + j] = (ch >> (15 - j)) & 1
    return out


def _key_bits(key: str) -> list[int]:
    return _bits(key[:4])


def _bits_to_hex(bits: list[int]) -> str:
    out = []
    for i in range(0, 64, 4):
        v = 0
        for b in bits[i:i + 4]:
            v = (v << 1) | b
        out.append("%X" % v)
    return "".join(out)


def str_enc(data: str, key: str) -> str:
    """金智 strEnc → 大写 hex 串。"""
    if not data or not key:
        return ""
    keys = [_key_bits(key[i:i + 4]) for i in range(0, len(key), 4)]
    out = []
    for i in range(0, len(data), 4):
        cur = _bits(data[i:i + 4])
        for k in keys:
            cur = _enc_block(cur, k)
        out.append(_bits_to_hex(cur))
    return "".join(out)


def md5_hex(s: str) -> str:
    return hashlib.md5(s.encode("utf-8")).hexdigest()


def enc_params(params: str, deskey: str, timestamp: str) -> str:
    """登录 POST 的 params/token/timestamp 部分。timestamp 必须来自 getTempNowtime。"""
    if not timestamp:
        raise ValueError("timestamp 必须从 /frame/homepage?method=getTempNowtime 获取")
    token = md5_hex(md5_hex(params) + md5_hex(timestamp))
    encoded = base64.b64encode(str_enc(params, deskey).encode("ascii")).decode("ascii")
    return "params=%s&token=%s&timestamp=%s" % (encoded, token, timestamp)


__all__ = ["str_enc", "md5_hex", "enc_params"]
