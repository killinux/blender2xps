# -*- coding: utf-8 -*-
"""Blender scene -> XPS model.  This is the exporter core; the UI in
__init__.py only builds a Settings object and calls export_model()."""

import os
import re
import time

import bpy
import numpy as np
import math

from mathutils import Matrix, Vector, kdtree

from . import xps_format as xf
from . import bone_names as bn
from . import materials as mt


class ExportError(Exception):
    pass


class Settings(object):
    """Plain container mirroring the operator properties."""

    def __init__(self, **kw):
        self.filepath = ''
        self.fmt = 'AUTO'                 # AUTO / XPS3 / XPS2 / MESH / ASCII
        self.scope = 'ARMATURE'           # ARMATURE / SELECTED / VISIBLE
        self.visible_only = True
        self.armature_name = ''
        self.apply_modifiers = True
        self.bake_pose = False
        self.scale = 1.0
        self.max_weights = 4              # 0 = unlimited
        self.weight_threshold = 1e-4
        self.unweighted = 'ROOT'          # ROOT / NEAREST / KEEP
        self.bone_naming = 'XPS'          # XPS / KEEP
        self.ascii_bone_names = False
        self.mmd_merge_helpers = True
        self.drop_internal_bones = True
        self.sort_bones = True
        self.ascii_mesh_names = True
        self.copy_textures = True
        self.alpha_mode = 'AUTO'          # AUTO / NEVER / ALWAYS
        self.unlit = False
        self.specular = 0.1
        self.vertex_colors = True
        self.write_report = True
        self.all_uv_layers = False
        self.hide_helper_bones = True
        self.hide_facial_bones = False
        self.skip_translucent = True
        self.bake_mode = 'AUTO'           # AUTO / ALL / OFF
        self.bake_size = 0                # 0 = auto (source texture size, max 2048)
        self.strip_common_prefix = True
        self.auto_facing = True
        self._pre_rotation = None         # mathutils.Matrix (3x3) applied to all world coordinates
        for k, v in kw.items():
            if not hasattr(self, k):
                raise TypeError('unknown setting %r' % k)
            setattr(self, k, v)

    @classmethod
    def from_props(cls, props):
        s = cls()
        for k in list(vars(s)):
            if hasattr(props, k):
                setattr(s, k, getattr(props, k))
        return s


class Report(object):
    def __init__(self):
        self.lines = []
        self.warnings = []
        self.errors = []

    def note(self, msg):
        self.lines.append(msg)

    def warn(self, msg):
        self.warnings.append(msg)
        self.lines.append('警告: ' + msg)

    def error(self, msg):
        self.errors.append(msg)
        self.lines.append('错误: ' + msg)

    def text(self):
        return '\n'.join(self.lines)


class ExportResult(object):
    def __init__(self):
        self.path = ''
        self.fmt = ''
        self.stats = {}
        self.warnings = []
        self.errors = []
        self.report_text = ''
        self.report_path = ''
        self.seconds = 0.0
        self.bone_count = 0
        self.mesh_count = 0

    def summary(self):
        s = self.stats
        return ('%s | 骨骼 %d | 网格 %d | 顶点 %d | 三角面 %d | 警告 %d | %.1fs' % (
            os.path.basename(self.path), self.bone_count, self.mesh_count,
            s.get('vertices', 0), s.get('faces', 0), len(self.warnings), self.seconds))


# --------------------------------------------------------------------------- coordinate helpers

def to_xps(v):
    """Blender (x, y, z) Z-up  ->  XPS (x, z, -y) Y-up."""
    return (v[0], v[2], -v[1])


def to_xps_array(a):
    return np.column_stack((a[:, 0], a[:, 2], -a[:, 1]))


# --------------------------------------------------------------------------- object collection

def find_armature_for(obj):
    for mod in obj.modifiers:
        if mod.type == 'ARMATURE' and mod.object is not None:
            return mod.object
    if obj.parent is not None and obj.parent.type == 'ARMATURE':
        return obj.parent
    return None


def selected_objects(context):
    """context.selected_objects is missing in timer / background contexts."""
    sel = getattr(context, 'selected_objects', None)
    if sel is None:
        sel = [o for o in context.view_layer.objects if o.select_get()]
    return list(sel)


def is_mmd_helper_object(obj):
    return getattr(obj, 'mmd_type', 'NONE') != 'NONE'


def _mesh_has_geometry(obj):
    if obj.data is None:
        return False
    if len(obj.data.polygons) > 0:
        return True
    # a modifier may generate faces (skin, screw, geometry nodes ...)
    return any(m.type not in ('ARMATURE',) for m in obj.modifiers) and len(obj.data.vertices) > 0


def resolve_armature(context, settings, meshes):
    if settings.armature_name:
        arm = bpy.data.objects.get(settings.armature_name)
        if arm is not None and arm.type == 'ARMATURE':
            return arm
    active = context.view_layer.objects.active
    if active is not None and active.type == 'ARMATURE':
        return active
    if active is not None and active.type == 'MESH':
        arm = find_armature_for(active)
        if arm is not None:
            return arm
    for obj in selected_objects(context):
        if obj.type == 'ARMATURE':
            return obj
    counts = {}
    order = []
    for obj in meshes:
        arm = find_armature_for(obj)
        if arm is not None:
            if arm.name not in counts:
                order.append(arm)
            counts[arm.name] = counts.get(arm.name, 0) + 1
    if order:
        return max(order, key=lambda a: counts[a.name])
    for obj in context.view_layer.objects:
        if obj.type == 'ARMATURE' and not is_mmd_helper_object(obj):
            return obj
    return None


