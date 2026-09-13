# -*- coding: utf-8 -*-
"""End-to-end test, run inside Blender:

    blender -b --python tests/test_export_headless.py -- --out <dir> [--no-roundtrip]

Builds a synthetic rig (transformed armature, mirrored multi-material mesh,
messy weights), exports it in every format, re-reads the files with the
independent reader and checks bones, vertices, weights, UVs and textures.
If XNALaraMesh is installed the .xps file is imported back and compared too.
"""
import math
import os
import sys
import tempfile

import bpy
from mathutils import Matrix, Vector

REPO = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))
if REPO not in sys.path:
    sys.path.insert(0, REPO)

argv = sys.argv[sys.argv.index('--') + 1:] if '--' in sys.argv else []
OUT = None
ROUNDTRIP = True
i = 0
while i < len(argv):
    if argv[i] == '--out':
        OUT = argv[i + 1]
        i += 2
    elif argv[i] == '--no-roundtrip':
        ROUNDTRIP = False
        i += 1
    else:
        i += 1
if OUT is None:
    OUT = os.path.join(tempfile.gettempdir(), 'blender2xps_test')
os.makedirs(OUT, exist_ok=True)

import blender2xps
from blender2xps import export_xps, xps_format as xf, bone_names as bn

try:
    blender2xps.register()
except Exception:
    pass

failures = []


def check(cond, msg):
    if cond:
        print('  ok  -', msg)
    else:
        print('  FAIL-', msg)
        failures.append(msg)


def close(a, b, tol=1e-4):
    return max(abs(x - y) for x, y in zip(a, b)) <= tol


# --------------------------------------------------------------------------- scene

