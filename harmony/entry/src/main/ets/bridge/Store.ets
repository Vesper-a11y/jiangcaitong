import { preferences } from '@kit.ArkData';
import { common } from '@kit.AbilityKit';
import { cryptoFramework } from '@kit.CryptoArchitectureKit';
import { util } from '@kit.ArkTS';
import { hilog } from '@kit.PerformanceAnalysisKit';

const TAG: string = 'HYT-Store';
const NAME: string = 'hyt';
/** 本机密钥在首选项里的键名。 */
const KEY_SLOT: string = 'k.v1';
/** 密文前缀。换算法换前缀，老数据读不出来就说「坏」，别假装解开了。 */
const SEAL: string = 'a1:';

let PREF: preferences.Preferences | null = null;
let CTX: common.UIAbilityContext | null = null;
let KEY: Uint8Array | null = null;

/** 建首选项。`EntryAbility` 起来时调一次，失败也只是「记不住账号」。 */
export function storeInit(ctx: common.UIAbilityContext): boolean {
  CTX = ctx;
  try {
    const opt: preferences.Options = { name: NAME };
    PREF = preferences.getPreferencesSync(ctx, opt);
    return true;
  } catch (e) {
    const err = e as Error;
    hilog.error(0x0000, TAG, '首选项打不开：%{public}s', err.message);
    PREF = null;
    return false;
  }
}

/** 给要拉系统能力（打开浏览器）的地方用。 */
export function appContext(): common.UIAbilityContext | null {
  return CTX;
}

// ------------------------------------------------------------------ 明文读写

export function rawGet(k: string): string {
  const p = PREF;
  if (p === null) {
    return '';
  }
  try {
    const v: preferences.ValueType = p.getSync(k, '');
    return typeof v === 'string' ? v : '';
  } catch (e) {
    hilog.warn(0x0000, TAG, '读 %{public}s 失败', k);
    return '';
  }
}

export function rawHas(k: string): boolean {
  const p = PREF;
  if (p === null) {
    return false;
  }
  try {
    return p.hasSync(k);
  } catch (e) {
    return false;
  }
}

export function rawPut(k: string, v: string): void {
  const p = PREF;
  if (p === null) {
    return;
  }
  try {
    p.putSync(k, v);
    p.flush().catch((e: Error) => {
      hilog.error(0x0000, TAG, '写 %{public}s 没落盘：%{public}s', k, e.message);
    });
  } catch (e) {
    const err = e as Error;
    hilog.error(0x0000, TAG, '写 %{public}s 失败：%{public}s', k, err.message);
  }
}

export function rawDel(k: string): void {
  const p = PREF;
  if (p === null) {
    return;
  }
  try {
    p.deleteSync(k);
    p.flush().catch((e: Error) => {
      hilog.warn(0x0000, TAG, '删 %{public}s 没落盘', k);
    });
  } catch (e) {
    hilog.warn(0x0000, TAG, '删 %{public}s 失败', k);
  }
}

// ------------------------------------------------------------------ 字节与编码

function bytesOf(s: string): Uint8Array {
  return new util.TextEncoder('utf-8').encodeInto(s);
}

function strOf(b: Uint8Array): string {
  return util.TextDecoder.create('utf-8').decodeToString(b);
}

export function b64Of(b: Uint8Array): string {
  return new util.Base64Helper().encodeToStringSync(b);
}

function bytesOfB64(s: string): Uint8Array {
  return new util.Base64Helper().decodeSync(s);
}

function randomBytes(n: number): Uint8Array {
  return cryptoFramework.createRandom().generateRandomSync(n).data;
}

/** AES-128-CBC/PKCS7 一把梭。`key` 必须是 16 字节。 */
export function aesCbc(enc: boolean, key: Uint8Array, iv: Uint8Array,
                       data: Uint8Array): Uint8Array {
  const k: cryptoFramework.SymKey =
    cryptoFramework.createSymKeyGenerator('AES128')
      .convertKeySync({ data: key } as cryptoFramework.DataBlob);
  const c: cryptoFramework.Cipher =
    cryptoFramework.createCipher('AES128|CBC|PKCS7');
  const spec: cryptoFramework.IvParamsSpec = {
    algName: 'IvParamsSpec',
    iv: { data: iv } as cryptoFramework.DataBlob
  };
  c.initSync(enc ? cryptoFramework.CryptoMode.ENCRYPT_MODE
                 : cryptoFramework.CryptoMode.DECRYPT_MODE, k, spec);
  return c.doFinalSync({ data: data } as cryptoFramework.DataBlob).data;
}

// ------------------------------------------------------------------ 加密存

/** 本机密钥：第一次用的时候现生成，之后一直用它。 */
function localKey(): Uint8Array {
  const cached = KEY;
  if (cached !== null) {
    return cached;
  }
  const saved = rawGet(KEY_SLOT);
  if (saved.length > 0) {
    try {
      const raw = bytesOfB64(saved);
      if (raw.length === 16) {
        KEY = raw;
        return raw;
      }
    } catch (e) {
      hilog.warn(0x0000, TAG, '本机密钥解不开，重新生成');
    }
  }
  const fresh = randomBytes(16);
  rawPut(KEY_SLOT, b64Of(fresh));
  KEY = fresh;
  return fresh;
}

/** 明文 → `a1:<iv>:<密文>`。 */
export function seal(plain: string): string {
  const iv = randomBytes(16);
  const ct = aesCbc(true, localKey(), iv, bytesOf(plain));
  return SEAL + b64Of(iv) + ':' + b64Of(ct);
}

/** `a1:<iv>:<密文>` → 明文。解不开返回空串（**不抛**，调用方按「坏了」处理）。 */
export function openSeal(sealed: string): string {
  if (sealed.length === 0) {
    return '';
  }
  if (!sealed.startsWith(SEAL)) {
    return '';
  }
  const body = sealed.substring(SEAL.length);
  const cut = body.indexOf(':');
  if (cut <= 0) {
    return '';
  }
  try {
    const iv = bytesOfB64(body.substring(0, cut));
    const ct = bytesOfB64(body.substring(cut + 1));
    return strOf(aesCbc(false, localKey(), iv, ct));
  } catch (e) {
    hilog.warn(0x0000, TAG, '密文解不开（密钥换过？）');
    return '';
  }
}

/** 加密存一段 JSON。返回 '' = 存好了；'soft' = 存了但用的是本机密钥；
 *  'fail: …' = 没存上。**不返回 ''** 假装成功 —— 界面要能如实说。 */
export function sealPut(k: string, plain: string): string {
  try {
    rawPut(k, seal(plain));
    if (rawGet(k).length === 0) {
      return 'fail: 写本机存储失败';
    }
    return 'soft';
  } catch (e) {
    const err = e as Error;
    return 'fail: ' + err.message;
  }
}

/** 读一段加密 JSON。'' = 没存过或解不开（用 rawHas 区分）。 */
export function sealGet(k: string): string {
  return openSeal(rawGet(k));
}

/** "none" 没存过 / "ok" 存着且解得开 / "broken" 存着但解不开。 */
export function sealState(k: string): string {
  if (!rawHas(k)) {
    return 'none';
  }
  return sealGet(k).length > 0 ? 'ok' : 'broken';
}
