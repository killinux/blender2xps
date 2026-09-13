# -*- coding: utf-8 -*-
r"""Write an index README.md for a folder of Blender2XPS batch exports.

    python tools/write_export_index.py D:\vindictus_exports\xps [manifest.json]

Each sub folder <id>/<id>.xps is read with the independent reader; stats come
from the file itself, bake/skip counts from the export report.
"""
import importlib.util
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
spec = importlib.util.spec_from_file_location('xps_format', os.path.join(HERE, '..', 'blender2xps', 'xps_format.py'))
xf = importlib.util.module_from_spec(spec)
spec.loader.exec_module(xf)
spec2 = importlib.util.spec_from_file_location('bone_names', os.path.join(HERE, '..', 'blender2xps', 'bone_names.py'))
bn = importlib.util.module_from_spec(spec2)
spec2.loader.exec_module(bn)


def main():
    args = [a for a in sys.argv[1:] if not a.startswith('--')]
    root = args[0]
    names = {}
    if len(args) > 1 and os.path.isfile(args[1]):
        try:
            m = json.load(open(args[1], encoding='utf-8'))
            names = {r['label']: r.get('name', '') for r in m.get('results', [])}
        except Exception:
            pass
    xps_names = set(bn.XPS_NAMES.values())
    rows = []
    for d in sorted(os.listdir(root)):
        p = os.path.join(root, d, d + '.xps')
        if not os.path.isfile(p):
            continue
        model = xf.read_model_file(p)
        errors, warnings, stats = xf.verify_model(model)
        hidden = sum(1 for b in model.bones if b.name.lower().startswith('unused'))
        std = sum(1 for b in model.bones if b.name in xps_names)
        rep = ''
        rp = p + '.report.txt'
        if os.path.isfile(rp):
            rep = open(rp, encoding='utf-8').read()
        baked = len(re.findall(r'烘焙材质', rep))
        skipped = len(re.findall(r'跳过材质', rep))
        warn = len(re.findall(r'^警告', rep, re.M))
        size = sum(os.path.getsize(os.path.join(root, d, f)) for f in os.listdir(os.path.join(root, d)))
        rows.append((d, names.get(d, ''), stats['bones'], std, hidden, stats['meshes'], stats['vertices'],
                     stats['faces'], baked, skipped, warn, 'OK' if not errors else 'ERR', size / 1e6))

    out = []
    out.append('# XPS 导出索引')
    out.append('')
    out.append('由 Blender2XPS 批量导出。每个目录：`<id>.xps`（XPS 11.x 通用二进制 v2.15，每顶点 4 权重）'
               '+ 贴图 PNG + `<id>.xps.report.txt`。XPS 里 Modify > Load Generic Item 选 `<id>.xps` 即可。')
    out.append('')
    out.append('| 模型 | 说明 | 骨骼 | XPS标准名 | 隐藏(unused_) | 部件 | 顶点 | 三角面 | 烘焙材质 | 跳过材质 | 警告 | 校验 | 大小 MB |')
    out.append('|---|---|---|---|---|---|---|---|---|---|---|---|---|')
    for r in rows:
        out.append('| %s | %s | %d | %d | %d | %d | %d | %d | %d | %d | %d | %s | %.0f |' % r)
    out.append('')
    out.append('说明：')
    out.append('- 骨名已映射为 XPS 标准名（root ground / root hips / spine lower / arm left elbow / leg left knee ...），'
               '扭转骨、肌肉修正骨等辅助骨加了 `unused_` 前缀：XPS 骨骼列表里不显示，仍参与蒙皮；`FACIAL_*` 面部骨保留可见。')
    out.append('- 颜色/透明由节点算出的材质（头发、睫毛、眉毛、眼球、带色调的皮肤）已烘焙成 `*_baked.png`（带 alpha）。')
    out.append('- 半透明辅助壳（眼部遮蔽壳、泪线、假反射片）和源文件里隐藏的物体未导出。')
    out.append('- 重新导出：`blender -b --python tools/batch_export_blends.py -- --out <此目录> --scale 0.01 --bake AUTO <blend...>`')
    with open(os.path.join(root, 'README.md'), 'w', encoding='utf-8') as f:
        f.write('\n'.join(out) + '\n')
    print('\n'.join(out[4:]))


if __name__ == '__main__':
    main()
