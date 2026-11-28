#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""把已发布的 GitHub Release 复制到 Gitee：建标签、建 Release、传附件。

只在手动触发时运行（见 .github/workflows/gitee-release.yml），不做自动发版。
所需环境变量：
  GITEE_TOKEN   Gitee 私人令牌（projects 权限），来自仓库 Secret
  GH_REPO       形如 Vesper-a11y/jiangcaitong
  GITEE_OWNER   形如 vesper-a11y
  GITEE_REPO    形如 jiangcaitong
  TAG           形如 v1.0.4
  VERSION       形如 1.0.4
"""
import json
import os
import subprocess
import sys
import tempfile
import urllib.error
import urllib.parse
import urllib.request

TOKEN = os.environ['GITEE_TOKEN']
GH_REPO = os.environ['GH_REPO']
OWNER = os.environ['GITEE_OWNER']
REPO = os.environ['GITEE_REPO']
TAG = os.environ['TAG']
VERSION = os.environ['VERSION']

API = 'https://gitee.com/api/v5/repos/%s/%s' % (OWNER, REPO)
APK = 'jiangcaitong-%s.apk' % VERSION


def log(msg):
    print(msg, flush=True)


def call(method, url, fields=None):
    """form-encoded 调用 Gitee API，返回 (状态码, 解析后的 JSON 或原文)。"""
    data = None
    headers = {'Accept': 'application/json'}
    if fields is not None:
        fields = dict(fields)
        fields['access_token'] = TOKEN
        data = urllib.parse.urlencode(fields).encode('utf-8')
        headers['Content-Type'] = 'application/x-www-form-urlencoded'
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            raw = r.read().decode('utf-8', 'replace')
            code = r.getcode()
    except urllib.error.HTTPError as e:
        raw = e.read().decode('utf-8', 'replace')
        code = e.code
    try:
        return code, json.loads(raw)
    except ValueError:
        return code, raw


def ensure_tag(commitish):
    code, body = call('POST', API + '/git/tags', {
        'tag_name': TAG,
        'refs': 'refs/heads/main',
        'tag_message': 'release(%s): 发布 %s 版本' % (VERSION, VERSION),
    })
    if code in (200, 201):
        log('已创建 Gitee 标签 %s（指向 %s）' % (TAG, commitish))
        return
    if isinstance(body, dict) and '已存在' in str(body.get('message', '')):
        log('Gitee 标签 %s 已存在，跳过创建' % TAG)
        return
    log('创建标签返回 %s：%s（继续尝试发布）' % (code, body))


def find_release():
    page = 1
    while page <= 10:
        code, body = call('GET', API + '/releases?per_page=50&page=%d' % page)
        if code != 200 or not isinstance(body, list):
            log('读取 Release 列表失败：%s' % body)
            return None
        for r in body:
            if r.get('tag_name') == TAG:
                return r
        if len(body) < 50:
            return None
        page += 1
    return None


def create_release(body_text):
    fields = {
        'tag_name': TAG,
        'name': '%s %s' % (VERSION, '版本发布'),
        'body': body_text,
        'target_commitish': 'main',
        'prerelease': 'false',
    }
    code, body = call('POST', API + '/releases', fields)
    if code in (200, 201):
        log('已创建 Gitee Release：%s' % body.get('html_url'))
        return body
    log('创建 Release 失败 %s：%s' % (code, body))
    return None


def upload(release_id, path, name):
    url = '%s/releases/%s/attach_files' % (API, release_id)
    out = subprocess.run(
        ['curl', '-sS', '-m', '300', '-X', 'POST',
         '-F', 'file=@%s;filename=%s' % (path, name),
         '%s?access_token=%s' % (url, urllib.parse.quote(TOKEN, safe=''))],
        capture_output=True, text=True)
    text = (out.stdout or '').strip()
    try:
        data = json.loads(text)
    except ValueError:
        data = None
    if isinstance(data, dict) and (data.get('name') or data.get('id')):
        log('已上传 %s' % name)
        return True
    log('上传 %s 失败：%s%s' % (name, text[:300], out.stderr[:200]))
    return False


def download(url, path):
    req = urllib.request.Request(url, headers={'Accept': '*/*'})
    with urllib.request.urlopen(req, timeout=300) as r, open(path, 'wb') as f:
        while True:
            chunk = r.read(65536)
            if not chunk:
                break
            f.write(chunk)


def main():
    tmp = tempfile.mkdtemp(prefix='gitee-rel-')
    apk_path = os.path.join(tmp, APK)
    log('从 GitHub 取安装包：%s' % APK)
    download('https://github.com/%s/releases/download/%s/%s' % (GH_REPO, TAG, APK), apk_path)
    log('安装包大小 %d 字节' % os.path.getsize(apk_path))

    with open('version.json', encoding='utf-8') as f:
        manifest = json.load(f)
    manifest['downloadUrl'] = 'https://gitee.com/%s/%s/releases/download/%s/%s' % (
        OWNER, REPO, TAG, APK)
    json_path = os.path.join(tmp, 'version.json')
    with open(json_path, 'w', encoding='utf-8') as f:
        json.dump(manifest, f, ensure_ascii=False, indent=2)
        f.write('\n')

    lines = ['## %s' % VERSION, '']
    for n in manifest.get('changelog', []):
        lines.append('- ' + n)
    lines += ['', '**下载**：`%s`（Android 8.0 及以上）' % APK]
    body_text = '\n'.join(lines)

    rel = find_release()
    if rel:
        log('Gitee 上已有 %s 的 Release：%s' % (TAG, rel.get('html_url')))
    else:
        ensure_tag('main')
        rel = create_release(body_text)
        if not rel:
            sys.exit(1)

    rel_id = rel['id']
    have = {a.get('name') for a in (rel.get('assets') or [])}
    ok = True
    for path, name in ((apk_path, APK), (json_path, 'version.json')):
        if name in have:
            log('%s 已在 Gitee 上，跳过' % name)
            continue
        ok = upload(rel_id, path, name) and ok

    url = 'https://gitee.com/%s/%s/releases/download/%s/%s' % (OWNER, REPO, TAG, APK)
    log('核对附件地址：%s' % url)
    head = subprocess.run(
        ['curl', '-sS', '-o', '/dev/null', '-w', '%{http_code} %{size_download}',
         '-L', '-r', '0-2000', url], capture_output=True, text=True)
    log('返回：%s' % (head.stdout or head.stderr).strip())
    if not ok:
        sys.exit(1)


if __name__ == '__main__':
    main()
