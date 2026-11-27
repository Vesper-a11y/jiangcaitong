import { webview } from '@kit.ArkWeb';
import { ReqBridge, setWebController } from '../bridge/ReqBridge';
import { storeInit } from '../bridge/Store';
import { hilog } from '@kit.PerformanceAnalysisKit';
import { BusinessError } from '@kit.BasicServicesKit';
import { common } from '@kit.AbilityKit';

const TAG: string = 'HYT';

/**
 * 挂到 `window.Android` 上的方法。**声明式注入和手动注册必须用同一份名单** ——
 * 抄两遍的话，补一个方法只改了一处，就会出现「第一次能登、刷新一次就不行」
 * 或者反过来的鬼现象，而且两边都不报错。
 *
 * 🔴 少一个方法的后果不是「这个功能没有」，是**整条路断掉**：
 *    2026-11-14 用户报「所有账号都完全无法登录」，其中一处就是 `cxAes`
 *    没在这个名单里 —— 学习通拿不到密文，直接判「密码加密失败」。
 */
const METHODS: string[] = [
  'req', 'req2', 'openUrl',
  'cxAes', 'cxDumpCookies', 'cxLoadCookies', 'cxHasLogin',
  'cxSave', 'cxLoad', 'cxClear', 'cxClearCookies',
  'loadCred', 'saveCred', 'credState', 'clearCred',
  'loadPortalCred', 'savePortalCred', 'portalCredState', 'clearPortalCred',
  'loadCxPwd', 'saveCxPwd', 'cxPwdState', 'clearCxPwd',
  // 完美校园（饮水）的账号存盘。漏了这五个 ⇒ 登录成功后报「此版本不记住登录」。
  'saveWater', 'loadWater', 'waterState', 'clearWater',
  // 财务处（缴费）的账号存盘。和上面同一类：漏一个 = 登录完存不下。
  'saveCw', 'loadCw', 'cwState', 'clearCw',
  // 权限自查（2026-11-15）。鸿蒙这边回空串 ⇒ 页面把「权限」那一行藏起来。
  'permState', 'permOpen',
  'storageInfo',
  'cookie', 'clearCookies', 'clearCookiesFor'
];

/**
 * 整个 App 就是这一个 Web 容器。
 *
 * 页面在 `resources/rawfile/web/` 里，**和 Android 版的 assets/web 是同一份**
 * （`build_hap.py` 每次打包从 `mobile/web/` 现拷）。理由见 EntryAbility 的注释：
 * 两套界面必然岔开，而这个 App 的界面细节（一屏装下、像素级间距）已经被
 * 几十条判据盯着，重写一遍等于把这些判据全废掉。
 *
 * 原生这侧只注入一个对象 `Android`：网络、账号密码、学习通 cookie、密码加密。
 * 通知、NFC、后台任务这些**先不给**，`app.js` 里那些 `window.AndroidXxx &&`
 * 会自动降级成「此功能仅手机版」。这比给一个假实现好：假实现不报错、
 * 只是永远不动，用户会以为坏了。
 */
@Entry
@Component
struct Index {
  private controller: webview.WebviewController = new webview.WebviewController();
  private bridge: ReqBridge = new ReqBridge();
  /** 自查只补救一次，别形成「注册 → 刷新 → 再注册」的循环。 */
  private reRegistered: boolean = false;

  aboutToAppear(): void {
    // 账号密码要落在应用私有目录里，得先拿到上下文。
    const ctx = getContext(this) as common.UIAbilityContext;
    if (!storeInit(ctx)) {
      hilog.error(0x0000, TAG, '本机存储打不开：账号记不住，但这次能用');
    }
    // 容器先交给桥：回调要往这里送。桥自己不用 `this`，所以两边互不依赖。
    setWebController(this.controller);
  }

  /** 页面加载完：`window.Android` 在不在？不在就手动注册一次再刷新。 */
  private checkBridge(): void {
    this.controller.runJavaScript('typeof window.Android').then((res: string) => {
      // 回来的是 JSON 编码过的值（带引号），去掉引号再比。
      const v: string = res.replace(/["'\s]/g, '');
      if (v === 'object' || v === 'function') {
        return;
      }
      hilog.error(0x0000, TAG, '桥没挂上（typeof=%{public}s）', v);
      if (this.reRegistered) {
        hilog.error(0x0000, TAG, '手动注册后仍然没有 window.Android');
        return;
      }
      this.reRegistered = true;
      try {
        this.controller.registerJavaScriptProxy(this.bridge, 'Android', METHODS);
        this.controller.refresh();
        hilog.info(0x0000, TAG, '已手动注册桥并刷新一次');
      } catch (e) {
        const err = e as BusinessError;
        hilog.error(0x0000, TAG, '手动注册失败：%{public}s', err.message);
      }
    }).catch((e: BusinessError) => {
      hilog.warn(0x0000, TAG, '自查脚本没跑成：%{public}s', e.message);
    });
  }

  build() {
    Stack() {
      Web({ src: $rawfile('web/index.html'), controller: this.controller })
        .javaScriptAccess(true)
        .domStorageAccess(true)
        .fileAccess(true)
        .imageAccess(true)
        .onlineImageAccess(true)
        .zoomAccess(false)
        .mixedMode(MixedMode.All)
        .cacheMode(CacheMode.Default)
        .horizontalScrollBarAccess(false)
        .verticalScrollBarAccess(false)
        .javaScriptProxy({
          object: this.bridge,
          name: 'Android',
          methodList: METHODS,
          controller: this.controller
        })
        .onPageEnd(() => {
          this.checkBridge();
        })
    }
    .width('100%')
    .height('100%')
  }
}