def collect_objects(context, settings, report=None):
    """Return (armature, mesh objects, skipped [(name, reason)])."""
    skipped = []
    scope = settings.scope
    if scope == 'SELECTED':
        candidates = selected_objects(context)
        if not any(o.type == 'MESH' for o in candidates):
            # only an armature selected: take everything bound to it
            arms = [o for o in candidates if o.type == 'ARMATURE']
            if arms:
                candidates = [o for o in context.view_layer.objects if o.type == 'MESH' and find_armature_for(o) in arms]
    elif scope == 'VISIBLE':
        candidates = [o for o in context.view_layer.objects if o.visible_get()]
    else:
        candidates = list(context.view_layer.objects)

    meshes = []
    for obj in candidates:
        if obj.type != 'MESH':
            continue
        if is_mmd_helper_object(obj):
            skipped.append((obj.name, 'mmd_tools 刚体/关节'))
            continue
        if settings.visible_only and scope != 'VISIBLE' and not obj.visible_get():
            skipped.append((obj.name, '隐藏'))
            continue
        if not _mesh_has_geometry(obj):
            skipped.append((obj.name, '没有面'))
            continue
        meshes.append(obj)

    armature = resolve_armature(context, settings, meshes)
    if scope == 'ARMATURE' and armature is not None:
        bound = []
        for obj in meshes:
            if find_armature_for(obj) is armature:
                bound.append(obj)
            else:
                skipped.append((obj.name, '未绑定到骨架 %s' % armature.name))
        meshes = bound
    return armature, meshes, skipped


# --------------------------------------------------------------------------- facing

def facing_rotation(armature, report=None, weighted=()):
    """Rotation about Z that turns the character to face -Y, based on the
    ankle -> toes direction of the (mapped) leg bones.  None when unknown or
    already facing -Y."""
    if armature is None:
        return None
    weighted = set(weighted)
    M = armature.matrix_world
    by_key = {}
    for b in armature.data.bones:
        key = bn.canonical_key(b.name)
        if key and key not in by_key:
            by_key[key] = b
    d = None
    source = ''
    # 1. eyes sit in front of the head bone: the most reliable cue
    if 'head' in by_key and 'eye_l' in by_key and 'eye_r' in by_key:
        eyes = ((M @ by_key['eye_l'].head_local) + (M @ by_key['eye_r'].head_local)) / 2.0
        d = eyes - (M @ by_key['head'].head_local)
        d.z = 0.0
        source = '眼球'
        if d.length < 1e-4:
            d = None
    # 2. otherwise ankle -> toes of a weighted leg
    if d is None:
        candidates = []
        for b in armature.data.bones:
            key = bn.canonical_key(b.name)
            if key in ('toes_l', 'toes_r') and b.parent is not None:
                score = (b.parent.name in weighted) + (b.name in weighted)
                candidates.append((-score, b))
        candidates.sort(key=lambda t: t[0])
        if not candidates:
            return None
        best = candidates[0][0]
        d = Vector((0.0, 0.0, 0.0))
        for score, toes in candidates:          # average both feet: splayed feet cancel out
            if score != best:
                break
            step = (M @ toes.head_local) - (M @ toes.parent.head_local)
            step.z = 0.0
            if step.length > 1e-6:
                d += step.normalized()
        source = '脚趾'
    if d.length < 1e-6:
        return None
    angle = math.atan2(d.y, d.x)
    rot = -math.pi / 2.0 - angle
    rot = (rot + math.pi) % (2 * math.pi) - math.pi
    if abs(rot) < math.radians(5.0):
        return None
    if report is not None:
        report.note('模型朝向按%s方向自动转正: 绕 Z 旋转 %.0f°' % (source, math.degrees(rot)))
    return Matrix.Rotation(rot, 3, 'Z')


# --------------------------------------------------------------------------- bones

class BoneTable(object):
    def __init__(self):
        self.bones = []               # xf.XpsBone in export order
        self.index_of = {}            # blender bone name -> export index
        self.redirect = {}            # merged / dropped blender bone name -> export index
        self.renamed = {}             # blender name -> exported name (only changed ones)
        self.merged = {}              # blender name -> target blender name
        self.dropped = []
        self.hidden = []              # exported names that got the XPS "unused" prefix
        self.root_index = 0
        self.positions_blender = []   # world space heads (Blender axes) per export bone
        self.armature = None

    def index_for_group(self, name):
        idx = self.index_of.get(name)
        if idx is None:
            idx = self.redirect.get(name, -1)
        return idx


def _topo_sort(names, parent_fn):
    """Stable parents-first ordering (iterative, tolerates cycles)."""
    order = []
    placed = set()
    for start in names:
        chain = []
        seen = set()
        n = start
        while n is not None and n not in placed and n not in seen:
            seen.add(n)
            chain.append(n)
            n = parent_fn(n)
        for n in reversed(chain):
            if n not in placed:
                placed.add(n)
                order.append(n)
    return order


