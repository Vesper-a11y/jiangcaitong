#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""清单自动补齐：作者只交 .js，其余的算数由这里做。

合并到 main 之后由 .github/workflows/plugin-index.yml 自动跑，也可以本地手动跑：

  python tools/sync_index.py --dry-run    # 只看会改成什么，不动文件
  python tools/sync_index.py              # 真写 plugins/index.json

它做的事：
  · 扫 plugins/<id>/<ver>.js，给清单里缺失或与文件对不上的插件补条目 / 更新条目；
  · size 用 UTF-16 码元数、sha256 用 UTF-8 字节 —— 和 App 端 pluginFetchCode 的
    核法一字不差（App 核的是 JS 的 text.length，不是字节数）；
  · 内容变了就把 code +1（App 靠 code 比新旧）；
  · 名字、标签、简介从文件头的 `@name / @tag / @desc` 注释里读，没写就用默认值；
  · 提交人从该文件的最后一次提交取（GitHub 的 noreply 邮箱能还原出帐号）。

代码里的插件仍然由维护者在合并前看一遍 —— 这里只解决「清单要手写」这一件苦差。
"""
import argparse
import datetime
import hashlib
import io
import json
import os
import re
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PLUG = os.path.join(ROOT, 'plugins')
INDEX = os.path.join(PLUG, 'index.json')
OWNER = 'Vesper-a11y'          # 仓库主：这个帐号交的插件标「作者」

META = re.compile(r'@(name|tag|desc)[ \t]+([^\r\n*]+)')


def load():
    if not os.path.exists(INDEX):
        return {'v': 1, 'plugins': []}
    with io.open(INDEX, encoding='utf-8') as f:
        d = json.load(f)
    if not isinstance(d.get('plugins'), list):
        d['plugins'] = []
    return d


def save(d):
    with io.open(INDEX, 'w', encoding='utf-8', newline='\n') as f:
        json.dump(d, f, ensure_ascii=False, indent=2)
        f.write('\n')


def git(*args):
    try:
        out = subprocess.run(['git'] + list(args), cwd=ROOT,
                             capture_output=True, text=True)
        return out.stdout.strip() if out.returncode == 0 else ''
    except OSError:
        return ''


def who(path):
    """这个文件最后一次是谁提交的：能还原成 GitHub 帐号就还原，否则用署名。"""
    email = git('log', '-1', '--format=%ae', '--', path)
    m = re.match(r'^\d+\+([^@]+)@users\.noreply\.github\.com$', email or '')
    if m:
        return m.group(1)
    name = git('log', '-1', '--format=%an', '--', path)
    return (name or '').strip()


def when(path):
    day = git('log', '-1', '--format=%ad', '--date=short', '--', path)
    return day or datetime.date.today().isoformat()


def meta(text, pid):
    """从文件头注释读名字 / 标签 / 简介。"""
    head = text[:600]
    got = {}
    for key, val in META.findall(head):
        got.setdefault(key, val.strip())
    return {
        'name': (got.get('name') or pid)[:20],
        'tag': (got.get('tag') or '插件')[:10],
        'desc': (got.get('desc') or '')[:90],
    }


def ver_key(ver):
    """版本号按数字比大小：1.0.10 要排 1.0.9 后面（按字符串比会反）。"""
    nums = re.findall(r'\d+', ver or '')
    return tuple(int(n) for n in nums) or (0,)


def scan():
    """每个插件只取最新那版：{id: (ver, 相对路径, 文本)}。
    跳过 _template.js 这类下划线开头的文件；同一个 id 留多份历史版本时按版本号取最大。"""
    out = {}
    if not os.path.isdir(PLUG):
        return out
    for pid in sorted(os.listdir(PLUG)):
        d = os.path.join(PLUG, pid)
        if pid.startswith('_') or not os.path.isdir(d):
            continue
        for fn in sorted(os.listdir(d)):
            if not fn.endswith('.js') or fn.startswith('_'):
                continue
            ver = fn[:-3]
            if pid in out and ver_key(out[pid][0]) >= ver_key(ver):
                continue
            rel = 'plugins/%s/%s.js' % (pid, ver)
            with io.open(os.path.join(d, fn), encoding='utf-8', newline='') as f:
                out[pid] = (ver, rel, f.read())
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--dry-run', action='store_true', help='只打印，不写清单')
    a = ap.parse_args()

    d = load()
    by_id = {p.get('id'): p for p in d['plugins']}
    changed = False
    files = scan()

    for pid, (ver, rel, text) in files.items():
        size = len(text.encode('utf-16-le')) // 2      # UTF-16 码元数，同 App 的 text.length
        sha = hashlib.sha256(text.encode('utf-8')).hexdigest()
        prev = by_id.get(pid)

        if prev and prev.get('size') == size and prev.get('sha256') == sha \
                and prev.get('file') == rel:
            continue                                    # 文件没变，一条都不动

        m = meta(text, pid)
        author = who(rel) or (prev or {}).get('by') or OWNER
        entry = {
            'id': pid,
            'name': m['name'],
            'tag': m['tag'],
            'desc': m['desc'],
            'ver': ver,
            'code': (int(prev.get('code') or 0) + 1) if prev else 1,
            'file': rel,
            'size': size,
            'sha256': sha,
            'by': author,
            'at': when(rel),
            'role': (prev or {}).get('role') or ('作者' if author == OWNER else ''),
        }
        if prev:
            print('更新 %s → v%s（code %s → %s）' % (pid, ver, prev.get('code'), entry['code']))
            d['plugins'] = [entry if p.get('id') == pid else p for p in d['plugins']]
        else:
            print('新增 %s v%s（%d 字符，%s）' % (pid, ver, size, author))
            d['plugins'].append(entry)
        by_id[pid] = entry
        changed = True

    # 文件没了就是下架：清单里对应那一条一起摘掉（历史记录仍在 git 里）。
    gone = [p.get('id') for p in d['plugins'] if p.get('id') not in files]
    for pid in gone:
        print('下架 %s（文件已删除）' % pid)
        d['plugins'] = [p for p in d['plugins'] if p.get('id') != pid]
        changed = True

    if not changed:
        print('清单已是最新，无需改动')
        return 0
    if a.dry_run:
        print('（dry-run：未写入）')
        return 0
    save(d)
    print('已写入 plugins/index.json（共 %d 条）' % len(d['plugins']))
    return 0


if __name__ == '__main__':
    sys.exit(main())
