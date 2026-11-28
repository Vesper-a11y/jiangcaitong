# -*- coding: utf-8 -*-
"""走 GitHub 接口提交当前工作区的改动（代理下 git push 走不通时的备用通道）。

用法：python _gh_push.py <subject> [body]
"""
import base64
import json
import os
import subprocess
import sys
import urllib.request

REPO = 'Vesper-a11y/jiangcaitong'
API = 'https://api.github.com/repos/' + REPO


def token():
    out = subprocess.run(
        ['git', 'credential', 'fill'],
        input='protocol=https\nhost=github.com\n\n',
        capture_output=True, text=True)
    for line in out.stdout.splitlines():
        if line.startswith('password='):
            return line[9:]
    sys.exit('拿不到 GitHub 凭据')


TOKEN = token()


def call(method, url, payload=None):
    data = None
    headers = {'Authorization': 'Bearer ' + TOKEN,
               'Accept': 'application/vnd.github+json',
               'User-Agent': 'jiangcaitong-push'}
    if payload is not None:
        data = json.dumps(payload, ensure_ascii=False).encode('utf-8')
        headers['Content-Type'] = 'application/json; charset=utf-8'
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=90) as r:
            return json.loads(r.read().decode('utf-8'))
    except urllib.error.HTTPError as e:
        sys.exit('%s %s -> %s %s' % (method, url, e.code, e.read().decode('utf-8', 'replace')[:400]))


def main():
    subject = sys.argv[1]
    body = sys.argv[2] if len(sys.argv) > 2 else ''
    message = subject + ('\n\n' + body if body else '')

    files = []
    st = subprocess.run(['git', 'status', '--porcelain'],
                        capture_output=True, text=True).stdout.splitlines()
    for line in st:
        if not line.strip() or line[:2].strip() == 'D':
            continue
        files.append(line[3:].strip().strip('"'))
    paths = [p for p in files if os.path.isfile(p)]
    if not paths:
        sys.exit('没有可提交的文件')
    print('将提交：', ', '.join(paths))

    ref = call('GET', API + '/git/ref/heads/main')
    base = ref['object']['sha']
    tree_sha = call('GET', API + '/git/commits/' + base)['tree']['sha']

    tree_items = []
    for p in paths:
        with open(p, 'rb') as f:
            raw = f.read()
        blob = call('POST', API + '/git/blobs', {
            'content': base64.b64encode(raw).decode('ascii'),
            'encoding': 'base64'})
        tree_items.append({'path': p.replace('\\', '/'), 'mode': '100644',
                           'type': 'blob', 'sha': blob['sha']})
        print('  blob', p, blob['sha'][:10])

    tree = call('POST', API + '/git/trees', {'base_tree': tree_sha, 'tree': tree_items})
    commit = call('POST', API + '/git/commits', {
        'message': message, 'tree': tree['sha'], 'parents': [base]})
    call('PATCH', API + '/git/refs/heads/main', {'sha': commit['sha'], 'force': False})
    print('已提交', commit['sha'][:7])
    print(commit['html_url'])


if __name__ == '__main__':
    main()
