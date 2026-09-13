# -*- coding: utf-8 -*-
"""Inspect / verify XPS model files without Blender.

    python tools/xpsdump.py model.xps [more files...]
    python tools/xpsdump.py --bones model.xps      # list every bone
    python tools/xpsdump.py --compare a.xps b.mesh.ascii   # numeric comparison
"""
import importlib.util
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_SPEC = importlib.util.spec_from_file_location('xps_format', os.path.join(_HERE, '..', 'blender2xps', 'xps_format.py'))
xf = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(xf)


def dump(path, list_bones=False):
    model = xf.read_model_file(path)
    print('==== %s (%d bytes)' % (path, os.path.getsize(path)))
    print(xf.describe_model(model))
    if list_bones:
        for i, b in enumerate(model.bones):
            print('  [%3d] %-32s parent=%-4d (%.4f, %.4f, %.4f)' % (i, b.name, b.parent, b.pos[0], b.pos[1], b.pos[2]))
    errors, warnings, stats = xf.verify_model(model)
    print('stats:', stats)
    for w in warnings:
        print('WARNING:', w)
    for e in errors:
        print('ERROR:', e)
    print('verify:', 'OK' if not errors else 'FAILED')
    return model, errors


def compare(a, b, tol=1e-4):
    ma = xf.read_model_file(a)
    mb = xf.read_model_file(b)
    ok = True
    if len(ma.bones) != len(mb.bones):
        print('bone count differs', len(ma.bones), len(mb.bones))
        ok = False
    for i, (x, y) in enumerate(zip(ma.bones, mb.bones)):
        if x.name != y.name or x.parent != y.parent or max(abs(p - q) for p, q in zip(x.pos, y.pos)) > tol:
            print('bone %d differs: %s vs %s' % (i, (x.name, x.parent, x.pos), (y.name, y.parent, y.pos)))
            ok = False
            break
    if len(ma.meshes) != len(mb.meshes):
        print('mesh count differs', len(ma.meshes), len(mb.meshes))
        ok = False
    for x, y in zip(ma.meshes, mb.meshes):
        if x.name != y.name or x.vertex_count != y.vertex_count or len(x.faces) != len(y.faces):
            print('mesh differs: %s(%d/%d) vs %s(%d/%d)' % (x.name, x.vertex_count, len(x.faces), y.name, y.vertex_count, len(y.faces)))
            ok = False
            continue
        for i in range(x.vertex_count):
            if max(abs(p - q) for p, q in zip(x.positions[i], y.positions[i])) > tol:
                print('mesh %s vertex %d position differs' % (x.name, i))
                ok = False
                break
            wa = sorted((b, w) for b, w in zip(x.bone_ids[i], x.bone_weights[i]) if w > 0)
            wb = sorted((b, w) for b, w in zip(y.bone_ids[i], y.bone_weights[i]) if w > 0)
            if [b for b, _w in wa] != [b for b, _w in wb] or any(abs(p - q) > 1e-3 for (_a, p), (_b, q) in zip(wa, wb)):
                print('mesh %s vertex %d weights differ: %s vs %s' % (x.name, i, wa, wb))
                ok = False
                break
    print('compare:', 'EQUAL' if ok else 'DIFFERENT')
    return ok


if __name__ == '__main__':
    args = sys.argv[1:]
    if not args:
        print(__doc__)
        sys.exit(1)
    if args[0] == '--compare':
        sys.exit(0 if compare(args[1], args[2]) else 1)
    list_bones = False
    if args[0] == '--bones':
        list_bones = True
        args = args[1:]
    failed = False
    for p in args:
        try:
            _m, errors = dump(p, list_bones)
            failed = failed or bool(errors)
        except Exception as exc:
            print('ERROR reading %s: %s' % (p, exc))
            failed = True
    sys.exit(1 if failed else 0)