def build_bone_table(armature, settings, group_names=(), report=None):
    table = BoneTable()
    table.armature = armature
    scale = settings.scale
    if armature is None:
        table.bones.append(xf.XpsBone('root ground', -1, (0.0, 0.0, 0.0)))
        table.positions_blender.append(Vector((0.0, 0.0, 0.0)))
        table.root_index = 0
        return table

    bones = list(armature.data.bones)
    names = [b.name for b in bones]
    name_set = set(names)
    parent_of = {b.name: (b.parent.name if b.parent is not None else None) for b in bones}
    group_names = set(group_names)

    merge_into = {}
    if settings.mmd_merge_helpers:
        for b in bones:
            target = bn.mmd_merge_target(b.name, name_set)
            if target is not None:
                merge_into[b.name] = target

    def final_target(n):
        seen = set()
        while n in merge_into and n not in seen:
            seen.add(n)
            n = merge_into[n]
        return n

    dropped = set()
    if settings.drop_internal_bones:
        for b in bones:
            if bn.MMD_INTERNAL_RE.match(b.name) and b.name not in group_names:
                dropped.add(b.name)
    removed = set(merge_into) | dropped

    def is_descendant_or_self(node, ancestor):
        seen = set()
        while node is not None and node not in seen:
            if node == ancestor:
                return True
            seen.add(node)
            node = parent_of[node]
        return False

    def effective_parent(n):
        """Nearest surviving ancestor.  A removed ancestor that was merged
        into another bone hands its children to that bone, unless the target
        sits below *n* itself (e.g. 肩P merged into its child 肩), in which case
        we simply continue upwards."""
        p = parent_of[n]
        while p is not None and p in removed:
            if p in merge_into:
                t = final_target(p)
                if t not in removed and not is_descendant_or_self(t, n):
                    return t
                p = parent_of[p]
            else:
                p = parent_of[p]
        return p

    kept = [n for n in names if n not in removed]
    if settings.sort_bones:
        kept = _topo_sort(kept, effective_parent)
    index_of = {n: i for i, n in enumerate(kept)}

    new_names = list(kept)
    if settings.bone_naming == 'XPS':
        mapping = bn.map_names_to_xps(kept)
        new_names = [mapping.get(n, n) for n in kept]
    if settings.ascii_bone_names:
        new_names = [bn.romanize(n) or ('bone%d' % i) for i, n in enumerate(new_names)]
    xps_named = set(bn.XPS_NAMES.values())
    if settings.hide_helper_bones or settings.hide_facial_bones:
        hidden = []
        for i, n in enumerate(kept):
            if new_names[i] in xps_named:
                continue
            if (settings.hide_helper_bones and bn.is_helper_bone(n)) or \
                    (settings.hide_facial_bones and bn.is_facial_bone(n)):
                new_names[i] = bn.hide_name(new_names[i])
                hidden.append(new_names[i])
        table.hidden = hidden
    new_names = bn.make_unique(new_names)

    M = armature.matrix_world
    R = settings._pre_rotation
    pose_bones = armature.pose.bones
    data_bones = armature.data.bones
    for i, n in enumerate(kept):
        if settings.bake_pose:
            head = M @ pose_bones[n].head
        else:
            head = M @ data_bones[n].head_local
        if R is not None:
            head = R @ head
        parent_name = effective_parent(n)
        parent_idx = index_of[parent_name] if parent_name is not None else -1
        pos = to_xps(head)
        table.bones.append(xf.XpsBone(new_names[i], parent_idx, (pos[0] * scale, pos[1] * scale, pos[2] * scale)))
        table.positions_blender.append(Vector(head))   # already includes the facing rotation
        if new_names[i] != n:
            table.renamed[n] = new_names[i]
    table.index_of = index_of

    for n in merge_into:
        t = final_target(n)
        if t in index_of:
            table.redirect[n] = index_of[t]
            table.merged[n] = t
        else:
            anc = effective_parent(n)
            table.redirect[n] = index_of[anc] if anc is not None else 0
            table.merged[n] = anc if anc is not None else kept[0]
    for n in dropped:
        anc = effective_parent(n)
        table.redirect[n] = index_of[anc] if anc is not None else 0
    table.dropped = sorted(dropped)

    roots = [i for i, b in enumerate(table.bones) if b.parent < 0]
    table.root_index = roots[0] if roots else 0
    for i, b in enumerate(table.bones):
        if b.name == bn.XPS_NAMES['root']:
            table.root_index = i
            break

    if report is not None:
        report.note('骨架 %s: %d 根骨 -> 导出 %d 根 (合并 %d, 去除 %d, 改名 %d, XPS 列表中隐藏 %d)' % (
            armature.name, len(bones), len(table.bones), len(table.merged), len(table.dropped),
            len(table.renamed), len(table.hidden)))
        if len(roots) > 1:
            report.note('  根骨有 %d 根: %s' % (len(roots), ', '.join(table.bones[i].name for i in roots[:6])))
    return table


# --------------------------------------------------------------------------- modifiers

def _prepare_modifiers(objects, settings):
    changes = []
    for obj in objects:
        for mod in obj.modifiers:
            if mod.type == 'ARMATURE':
                disable = not settings.bake_pose
            elif mod.name.lower().startswith('mmd_edge'):
                disable = True            # mmd_tools outline preview shell
            else:
                disable = not settings.apply_modifiers
            if disable and mod.show_viewport:
                try:
                    mod.show_viewport = False
                    changes.append((mod, True))
                except Exception:
                    pass
    return changes


def _restore_modifiers(changes):
    for mod, value in changes:
        try:
            mod.show_viewport = value
        except Exception:
            pass


# --------------------------------------------------------------------------- weights

def _vertex_weights(obj, me, table, settings, co_world, report):
    n = len(me.vertices)
    group_names = [g.name for g in obj.vertex_groups]
    g2b = [table.index_for_group(name) for name in group_names]
    max_w = settings.max_weights
    thr = settings.weight_threshold
    root = table.root_index
    if obj.parent_type == 'BONE' and obj.parent_bone:
        root = table.index_for_group(obj.parent_bone)
        if root < 0:
            root = table.root_index
    out = [None] * n
    unweighted = 0
    truncated = 0
    ignored_groups = set()
    tree = None
    if settings.unweighted == 'NEAREST':
        tree = kdtree.KDTree(len(table.positions_blender))
        for i, p in enumerate(table.positions_blender):
            tree.insert(p, i)
        tree.balance()

    for i, v in enumerate(me.vertices):
        acc = {}
        for g in v.groups:
            gi = g.group
            if gi >= len(g2b):
                continue
            w = g.weight
            if w <= thr:
                continue
            b = g2b[gi]
            if b < 0:
                ignored_groups.add(group_names[gi])
                continue
            acc[b] = acc.get(b, 0.0) + w
        if not acc:
            unweighted += 1
            if settings.unweighted == 'NEAREST' and tree is not None:
                _co, idx, _d = tree.find(Vector(co_world[i]))
                out[i] = ([idx], [1.0])
            elif settings.unweighted == 'KEEP':
                out[i] = ([root], [0.0])
            else:
                out[i] = ([root], [1.0])
            continue
        items = sorted(acc.items(), key=lambda kv: -kv[1])
        if max_w and len(items) > max_w:
            truncated += 1
            items = items[:max_w]
        total = sum(w for _b, w in items)
        out[i] = ([b for b, _w in items], [w / total for _b, w in items])

    if report is not None:
        if unweighted:
            what = {'ROOT': '绑定到根骨', 'NEAREST': '绑定到最近的骨骼', 'KEEP': '保持为 0 权重'}[settings.unweighted]
            report.warn('%s: %d 个顶点没有骨骼权重，已%s' % (obj.name, unweighted, what))
        if truncated:
            report.note('%s: %d 个顶点超过 %d 个权重，已按权重大小截断并重新归一化' % (obj.name, truncated, max_w))
        if ignored_groups:
            report.note('%s: 忽略非骨骼顶点组: %s' % (obj.name, ', '.join(sorted(ignored_groups)[:8])))
    return out


