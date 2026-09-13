# -*- coding: utf-8 -*-
"""Batch export .blend files to XPS with Blender2XPS (headless).

    blender -b --python tools/batch_export_blends.py -- --out <root> [--scale 0.01] [--scope VISIBLE]
            [--bake AUTO|ALL|OFF] [--hide-facial] [--no-facing] [--fmt AUTO] model1.blend model2.blend ...

Each blend is exported to <root>/<blend name>/<blend name>.xps (+ textures + report).
A JSON summary line "BLENDER2XPS_BATCH={...}" is printed per file so a driver
script can collect results.  One Blender process can handle several files;
run several processes in parallel for speed.
"""
import json
import os
import sys
import time
import traceback

import bpy

REPO = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))
if REPO not in sys.path:
    sys.path.insert(0, REPO)

argv = sys.argv[sys.argv.index('--') + 1:] if '--' in sys.argv else []
opts = {'out': '', 'scale': 1.0, 'scope': 'VISIBLE', 'bake': 'AUTO', 'hide_facial': False, 'fmt': 'AUTO',
        'bone_naming': 'XPS', 'max_weights': 4, 'bake_size': 0, 'facing': True}
files = []
i = 0
while i < len(argv):
    a = argv[i]
    if a == '--out':
        opts['out'] = argv[i + 1]
        i += 2
    elif a == '--scale':
        opts['scale'] = float(argv[i + 1])
        i += 2
    elif a == '--scope':
        opts['scope'] = argv[i + 1]
        i += 2
    elif a == '--bake':
        opts['bake'] = argv[i + 1]
        i += 2
    elif a == '--bake-size':
        opts['bake_size'] = int(argv[i + 1])
        i += 2
    elif a == '--fmt':
        opts['fmt'] = argv[i + 1]
        i += 2
    elif a == '--bone-naming':
        opts['bone_naming'] = argv[i + 1]
        i += 2
    elif a == '--max-weights':
        opts['max_weights'] = int(argv[i + 1])
        i += 2
    elif a == '--hide-facial':
        opts['hide_facial'] = True
        i += 1
    elif a == '--no-facing':
        opts['facing'] = False
        i += 1
    else:
        files.append(a)
        i += 1
if not files or not opts['out']:
    print(__doc__)
    raise SystemExit(2)

from blender2xps import export_xps   # noqa: E402

for blend in files:
    t0 = time.time()
    name = os.path.splitext(os.path.basename(blend))[0]
    out_dir = os.path.join(opts['out'], name)
    summary = {'blend': blend, 'name': name, 'ok': False}
    try:
        bpy.ops.wm.open_mainfile(filepath=blend, load_ui=False)
        settings = export_xps.Settings(
            filepath=os.path.join(out_dir, name + '.xps'), fmt=opts['fmt'], scope=opts['scope'],
            scale=opts['scale'], bake_mode=opts['bake'], bake_size=opts['bake_size'],
            hide_facial_bones=opts['hide_facial'], bone_naming=opts['bone_naming'],
            max_weights=opts['max_weights'], auto_facing=opts['facing'])
        res = export_xps.export_model(bpy.context, settings)
        summary.update({'ok': not res.errors, 'path': res.path, 'fmt': res.fmt, 'bones': res.bone_count,
                        'meshes': res.mesh_count, 'stats': res.stats, 'warnings': res.warnings,
                        'errors': res.errors, 'seconds': round(time.time() - t0, 1)})
    except Exception as exc:
        summary['errors'] = [str(exc)]
        summary['traceback'] = traceback.format_exc()
        print(summary['traceback'])
    print('BLENDER2XPS_BATCH=' + json.dumps(summary, ensure_ascii=False))
    sys.stdout.flush()
