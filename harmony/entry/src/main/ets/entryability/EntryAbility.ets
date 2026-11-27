import { AbilityConstant, UIAbility, Want } from '@kit.AbilityKit';
import { window } from '@kit.ArkUI';
import { hilog } from '@kit.PerformanceAnalysisKit';

const TAG: string = 'HYT';

/**
 * 鸿蒙入口。
 *
 * 🔴 这里**不写业务**。整个 App 的界面和数据层都在 `rawfile/web/` 里那份
 *    index.html 中（和 Android 版同一份代码），原生这侧只做两件事：
 *    起一个全屏 Web 容器、把 `Android.req` 那颗桥接上。
 *    另写一套 ArkTS 界面 = 两套真相源，改一处永远忘另一处 —— Android 版
 *    吃过这个亏（`native.js` 和 `bridge.py` 各抄一遍，口径慢慢就岔了）。
 */
export default class EntryAbility extends UIAbility {
  onCreate(want: Want, launchParam: AbilityConstant.LaunchParam): void {
    hilog.info(0x0000, TAG, 'EntryAbility onCreate');
  }

  onWindowStageCreate(windowStage: window.WindowStage): void {
    windowStage.loadContent('pages/Index', (err) => {
      if (err.code) {
        hilog.error(0x0000, TAG, 'loadContent 失败 %{public}s', JSON.stringify(err));
      }
    });
  }

  onDestroy(): void {
    hilog.info(0x0000, TAG, 'EntryAbility onDestroy');
  }
}