def build_scene():
    bpy.ops.wm.read_factory_settings(use_empty=True)
    scene = bpy.context.scene

    root_empty = bpy.data.objects.new('Root', None)
    scene.collection.objects.link(root_empty)
    root_empty.location = (0.0, 0.0, 0.3)

    arm_data = bpy.data.armatures.new('Arm')
    arm = bpy.data.objects.new('Arm', arm_data)
    scene.collection.objects.link(arm)
    arm.parent = root_empty
    arm.location = (1.0, 2.0, 0.0)
    arm.rotation_euler = (0.0, 0.0, math.radians(30))
    arm.scale = (1.5, 1.5, 1.5)

    bpy.context.view_layer.objects.active = arm
    bpy.ops.object.mode_set(mode='EDIT')
    eb = arm_data.edit_bones
    b_root = eb.new('全ての親')
    b_root.head, b_root.tail = (0, 0, 0), (0, 0, 0.2)
    b_hips = eb.new('センター')
    b_hips.head, b_hips.tail = (0, 0, 0.9), (0, 0, 1.1)
    b_hips.parent = b_root
    b_spine = eb.new('上半身')
    b_spine.head, b_spine.tail = (0, 0, 1.1), (0, 0, 1.4)
    b_spine.parent = b_hips
    b_arm_l = eb.new('腕.L')
    b_arm_l.head, b_arm_l.tail = (0.2, 0, 1.4), (0.6, 0, 1.4)
    b_arm_l.parent = b_spine
    b_arm_r = eb.new('腕.R')
    b_arm_r.head, b_arm_r.tail = (-0.2, 0, 1.4), (-0.6, 0, 1.4)
    b_arm_r.parent = b_spine
    b_twist = eb.new('腕捩.L')        # helper: will be merged into 腕.L
    b_twist.head, b_twist.tail = (0.4, 0, 1.4), (0.5, 0, 1.4)
    b_twist.parent = b_arm_l
    b_dummy = eb.new('_dummy_腕捩.L')  # mmd_tools internal: will be dropped
    b_dummy.head, b_dummy.tail = (0.4, 0, 1.5), (0.5, 0, 1.5)
    b_dummy.parent = b_twist
    b_leg = eb.new('足D.L')            # D bone merged into 足.L
    b_leg.head, b_leg.tail = (0.1, 0, 0.9), (0.1, 0, 0.5)
    b_leg.parent = b_hips
    b_leg_main = eb.new('足.L')
    b_leg_main.head, b_leg_main.tail = (0.1, 0, 0.9), (0.1, 0, 0.5)
    b_leg_main.parent = b_hips
    b_toe = eb.new('足先EX.L')          # child of the merged bone -> re-parented to 足.L
    b_toe.head, b_toe.tail = (0.1, 0, 0.1), (0.1, -0.1, 0.1)
    b_toe.parent = b_leg
    bpy.ops.object.mode_set(mode='OBJECT')
    arm_data.bones['腕捩.L'].use_deform = False

    # mesh: 3x3 grid of quads on the XZ plane, then mirrored by a modifier
    me = bpy.data.meshes.new('Body')
    verts = []
    for iy in range(4):
        for ix in range(4):
            verts.append((0.1 + ix * 0.15, 0.0, 0.2 + iy * 0.4))
    faces = []
    for iy in range(3):
        for ix in range(3):
            a = iy * 4 + ix
            faces.append((a, a + 1, a + 5, a + 4))
    me.from_pydata(verts, [], faces)
    me.update()
    obj = bpy.data.objects.new('Body', me)
    scene.collection.objects.link(obj)
    obj.parent = arm
    obj.location = (0.0, 0.5, 0.0)
    mod = obj.modifiers.new('Armature', 'ARMATURE')
    mod.object = arm
    mir = obj.modifiers.new('Mirror', 'MIRROR')
    mir.use_axis[0] = True
    mir.use_mirror_merge = False

    # uv
    uv = me.uv_layers.new(name='UVMap')
    for poly in me.polygons:
        for li in poly.loop_indices:
            v = me.vertices[me.loops[li].vertex_index].co
            uv.data[li].uv = (v.x, v.z / 2.0)

    # materials: one textured, one without nodes
    img = bpy.data.images.new('checker', 8, 8, alpha=True)
    px = []
    for k in range(64):
        c = 1.0 if (k // 8 + k % 8) % 2 == 0 else 0.2
        px.extend((c, c, c, 1.0))
    img.pixels = px
    img_path = os.path.join(OUT, 'src_checker.png')
    img.filepath_raw = img_path
    img.file_format = 'PNG'
    img.save()
    img.source = 'FILE'
    img.filepath = img_path
    mat_a = bpy.data.materials.new('MatA')
    mat_a.use_nodes = True
    bsdf = mat_a.node_tree.nodes['Principled BSDF']
    tex = mat_a.node_tree.nodes.new('ShaderNodeTexImage')
    tex.image = img
    mat_a.node_tree.links.new(tex.outputs['Color'], bsdf.inputs['Base Color'])
    mat_b = bpy.data.materials.new('材质B')
    mat_b.use_nodes = False
    mat_b.diffuse_color = (1.0, 0.0, 0.0, 1.0)
    me.materials.append(mat_a)
    me.materials.append(mat_b)
    for poly in me.polygons:
        poly.material_index = 1 if poly.index >= 6 else 0
    me.polygons[0].use_smooth = True     # mixed smooth / flat

    # vertex groups with deliberately messy weights
    vg = {name: obj.vertex_groups.new(name=name) for name in
          ('全ての親', 'センター', '上半身', '腕.L', '腕.R', '腕捩.L', '足D.L', 'not_a_bone', 'mmd_edge_scale')}
    n = len(me.vertices)
    for vi in range(n):
        vg['mmd_edge_scale'].add([vi], 1.0, 'REPLACE')       # ignored (not a bone)
    vg['センター'].add([0], 0.40, 'REPLACE')                 # vertex 0: 5 bone weights -> truncated to 4
    vg['上半身'].add([0], 0.30, 'REPLACE')
    vg['腕.L'].add([0], 0.15, 'REPLACE')
    vg['腕.R'].add([0], 0.10, 'REPLACE')
    vg['全ての親'].add([0], 0.05, 'REPLACE')
    # vertex 1: no weights at all
    vg['上半身'].add([2], 0.25, 'REPLACE')                    # vertex 2: sum 0.5 -> normalised
    vg['腕.L'].add([2], 0.25, 'REPLACE')
    vg['腕捩.L'].add([3], 0.6, 'REPLACE')                     # vertex 3: helper + main -> merged
    vg['腕.L'].add([3], 0.4, 'REPLACE')
    vg['足D.L'].add([4], 1.0, 'REPLACE')                      # vertex 4: D bone -> 足.L
    vg['not_a_bone'].add([5], 1.0, 'REPLACE')                 # vertex 5: only a non-bone group -> unweighted
    for vi in range(6, n):
        vg['上半身'].add([vi], 1.0, 'REPLACE')

    # pose the armature (must not influence a rest export)
    arm.pose.bones['上半身'].rotation_mode = 'XYZ'
    arm.pose.bones['上半身'].rotation_euler = (0.0, 0.0, math.radians(45))
    return arm, obj, img_path


def expected_bone_world(arm, name):
    return arm.matrix_world @ arm.data.bones[name].head_local


def run():
    arm, obj, img_path = build_scene()
    bpy.context.view_layer.update()

    # evaluated (rest) mesh in world space for expectations
    dg = bpy.context.evaluated_depsgraph_get()
    mods = {m.name: m.show_viewport for m in obj.modifiers}
    obj.modifiers['Armature'].show_viewport = False
    bpy.context.view_layer.update()
    dg = bpy.context.evaluated_depsgraph_get()
    ev = obj.evaluated_get(dg).to_mesh()
    world_pts = [obj.matrix_world @ v.co for v in ev.vertices]
    n_eval_verts = len(ev.vertices)
    n_eval_tris = sum(len(p.vertices) - 2 for p in ev.polygons)
    obj.evaluated_get(dg).to_mesh_clear()
    obj.modifiers['Armature'].show_viewport = True

    bpy.context.view_layer.objects.active = arm
    for o in bpy.context.view_layer.objects:
        o.select_set(False)
    arm.select_set(True)

    results = {}
    for fmt in ('XPS3', 'XPS2', 'MESH', 'ASCII'):
        print('\n--- export %s' % fmt)
        path = os.path.join(OUT, 'test_%s.xps' % fmt.lower())
        settings = export_xps.Settings(filepath=path, fmt=fmt, scope='ARMATURE', bone_naming='XPS',
                                       max_weights=4, copy_textures=True, alpha_mode='AUTO')
        res = export_xps.export_model(bpy.context, settings)
        check(not res.errors, '%s: no verification errors %s' % (fmt, res.errors))
        model = xf.read_model_file(res.path)
        results[fmt] = (res, model)
        check(model.fmt == fmt, '%s: reader detects format (%s)' % (fmt, model.fmt))

        # bones ---------------------------------------------------------
        names = [b.name for b in model.bones]
        check(len(model.bones) == 7, '%s: 7 bones exported (10 - merged 腕捩.L, 足D.L - dropped _dummy_) got %d %s' % (fmt, len(model.bones), names))
        check('root ground' in names and 'root hips' in names and 'spine lower' in names, '%s: XPS standard names applied %s' % (fmt, names))
        check('arm left shoulder 2' in names and 'arm right shoulder 2' in names, '%s: .L/.R names mapped' % fmt)
        check('leg left thigh' in names and 'leg left toes' in names, '%s: 足.L / 足先EX.L mapped' % fmt)
        idx = {b.name: i for i, b in enumerate(model.bones)}
        toe = model.bones[idx['leg left toes']]
        check(toe.parent == idx['leg left thigh'], '%s: child of merged bone re-parented to merge target' % fmt)
        for i, b in enumerate(model.bones):
            check(b.parent < i, '%s: parent before child for %s' % (fmt, b.name))
        exp = expected_bone_world(arm, '腕.L')
        got = model.bones[idx['arm left shoulder 2']].pos
        check(close(got, (exp.x, exp.z, -exp.y)), '%s: bone position world transformed (%s vs %s)' % (fmt, tuple(round(v, 4) for v in got), (round(exp.x, 4), round(exp.z, 4), round(-exp.y, 4))))

        # meshes --------------------------------------------------------
        check(len(model.meshes) == 2, '%s: two material parts, got %s' % (fmt, [m.name for m in model.meshes]))
        total_faces = sum(len(m.faces) for m in model.meshes)
        check(total_faces == n_eval_tris, '%s: triangle count %d == evaluated %d (mirror applied)' % (fmt, total_faces, n_eval_tris))
        for m in model.meshes:
            check(m.name.split('_')[0].isdigit(), '%s: part name has render group: %s' % (fmt, m.name))
            check(m.name.isascii(), '%s: ascii part name: %s' % (fmt, m.name))
            check(all(max(f) < m.vertex_count for f in m.faces), '%s: face indices valid in %s' % (fmt, m.name))
            sums = [sum(w for w in ws if w > 0) for ws in m.bone_weights]
            check(all(abs(s - 1.0) < 1e-4 for s in sums), '%s: all weights normalised in %s' % (fmt, m.name))
            check(all(sum(1 for w in ws if w > 0) <= 4 for ws in m.bone_weights), '%s: max 4 weights in %s' % (fmt, m.name))
            check(all(t.file and t.file != 'missing.png' for t in m.textures), '%s: textures resolved %s' % (fmt, [t.file for t in m.textures]))
            for t in m.textures:
                check(os.path.isfile(os.path.join(OUT, t.file)), '%s: texture file written %s' % (fmt, t.file))
        # world position of a specific Blender vertex must appear in the file
        p = world_pts[7]
        target = (p.x, p.z, -p.y)
        found = None
        for m in model.meshes:
            for i, q in enumerate(m.positions):
                if close(q, target, 1e-3):
                    found = (m, i)
                    break
            if found:
                break
        check(found is not None, '%s: vertex 7 world position present in file' % fmt)
        if found:
            m, i = found
            uvs = m.uvs[i][0]
            check(0.0 <= uvs[0] <= 1.0 and 0.0 <= uvs[1] <= 1.0, '%s: uv in range %s' % (fmt, uvs))
            ws = {b: w for b, w in zip(m.bone_ids[i], m.bone_weights[i]) if w > 0}
            check(abs(ws.get(idx['spine lower'], 0.0) - 1.0) < 1e-5, '%s: vertex 7 weighted 1.0 to spine lower' % fmt)
        # unweighted vertex 1 -> root ground
        p = world_pts[1]
        target = (p.x, p.z, -p.y)
        for m in model.meshes:
            for i, q in enumerate(m.positions):
                if close(q, target, 1e-3):
                    ws = {b: w for b, w in zip(m.bone_ids[i], m.bone_weights[i]) if w > 0}
                    check(abs(ws.get(idx['root ground'], 0.0) - 1.0) < 1e-5, '%s: unweighted vertex bound to root ground' % fmt)
                    break
        # vertex 0: five weights truncated to four, normalised
        p = world_pts[0]
        target = (p.x, p.z, -p.y)
        for m in model.meshes:
            for i, q in enumerate(m.positions):
                if close(q, target, 1e-3):
                    active = [(b, w) for b, w in zip(m.bone_ids[i], m.bone_weights[i]) if w > 0]
                    check(len(active) == 4, '%s: vertex 0 truncated to 4 weights (%s)' % (fmt, active))
                    top = max(active, key=lambda t: t[1])
                    check(top[0] == idx['root hips'] and abs(top[1] - 0.40 / 0.95) < 1e-4, '%s: vertex 0 biggest weight renormalised' % fmt)
                    break
        # vertex 3: helper twist merged into 腕.L
        p = world_pts[3]
        target = (p.x, p.z, -p.y)
        for m in model.meshes:
            for i, q in enumerate(m.positions):
                if close(q, target, 1e-3):
                    ws = {b: w for b, w in zip(m.bone_ids[i], m.bone_weights[i]) if w > 0}
                    check(abs(ws.get(idx['arm left shoulder 2'], 0.0) - 1.0) < 1e-5, '%s: 腕捩.L weight merged into 腕.L' % fmt)
                    break
        # vertex 4: D bone merged
        p = world_pts[4]
        target = (p.x, p.z, -p.y)
        for m in model.meshes:
            for i, q in enumerate(m.positions):
                if close(q, target, 1e-3):
                    ws = {b: w for b, w in zip(m.bone_ids[i], m.bone_weights[i]) if w > 0}
                    check(abs(ws.get(idx['leg left thigh'], 0.0) - 1.0) < 1e-5, '%s: 足D.L weight merged into 足.L' % fmt)
                    break
        if fmt == 'MESH':
            check(model.meshes[0].tangents is not None, 'MESH: tangents present')

    # formats agree with each other
    a = results['XPS3'][1]
    for fmt in ('XPS2', 'MESH', 'ASCII'):
        b = results[fmt][1]
        same = len(a.bones) == len(b.bones) and all(
            x.name == y.name and x.parent == y.parent and close(x.pos, y.pos, 1e-4) for x, y in zip(a.bones, b.bones))
        check(same, 'XPS3 and %s: identical bones' % fmt)
        same = all(x.vertex_count == y.vertex_count and all(close(p, q, 1e-4) for p, q in zip(x.positions, y.positions))
                   for x, y in zip(a.meshes, b.meshes))
        check(same, 'XPS3 and %s: identical vertices' % fmt)

    # bake pose mode moves bones + mesh consistently
    path = os.path.join(OUT, 'test_posed.xps')
    res = export_xps.export_model(bpy.context, export_xps.Settings(filepath=path, fmt='XPS2', bake_pose=True))
    posed = xf.read_model_file(res.path)
    pidx = {b.name: i for i, b in enumerate(posed.bones)}
    exp = arm.matrix_world @ arm.pose.bones['腕.L'].head
    got = posed.bones[pidx['arm left shoulder 2']].pos
    check(close(got, (exp.x, exp.z, -exp.y), 1e-4), 'bake_pose: posed bone position exported')
    rest = results['XPS2'][1].bones[idx['arm left shoulder 2']].pos
    check(not close(got, rest, 1e-3), 'bake_pose: differs from rest export')

    # KEEP names + ascii fallback
    path = os.path.join(OUT, 'test_keep.xps')
    res = export_xps.export_model(bpy.context, export_xps.Settings(filepath=path, fmt='XPS2', bone_naming='KEEP', ascii_bone_names=True, mmd_merge_helpers=False, drop_internal_bones=False))
    keep = xf.read_model_file(res.path)
    names = [b.name for b in keep.bones]
    check(len(keep.bones) == 10, 'KEEP: all 10 bones kept %s' % names)
    check(all(n.isascii() for n in names), 'KEEP+ascii: romanised names %s' % names)

    # XNALaraMesh round trip -----------------------------------------------------
    if ROUNDTRIP:
        enabled = False
        for module in ('XNALaraMesh-master', 'XNALaraMesh'):
            try:
                bpy.ops.preferences.addon_enable(module=module)
                enabled = hasattr(bpy.ops, 'xps_tools') and hasattr(bpy.ops.xps_tools, 'import_model')
                if enabled:
                    break
            except Exception:
                continue
        if enabled:
            before = set(o.name for o in bpy.data.objects)
            bpy.ops.xps_tools.import_model(filepath=results['XPS2'][0].path)
            new = [o for o in bpy.data.objects if o.name not in before]
            imp_arm = next((o for o in new if o.type == 'ARMATURE'), None)
            imp_meshes = [o for o in new if o.type == 'MESH']
            check(imp_arm is not None, 'roundtrip: XNALaraMesh imported an armature')
            check(len(imp_meshes) == 2, 'roundtrip: two meshes imported')
            if imp_arm is not None:
                for name, orig in (('arm left shoulder 2', '腕.L'), ('root hips', 'センター'), ('leg left toes', '足先EX.L')):
                    ib = imp_arm.data.bones.get(name)
                    exp = expected_bone_world(arm, orig)
                    check(ib is not None and close(imp_arm.matrix_world @ ib.head_local, exp, 1e-3),
                          'roundtrip: bone %s head matches original world head' % name)
            if imp_meshes:
                pts = []
                for o in imp_meshes:
                    pts.extend(o.matrix_world @ v.co for v in o.data.vertices)
                lo = Vector((min(p.x for p in pts), min(p.y for p in pts), min(p.z for p in pts)))
                hi = Vector((max(p.x for p in pts), max(p.y for p in pts), max(p.z for p in pts)))
                olo = Vector((min(p.x for p in world_pts), min(p.y for p in world_pts), min(p.z for p in world_pts)))
                ohi = Vector((max(p.x for p in world_pts), max(p.y for p in world_pts), max(p.z for p in world_pts)))
                check(close(lo, olo, 1e-3) and close(hi, ohi, 1e-3), 'roundtrip: mesh bounding box matches (%s..%s vs %s..%s)' % (tuple(lo), tuple(hi), tuple(olo), tuple(ohi)))
                target = world_pts[7]
                best = None
                for o in imp_meshes:
                    for v in o.data.vertices:
                        d = ((o.matrix_world @ v.co) - target).length
                        if best is None or d < best[0]:
                            best = (d, o, v)
                d, o, v = best
                groups = {o.vertex_groups[g.group].name: g.weight for g in v.groups}
                check(d < 1e-3 and abs(groups.get('spine lower', 0.0) - 1.0) < 1e-4, 'roundtrip: vertex 7 weight 1.0 on spine lower after re-import (%s)' % groups)
        else:
            print('  skip- XNALaraMesh not available, round trip skipped')

    print('\n%d checks failed' % len(failures))
    for f in failures:
        print('   ', f)
    if failures:
        raise SystemExit(1)
    print('ALL TESTS PASSED')


run()