# --------------------------------------------------------------------------- names

_RG_PREFIX = re.compile(r'^(\d+)_')
_BLENDER_SUFFIX = re.compile(r'\.\d{3}$')


def _is_number(token):
    try:
        float(token)
        return True
    except ValueError:
        return False


def _clean_token(name, ascii_only):
    name = _BLENDER_SUFFIX.sub('', name)
    if ascii_only:
        name = bn.romanize(name)
    name = name.replace('_', '-')
    name = re.sub(r'\s+', ' ', name).strip(' -.')
    return name


def _common_object_prefix(objects):
    """Shared name prefix such as 'Fiona_' in Fiona_Face / Fiona_Hair, cut at a separator."""
    names = [_object_base_name(o)[1] for o in objects]
    if len(names) < 2:
        return ''
    prefix = os.path.commonprefix(names)
    cut = max(prefix.rfind('_'), prefix.rfind('-'), prefix.rfind(' '))
    if cut < 1:
        return ''
    prefix = prefix[:cut + 1]
    if any(len(n) <= len(prefix) for n in names):
        return ''
    return prefix


def _object_base_name(obj):
    """Strip an XNALaraMesh style prefix/suffix ('5_name_0.1_0_0') from an object name."""
    name = _BLENDER_SUFFIX.sub('', obj.name)
    m = _RG_PREFIX.match(name)
    if m:
        name = name[m.end():]
    tokens = name.split('_')
    while len(tokens) > 1 and _is_number(tokens[-1]):
        tokens.pop()
    name = '_'.join(tokens)
    optional = ''
    if name[:1] in ('+', '-'):
        optional, name = name[0], name[1:]
    return optional, name


def _part_name(obj, mat, slot, n_slots, rg, spec, ascii_only, used, part_index=0, single_object=False,
               strip_prefix=''):
    optional, base_obj = _object_base_name(obj)
    if strip_prefix and base_obj.startswith(strip_prefix) and len(base_obj) > len(strip_prefix):
        base_obj = base_obj[len(strip_prefix):]
    base = _clean_token(base_obj, ascii_only) or 'mesh'
    if n_slots > 1:
        mat_name = mat.name if mat is not None else 'mat%d' % slot
        mat_token = _clean_token(mat_name, ascii_only) or ('mat%d' % slot)
        # one object split by material: the material name alone is clearer in XPS
        base = mat_token if single_object else '%s-%s' % (base, mat_token)
    if part_index:
        base = '%s-part%d' % (base, part_index + 1)
    spec_txt = ('%g' % spec) if spec is not None else '0.1'
    cand = base
    n = 2
    while ('%d_%s%s_%s_0_0' % (rg, optional, cand, spec_txt)).lower() in used:
        cand = '%s %d' % (base, n)
        n += 1
    name = '%d_%s%s_%s_0_0' % (rg, optional, cand, spec_txt)
    used.add(name.lower())
    return name


# --------------------------------------------------------------------------- materials

def _render_group_for(obj, mat, tex, settings, uv_samples=None, plane=None):
    """Render group for one material slot (override -> auto)."""
    override = None
    if mat is not None and 'xps_render_group' in mat.keys():
        try:
            override = int(mat['xps_render_group'])
        except Exception:
            override = None
    if override is None:
        m = _RG_PREFIX.match(obj.name)
        if m:
            override = int(m.group(1))
    if override in mt.RENDER_GROUPS:
        return override, mt.RENDER_GROUPS[override][0]
    diffuse = tex.get(mt.DIFFUSE)
    if isinstance(diffuse, mt.BakedTexture):
        diffuse = None
    alpha = mt.material_needs_alpha(mat, diffuse, settings.alpha_mode, uv_samples, plane)
    return mt.choose_render_group(tex.keys(), alpha, settings.unlit), alpha


def _slot_material_info(obj, mat, slot, settings, texex, uv_count, report, uv_samples=None, baked=None):
    """Return (render group, [XpsTexture], specular).  *baked* is an optional
    BakedTexture that replaces the diffuse image (and supplies the alpha)."""
    tex = mt.find_material_textures(mat) if mat is not None else {}
    if baked is not None:
        tex.pop(mt.EMISSION, None)
        tex[mt.DIFFUSE] = baked
        rg, _alpha = _render_group_for(obj, mat, tex, settings, uv_samples, plane=baked.alpha_plane())
    else:
        rg, _alpha = _render_group_for(obj, mat, tex, settings, uv_samples)
    slots = mt.RENDER_GROUPS[rg][1]
    textures = []
    for kind in slots:
        img = tex.get(kind)
        layer = 1 if (kind == mt.LIGHT and uv_count > 1) else 0
        if isinstance(img, mt.BakedTexture):
            textures.append(xf.XpsTexture(texex.save_baked(img), layer))
        elif img is not None:
            textures.append(xf.XpsTexture(texex.export_image(img), layer))
        elif kind == mt.DIFFUSE:
            label = '%s-%d' % (obj.name, slot)
            textures.append(xf.XpsTexture(texex.flat_color(mat, label), layer))
            if report is not None:
                report.note('%s / %s: 没有漫反射贴图，已生成纯色贴图' % (obj.name, mat.name if mat else '无材质'))
        else:
            textures.append(xf.XpsTexture('missing.png', layer))
            if report is not None:
                report.warn('%s / %s: 渲染组 %d 需要 %s 贴图但材质里没有' % (obj.name, mat.name if mat else '无材质', rg, kind))
    spec = settings.specular
    if mat is not None and 'xps_specular' in mat.keys():
        try:
            spec = float(mat['xps_specular'])
        except Exception:
            pass
    return rg, textures, spec


