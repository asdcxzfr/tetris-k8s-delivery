#!/usr/bin/env python3
"""把 kubectl -o yaml 的输出清洗成可复现的清单:
   去掉 managedFields / resourceVersion / uid / creationTimestamp / generation /
   status / ownerReferences 等运行时噪声。

   ★ 关键: 对 YAML 1.1 里有歧义的字符串强制加引号。
     PyYAML 刻意不把单字母 y/Y/n/N 解析为 bool (为了避免 "Norway problem"),
     所以 dump 时不会给它们加引号; 但 Kubernetes 用的 go-yaml 是完整 YAML 1.1,
     会把裸写的 N 当成 false  ->  apply 时报 "env.name must be of type string: boolean"。
     这里显式覆盖 str 的 representer 来堵住这个坑。
"""
import re
import sys

import yaml

AMBIGUOUS = re.compile(
    r'^(y|Y|yes|Yes|YES|n|N|no|No|NO|on|On|ON|off|Off|OFF'
    r'|true|True|TRUE|false|False|FALSE|null|Null|NULL|~)$')

SKIP_ANN_PREFIX = ('kubectl.kubernetes.io/', 'deployment.kubernetes.io/',
                   'rollout.argoproj.io/revision')


class QuotedDumper(yaml.SafeDumper):
    pass


def _str_representer(dumper, data):
    if data == '' or AMBIGUOUS.match(data):
        return dumper.represent_scalar('tag:yaml.org,2002:str', data, style="'")
    return dumper.represent_scalar('tag:yaml.org,2002:str', data)


QuotedDumper.add_representer(str, _str_representer)


def clean(o):
    m = o.get('metadata') or {}
    for k in ('managedFields', 'resourceVersion', 'uid', 'creationTimestamp',
              'generation', 'selfLink', 'ownerReferences', 'finalizers'):
        m.pop(k, None)
    ann = m.get('annotations') or {}
    for k in list(ann):
        if k.startswith(SKIP_ANN_PREFIX):
            ann.pop(k, None)
    if not ann:
        m.pop('annotations', None)
    o.pop('status', None)
    return o


if __name__ == '__main__':
    src, dst = sys.argv[1], sys.argv[2]
    docs = [d for d in yaml.safe_load_all(open(src)) if d]
    with open(dst, 'w') as f:
        yaml.dump_all([clean(d) for d in docs], f, Dumper=QuotedDumper,
                      default_flow_style=False, allow_unicode=True,
                      sort_keys=False, width=200)
    print('cleaned %d doc(s) -> %s' % (len(docs), dst))