# --------------------------------------------------------------------------- mesh conversion

def _srgb(a):
    return np.where(a <= 0.0031308, 12.92 * a, 1.055 * np.power(np.clip(a, 0.0, None), 1.0 / 2.4) - 0.055)


def _loop_colors(me, loop_v, settings):
    if not settings.vertex_colors or not hasattr(me, 'color_attributes') or not me.color_attributes:
        return None
    attr = me.color_attributes.active_color or me.color_attributes[0]
    n_items = len(attr.data)
    if n_items == 0:
        return None
    buf = np.empty(n_items * 4, dtype=np.float32)
    srgb_done = False
    if attr.data_type == 'BYTE_COLOR':
        try:
            attr.data.foreach_get('color_srgb', buf)
            srgb_done = True
        except Exception:
            attr.data.foreach_get('color', buf)
    else:
        attr.data.foreach_get('color', buf)
    col = buf.reshape(-1, 4)
    if not srgb_done:
        col = col.copy()
        col[:, :3] = _srgb(col[:, :3])
    if attr.domain == 'POINT':
        col = col[loop_v]
    col = np.clip(np.rint(col * 255.0), 0, 255).astype(np.uint8)
    return col


def convert_mesh_object(obj, depsgraph, table, settings, texex, fmt, used_names, report, single_object=False,
                        strip_prefix=''):
    """Convert one mesh object into a list of XpsMesh parts (one per material slot)."""
    # decide per material what to do before touching the evaluated mesh
    slot_plan = {}
    to_bake = []
    for slot_idx, slot in enumerate(obj.material_slots):
        mat = slot.material
        if mt.material_is_invisible(mat):
            slot_plan[slot_idx] = 'invisible'
        elif settings.skip_translucent and mt.material_is_translucent_shell(mat):
            slot_plan[slot_idx] = 'translucent'
        elif mt.material_wants_bake(mat, settings.bake_mode):
            slot_plan[slot_idx] = 'bake'
            if mat not in to_bake:
                to_bake.append(mat)
    baked = {}
    if to_bake:
        baked = mt.bake_object_materials(bpy.context, obj, to_bake, settings.bake_size, report)
    eval_obj = obj.evaluated_get(depsgraph)
    try:
        me = eval_obj.to_mesh(preserve_all_data_layers=True, depsgraph=depsgraph)
    except RuntimeError as exc:
        report.warn('%s: 无法生成网格数据 (%s)' % (obj.name, exc))
        return []
    parts = []
    try:
        me.calc_loop_triangles()
        n_loops = len(me.loops)
        n_verts = len(me.vertices)
        n_tris = len(me.loop_triangles)
        if n_tris == 0 or n_verts == 0:
            report.warn('%s: 计算后的网格没有三角面，已跳过' % obj.name)
            return []

        # normals (custom / auto smooth aware).  Blender 3.x needs
        # calc_normals_split(); 4.1+ exposes corner_normals directly.
        nor = np.empty(n_loops * 3, dtype=np.float32)
        corner = getattr(me, 'corner_normals', None)
        if corner is None or len(corner) != n_loops:
            if hasattr(me, 'calc_normals_split'):
                me.calc_normals_split()
            corner = getattr(me, 'corner_normals', None)
        if corner is not None and len(corner) == n_loops:
            corner.foreach_get('vector', nor)
        else:
            me.loops.foreach_get('normal', nor)
        nor = nor.reshape(-1, 3)

        co = np.empty(n_verts * 3, dtype=np.float32)
        me.vertices.foreach_get('co', co)
        co = co.reshape(-1, 3)
        loop_v = np.empty(n_loops, dtype=np.int32)
        me.loops.foreach_get('vertex_index', loop_v)
        tri_loops = np.empty(n_tris * 3, dtype=np.int32)
        me.loop_triangles.foreach_get('loops', tri_loops)
        tri_loops = tri_loops.reshape(-1, 3)
        tri_mat = np.empty(n_tris, dtype=np.int32)
        me.loop_triangles.foreach_get('material_index', tri_mat)

        uv_layers = []
        for layer in me.uv_layers:
            buf = np.empty(n_loops * 2, dtype=np.float32)
            layer.data.foreach_get('uv', buf)
            uv_layers.append(buf.reshape(-1, 2))
            if not settings.all_uv_layers:
                break
        if not uv_layers:
            uv_layers = [np.zeros((n_loops, 2), dtype=np.float32)]
            report.warn('%s: 没有 UV，贴图无法正确显示' % obj.name)

        col = _loop_colors(me, loop_v, settings)

        # world transform (optionally turned to face the viewer)
        M = np.array(obj.matrix_world, dtype=np.float64)
        if settings._pre_rotation is not None:
            R4 = np.eye(4)
            R4[:3, :3] = np.array(settings._pre_rotation, dtype=np.float64)
            M = R4 @ M
        M3 = M[:3, :3]
        trans = M[:3, 3]
        co_w = co.astype(np.float64) @ M3.T + trans
        det = float(np.linalg.det(M3))
        mirrored = det < 0.0
        try:
            N = np.linalg.inv(M3).T
        except np.linalg.LinAlgError:
            N = M3
        nor_w = nor.astype(np.float64) @ N.T
        lens = np.linalg.norm(nor_w, axis=1)
        lens[lens == 0.0] = 1.0
        nor_w /= lens[:, None]

        pos_x = to_xps_array(co_w) * settings.scale
        nor_x = to_xps_array(nor_w)
        uv_x = [np.column_stack((u[:, 0], 1.0 - u[:, 1])) for u in uv_layers]

        tangents_x = None
        if fmt == xf.FMT_MESH and me.uv_layers:
            try:
                me.calc_tangents(uvmap=me.uv_layers[0].name)
                tan = np.empty(n_loops * 3, dtype=np.float32)
                me.loops.foreach_get('tangent', tan)
                sign = np.empty(n_loops, dtype=np.float32)
                me.loops.foreach_get('bitangent_sign', sign)
                tan_w = tan.reshape(-1, 3).astype(np.float64) @ M3.T
                tl = np.linalg.norm(tan_w, axis=1)
                tl[tl == 0.0] = 1.0
                tan_w /= tl[:, None]
                tangents_x = np.column_stack((tan_w[:, 0], tan_w[:, 2], -tan_w[:, 1], sign))
                me.free_tangents()
            except Exception:
                tangents_x = None

        weights = _vertex_weights(obj, me, table, settings, co_w, report)

        # de-duplication key per loop: vertex, normal, uvs, colour
        cols = [loop_v.astype(np.int64)]
        qn = np.rint(nor_x * 10000.0).astype(np.int64)
        cols += [qn[:, 0], qn[:, 1], qn[:, 2]]
        for u in uv_x:
            qu = np.rint(u * 100000.0).astype(np.int64)
            cols += [qu[:, 0], qu[:, 1]]
        if col is not None:
            cols += [col[:, k].astype(np.int64) for k in range(4)]
        keys = np.column_stack(cols)

        n_slots = max(1, len(obj.material_slots))
        slot_of_tri = np.clip(tri_mat, 0, n_slots - 1)
        uv_count = len(uv_x)
        for slot in range(n_slots):
            sel = np.nonzero(slot_of_tri == slot)[0]
            if sel.size == 0:
                continue
            mat = obj.material_slots[slot].material if obj.material_slots else None
            plan = slot_plan.get(slot)
            if plan == 'invisible':
                report.note('  跳过材质 %s: 完全透明' % mat.name)
                continue
            if plan == 'translucent':
                report.note('  跳过材质 %s: 半透明辅助壳 (alpha < 0.5)' % mat.name)
                continue
            loops_sel = tri_loops[sel].reshape(-1)
            uniq, first_idx, inverse = np.unique(keys[loops_sel], axis=0, return_index=True, return_inverse=True)
            inverse = np.asarray(inverse).reshape(-1)
            rep_loops = loops_sel[first_idx]
            faces = inverse.reshape(-1, 3)
            if not mirrored:
                faces = faces[:, [0, 2, 1]]

            # alpha detection samples the texture at the vertices and at the
            # triangle centroids of this part (Blender UV space, v not flipped)
            uv0 = uv_layers[0]
            uv_samples = np.concatenate((uv0[rep_loops], uv0[tri_loops[sel]].mean(axis=1)), axis=0)
            baked_tex = baked.get(mat.name) if (mat is not None and plan == 'bake') else None
            if plan == 'bake' and baked_tex is None:
                report.warn('  %s: 材质 %s 需要烘焙但烘焙失败，改用节点里找到的贴图' % (obj.name, mat.name))
            rg, textures, spec = _slot_material_info(obj, mat, slot, settings, texex, uv_count, report, uv_samples,
                                                     baked=baked_tex)
            name = _part_name(obj, mat, slot, n_slots, rg, spec, settings.ascii_mesh_names, used_names,
                              single_object=single_object, strip_prefix=strip_prefix)
            mesh = xf.XpsMesh(name, uv_count, textures)
            vidx = loop_v[rep_loops]
            mesh.positions = [tuple(p) for p in pos_x[vidx].tolist()]
            mesh.normals = [tuple(v) for v in nor_x[rep_loops].tolist()]
            if col is not None:
                mesh.colors = [tuple(c) for c in col[rep_loops].tolist()]
            else:
                mesh.colors = [(255, 255, 255, 255)] * len(rep_loops)
            per_layer = [[tuple(v) for v in u[rep_loops].tolist()] for u in uv_x]
            mesh.uvs = [list(t) for t in zip(*per_layer)]
            mesh.bone_ids = [weights[v][0] for v in vidx.tolist()]
            mesh.bone_weights = [weights[v][1] for v in vidx.tolist()]
            mesh.faces = [tuple(f) for f in faces.tolist()]
            if tangents_x is not None:
                mesh.tangents = [[tuple(t)] * uv_count for t in tangents_x[rep_loops].tolist()]
            if mesh.vertex_count > 65535:
                report.warn('%s: 部件 %s 有 %d 个顶点 (>65535)，非常旧的 XNALara 可能无法加载' % (obj.name, name, mesh.vertex_count))
            parts.append(mesh)
            report.note('  %-40s 顶点 %-6d 三角面 %-6d 渲染组 %-2d 贴图 %s' % (
                name, mesh.vertex_count, len(mesh.faces), rg, ', '.join(t.file for t in textures)))
    finally:
        try:
            eval_obj.to_mesh_clear()
        except Exception:
            pass
    return parts


# --------------------------------------------------------------------------- driver

def resolve_format(settings, max_weights_needed=4):
    fmt = settings.fmt
    path = settings.filepath.lower()
    if fmt == 'AUTO':
        if path.endswith('.ascii'):
            return xf.FMT_ASCII
        if settings.max_weights == 0 or settings.max_weights > 4:
            return xf.FMT_XPS3
        return xf.FMT_XPS2
    if fmt not in xf.ALL_FORMATS:
        raise ExportError('未知格式 %r' % fmt)
    return fmt


def export_model(context, settings):
    t0 = time.time()
    if not settings.filepath:
        raise ExportError('没有指定输出文件')
    report = Report()
    result = ExportResult()

    armature, meshes, skipped = collect_objects(context, settings, report)
    if not meshes:
        raise ExportError('没有可导出的网格（范围: %s）' % settings.scope)
    if armature is None:
        report.warn('没有找到骨架，模型将作为静态物体导出（只有一根 root ground 骨）')

    fmt = resolve_format(settings)
    if fmt in (xf.FMT_XPS2, xf.FMT_MESH) and (settings.max_weights == 0 or settings.max_weights > 4):
        report.note('格式 %s 每顶点固定 4 个权重，已把权重上限设为 4' % fmt)
        settings.max_weights = 4
    path = xf.fix_extension(bpy.path.abspath(settings.filepath), fmt)
    out_dir = os.path.dirname(path)
    if out_dir:
        os.makedirs(out_dir, exist_ok=True)

    group_names = set()
    for obj in meshes:
        group_names.update(g.name for g in obj.vertex_groups)
    settings._pre_rotation = facing_rotation(armature, report, group_names) if settings.auto_facing else None
    table = build_bone_table(armature, settings, group_names, report)

    texex = mt.TextureExporter(out_dir, settings.copy_textures, settings.ascii_mesh_names, report)
    model = xf.XpsModel(bones=table.bones)
    used_names = set()
    strip_prefix = _common_object_prefix(meshes) if (settings.strip_common_prefix and len(meshes) > 1) else ''
    changes = _prepare_modifiers(meshes, settings)
    mt.clear_alpha_cache()
    try:
        context.view_layer.update()
        depsgraph = context.evaluated_depsgraph_get()
        for obj in meshes:
            report.note('网格 %s:' % obj.name)
            model.meshes.extend(convert_mesh_object(obj, depsgraph, table, settings, texex, fmt, used_names, report,
                                                    single_object=(len(meshes) == 1), strip_prefix=strip_prefix))
    finally:
        _restore_modifiers(changes)
        mt.clear_alpha_cache()
    if not model.meshes:
        raise ExportError('所有网格都被跳过，没有写出文件')

    xf.write_model_file(model, path, fmt, machine='spx2rednelb', user='spx2rednelb', files='')

    # read back and verify what is on disk
    check = xf.read_model_file(path)
    errors, warnings, stats = xf.verify_model(check)
    if len(check.bones) != len(model.bones) or len(check.meshes) != len(model.meshes):
        errors.append('回读结果与写入不一致 (骨骼 %d/%d, 网格 %d/%d)' % (
            len(check.bones), len(model.bones), len(check.meshes), len(model.meshes)))
    for e in errors:
        report.error('文件校验: ' + e)
    for w in warnings:
        report.warn('文件校验: ' + w)

    result.path = path
    result.fmt = fmt
    result.stats = stats
    result.bone_count = len(model.bones)
    result.mesh_count = len(model.meshes)
    result.seconds = time.time() - t0

    head = []
    head.append('Blender2XPS 导出报告')
    head.append('文件: %s' % path)
    head.append('格式: %s   耗时: %.1fs' % (fmt, result.seconds))
    head.append('骨骼 %d  网格部件 %d  顶点 %d  三角面 %d  每顶点最多权重 %d' % (
        stats['bones'], stats['meshes'], stats['vertices'], stats['faces'], stats['max_weights_per_vertex']))
    bounds = xf.model_bounds(check)
    if bounds:
        lo, hi = bounds
        height = hi[1] - lo[1]
        head.append('包围盒 (XPS 坐标): X %.3f..%.3f  Y(高) %.3f..%.3f  Z %.3f..%.3f  -> 高度 %.3f' % (
            lo[0], hi[0], lo[1], hi[1], lo[2], hi[2], height))
        if height > 20.0:
            report.warn('模型高度 %.0f 单位，XPS 角色通常 1.5~2 单位高；源文件可能是厘米单位，建议缩放 0.01' % height)
        elif height < 0.2:
            report.warn('模型高度只有 %.3f 单位，可能需要放大' % height)
    if table.renamed:
        head.append('改名的骨骼 (%d): %s' % (len(table.renamed), ', '.join('%s->%s' % kv for kv in list(table.renamed.items())[:40])))
    if table.merged:
        head.append('合并的骨骼 (%d): %s' % (len(table.merged), ', '.join('%s->%s' % kv for kv in list(table.merged.items())[:40])))
    if table.dropped:
        head.append('去除的骨骼 (%d): %s' % (len(table.dropped), ', '.join(table.dropped[:40])))
    if table.hidden:
        head.append('XPS 列表中隐藏的辅助骨 (%d, 前缀 unused_): %s%s' % (
            len(table.hidden), ', '.join(table.hidden[:12]), ' ...' if len(table.hidden) > 12 else ''))
    if skipped:
        head.append('跳过的物体 (%d): %s' % (len(skipped), '; '.join('%s(%s)' % s for s in skipped[:30])))
    if texex.written:
        head.append('写出贴图 %d 个' % len(texex.written))
    head.append('')
    result.report_text = '\n'.join(head) + report.text()
    result.warnings = list(report.warnings)
    result.errors = list(report.errors)
    if settings.write_report:
        rp = path + '.report.txt'
        try:
            with open(rp, 'w', encoding='utf-8') as f:
                f.write(result.report_text + '\n')
            result.report_path = rp
        except Exception:
            pass
    print(result.report_text)
    return result


# --------------------------------------------------------------------------- dry run

def _uv_samples_by_slot(me):
    """{material slot: (n, 2) UV samples} from raw mesh data (for analyze())."""
    out = {}
    if not me.uv_layers or len(me.polygons) == 0:
        return out
    n_loops = len(me.loops)
    uv = np.empty(n_loops * 2, dtype=np.float32)
    me.uv_layers[0].data.foreach_get('uv', uv)
    uv = uv.reshape(-1, 2)
    n_polys = len(me.polygons)
    pm = np.empty(n_polys, dtype=np.int32)
    me.polygons.foreach_get('material_index', pm)
    ls = np.empty(n_polys, dtype=np.int32)
    me.polygons.foreach_get('loop_start', ls)
    lt = np.empty(n_polys, dtype=np.int32)
    me.polygons.foreach_get('loop_total', lt)
    loop_poly = np.repeat(np.arange(n_polys), lt)
    loop_mat = pm[loop_poly]
    for slot in np.unique(pm):
        out[int(slot)] = uv[loop_mat == slot]
    return out


def analyze(context, settings):
    """Diagnose the current selection without writing anything."""
    report = Report()
    armature, meshes, skipped = collect_objects(context, settings, report)
    lines = []
    lines.append('范围: %s   骨架: %s' % (settings.scope, armature.name if armature else '无'))
    if armature is not None:
        bones = armature.data.bones
        non_ascii = sum(1 for b in bones if not b.name.isascii())
        lower = set()
        dup = 0
        for b in bones:
            k = b.name.lower()
            dup += k in lower
            lower.add(k)
        posed = 0
        for pb in armature.pose.bones:
            mb = pb.matrix_basis
            if mb.to_translation().length > 1e-5 or abs(mb.to_quaternion().angle) > 1e-5:
                posed += 1
        sc = armature.matrix_world.to_scale()
        lines.append('  骨骼 %d 根, 非 ASCII 名 %d, 大小写重名 %d, 当前有姿态的骨 %d, 世界缩放 (%.3f %.3f %.3f)' % (
            len(bones), non_ascii, dup, posed, sc.x, sc.y, sc.z))
        heads = [armature.matrix_world @ b.head_local for b in bones]
        if heads:
            height = max(h.z for h in heads) - min(h.z for h in heads)
            if height * settings.scale > 20.0:
                lines.append('  提示: 骨架高约 %.0f 单位，像是厘米单位；XPS 角色通常 1.5~2 单位高，建议缩放 0.01' % height)
        if posed and not settings.bake_pose:
            lines.append('  提示: 骨架带姿态；导出使用静止姿态 (Rest)，如需把当前姿态当作绑定姿态请勾选“烘焙当前姿态”')
        group_names = set()
        for obj in meshes:
            group_names.update(g.name for g in obj.vertex_groups)
        table = build_bone_table(armature, settings, group_names, report)
        lines.append('  导出 %d 根 (合并 %d, 去除 %d, 改名 %d)' % (len(table.bones), len(table.merged), len(table.dropped), len(table.renamed)))
        if table.renamed:
            lines.append('  改名示例: ' + ', '.join('%s->%s' % kv for kv in list(table.renamed.items())[:12]))
    else:
        table = build_bone_table(None, settings, (), report)
    lines.append('网格 %d 个:' % len(meshes))
    for obj in meshes:
        me = obj.data
        arm = find_armature_for(obj)
        gnames = [g.name for g in obj.vertex_groups]
        g2b = [table.index_for_group(n) for n in gnames]
        unweighted = 0
        over = 0
        bad_sum = 0
        for v in me.vertices:
            ws = [g.weight for g in v.groups if g.group < len(g2b) and g2b[g.group] >= 0 and g.weight > settings.weight_threshold]
            if not ws:
                unweighted += 1
            if settings.max_weights and len(ws) > settings.max_weights:
                over += 1
            if ws and abs(sum(ws) - 1.0) > 0.01:
                bad_sum += 1
        ignored = [n for n, b in zip(gnames, g2b) if b < 0]
        sc = obj.matrix_world.to_scale()
        lines.append('  %s: 顶点 %d 面 %d 材质 %d UV %d | 骨架 %s | 无权重 %d, 超过 %d 权重 %d, 权重和≠1 %d%s%s' % (
            obj.name, len(me.vertices), len(me.polygons), len(obj.material_slots), len(me.uv_layers),
            arm.name if arm else '无', unweighted, settings.max_weights, over, bad_sum,
            (' | 忽略顶点组: ' + ', '.join(ignored[:6])) if ignored else '',
            (' | 缩放 (%.2f %.2f %.2f)' % (sc.x, sc.y, sc.z)) if any(abs(s - 1) > 1e-3 for s in sc) else ''))
        uv_by_slot = _uv_samples_by_slot(me)
        for slot_idx, slot in enumerate(obj.material_slots):
            mat = slot.material
            if mt.material_is_invisible(mat):
                lines.append('     材质[%d] %s -> 完全透明，跳过' % (slot_idx, mat.name))
                continue
            if settings.skip_translucent and mt.material_is_translucent_shell(mat):
                lines.append('     材质[%d] %s -> 半透明辅助壳，跳过' % (slot_idx, mat.name))
                continue
            if mt.material_wants_bake(mat, settings.bake_mode):
                lines.append('     材质[%d] %s -> 颜色/透明由节点计算，导出时烘焙成贴图 (%dx%d)' % (
                    slot_idx, mat.name, mt.bake_size_for(mat, settings.bake_size), mt.bake_size_for(mat, settings.bake_size)))
                continue
            tex = mt.find_material_textures(mat) if mat else {}
            rg, alpha = _render_group_for(obj, mat, tex, settings, uv_by_slot.get(slot_idx))
            lines.append('     材质[%d] %s -> 渲染组 %d%s | %s' % (
                slot_idx, mat.name if mat else '无', rg, ' (透明)' if alpha else '',
                ', '.join('%s=%s' % (k, os.path.basename(img.filepath) or img.name) for k, img in tex.items()) or '没有贴图(将生成纯色)'))
    mt.clear_alpha_cache()
    if skipped:
        lines.append('跳过: ' + '; '.join('%s(%s)' % s for s in skipped[:20]) + (' ...' if len(skipped) > 20 else ''))
    lines.extend(report.lines)
    return lines
