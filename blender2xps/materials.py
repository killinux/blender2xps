# -*- coding: utf-8 -*-
"""Material inspection for the XPS exporter.

* find the images a material uses (XPS Shader group, mmd_tools shader group,
  Principled BSDF, generic node groups, or plain image nodes)
* decide whether the material needs an alpha render group
* pick a render group and its texture slot list
* copy / convert the image files next to the exported model
"""

import os
import re
import shutil

import bpy
import numpy as np

DIFFUSE = 'diffuse'
LIGHT = 'lightmap'
BUMP = 'bump'
MASK = 'mask'
BUMP1 = 'bump1'
BUMP2 = 'bump2'
SPEC = 'specular'
ENV = 'environment'
EMISSION = 'emission'

# render group -> (uses alpha, texture slots in file order).  Same table as XPS
# (render group numbers are part of the mesh name: "<rg>_<name>_<spec>_<r1>_<r2>").
RENDER_GROUPS = {
    1: (False, [DIFFUSE, LIGHT, BUMP, MASK, BUMP1, BUMP2]),
    2: (False, [DIFFUSE, LIGHT, BUMP]),
    3: (False, [DIFFUSE, LIGHT]),
    4: (False, [DIFFUSE, BUMP]),
    5: (False, [DIFFUSE]),
    6: (True, [DIFFUSE, BUMP]),
    7: (True, [DIFFUSE]),
    8: (True, [DIFFUSE, LIGHT, BUMP]),
    9: (True, [DIFFUSE, LIGHT]),
    10: (False, [DIFFUSE]),                   # unlit
    11: (False, [DIFFUSE, BUMP]),             # vertex lit, static
    12: (True, [DIFFUSE, BUMP]),
    13: (False, [DIFFUSE]),                   # unlit, static
    14: (False, [DIFFUSE, BUMP]),
    15: (True, [DIFFUSE, BUMP]),
    16: (False, [DIFFUSE]),
    17: (False, [DIFFUSE, LIGHT]),
    18: (True, [DIFFUSE]),
    19: (True, [DIFFUSE, LIGHT]),
    20: (True, [DIFFUSE, LIGHT, BUMP, MASK, BUMP1, BUMP2]),
    21: (True, [DIFFUSE]),                    # unlit + alpha
    22: (False, [DIFFUSE, LIGHT, BUMP, MASK, BUMP1, BUMP2, SPEC]),
    23: (True, [DIFFUSE, LIGHT, BUMP, MASK, BUMP1, BUMP2, SPEC]),
    24: (False, [DIFFUSE, LIGHT, BUMP, SPEC]),
    25: (True, [DIFFUSE, LIGHT, BUMP, SPEC]),
    26: (False, [DIFFUSE, BUMP, ENV, MASK]),
    27: (True, [DIFFUSE, BUMP, ENV, MASK]),
    28: (False, [DIFFUSE, BUMP, MASK, BUMP1, BUMP2, ENV]),
    29: (True, [DIFFUSE, BUMP, MASK, BUMP1, BUMP2, ENV]),
    30: (False, [DIFFUSE, BUMP, EMISSION]),
    31: (True, [DIFFUSE, BUMP, EMISSION]),
    32: (False, [DIFFUSE]),
    33: (True, [DIFFUSE]),
    34: (False, [DIFFUSE, BUMP, MASK, SPEC]),
    35: (True, [DIFFUSE, BUMP, MASK, SPEC]),
    36: (False, [DIFFUSE, BUMP, EMISSION]),
    37: (True, [DIFFUSE, BUMP, EMISSION]),
    38: (False, [DIFFUSE, BUMP, SPEC, EMISSION]),
    39: (True, [DIFFUSE, BUMP, SPEC, EMISSION]),
    40: (False, [DIFFUSE, BUMP, SPEC]),
    41: (True, [DIFFUSE, BUMP, SPEC]),
    42: (False, [DIFFUSE, BUMP, SPEC]),
    43: (True, [DIFFUSE, BUMP, SPEC]),
}

IMAGE_EXTENSIONS = ('.png', '.jpg', '.jpeg', '.bmp', '.tga', '.dds')

_XPS_GROUP_INPUTS = {
    'Diffuse': DIFFUSE, 'Lightmap': LIGHT, 'Bump Map': BUMP, 'Bump Mask': MASK,
    'MicroBump 1': BUMP1, 'MicroBump 2': BUMP2, 'Specular': SPEC,
    'Environment': ENV, 'Emission': EMISSION,
}
_PRINCIPLED_INPUTS = {
    'Base Color': DIFFUSE, 'Normal': BUMP, 'Specular': SPEC, 'Specular IOR Level': SPEC,
    'Emission': EMISSION, 'Emission Color': EMISSION,
}
_GENERIC_GROUP_INPUTS = {
    'Color': DIFFUSE, 'Base Color': DIFFUSE, 'Diffuse': DIFFUSE, 'Albedo': DIFFUSE, 'Base Tex': DIFFUSE,
    'Normal': BUMP, 'Normal Map': BUMP, 'Bump': BUMP, 'Bump Map': BUMP,
    'Specular': SPEC, 'Emission': EMISSION, 'Emission Color': EMISSION, 'Emissive': EMISSION,
}
_PREFERRED_INPUTS = ('Color', 'Color1', 'Color2', 'A', 'B', 'Image', 'Base Color', 'Diffuse', 'Base Tex', 'Shader')
_LAST_INPUTS = ('Fac', 'Factor', 'Strength', 'Alpha', 'Roughness', 'Metallic', 'Vector', 'Normal', 'Height')


def _socket_order(sock):
    if sock.name in _PREFERRED_INPUTS:
        return 0
    if sock.name in _LAST_INPUTS:
        return 2
    return 1


def _upstream_image(socket, depth=0, visited=None):
    """Follow links upstream from *socket* until an image texture is found."""
    if socket is None or not socket.is_linked or depth > 24:
        return None
    if visited is None:
        visited = set()
    link = socket.links[0]
    node = link.from_node
    from_socket = link.from_socket
    key = (node.name, from_socket.identifier, depth > 0)
    if key in visited:
        return None
    visited.add(key)

    if node.bl_idname == 'ShaderNodeTexImage':
        return node.image if node.image is not None else None
    if node.bl_idname == 'ShaderNodeBump':
        return None                      # height maps are not tangent normal maps
    if node.bl_idname == 'ShaderNodeGroup':
        tree = node.node_tree
        if tree is None:
            return None
        outputs = [n for n in tree.nodes if n.bl_idname == 'NodeGroupOutput']
        outputs.sort(key=lambda n: 0 if getattr(n, 'is_active_output', False) else 1)
        for out in outputs:
            inp = out.inputs.get(from_socket.name)
            if inp is not None:
                img = _upstream_image(inp, depth + 1, visited)
                if img is not None:
                    return img
        for out in outputs:
            for inp in out.inputs:
                img = _upstream_image(inp, depth + 1, visited)
                if img is not None:
                    return img
        return None
    if node.bl_idname == 'NodeGroupInput':
        return None                      # would need the outer group's socket; handled by callers
    for inp in sorted(node.inputs, key=_socket_order):
        img = _upstream_image(inp, depth + 1, visited)
        if img is not None:
            return img
    return None


def _find_principled(tree):
    out = None
    for node in tree.nodes:
        if node.bl_idname == 'ShaderNodeOutputMaterial' and (node.is_active_output or out is None):
            out = node
    if out is not None:
        stack = [out.inputs.get('Surface')]
        seen = set()
        while stack:
            sock = stack.pop()
            if sock is None or not sock.is_linked:
                continue
            node = sock.links[0].from_node
            if node.name in seen:
                continue
            seen.add(node.name)
            if node.bl_idname == 'ShaderNodeBsdfPrincipled':
                return node
            for inp in node.inputs:
                if inp.type == 'SHADER':
                    stack.append(inp)
    for node in tree.nodes:
        if node.bl_idname == 'ShaderNodeBsdfPrincipled':
            return node
    return None


_KIND_BY_NAME = (
    (re.compile(r'(_n|_nrm|_nor|_normal|normal|_bump|bump)(\.|_|$)', re.I), BUMP),
    (re.compile(r'(_s|_spec|specular|_spc|_rough|_metal|_orm)(\.|_|$)', re.I), SPEC),
    (re.compile(r'(_e|_em|emis|emission|glow|_lum)(\.|_|$)', re.I), EMISSION),
    (re.compile(r'(_l|_lm|light|_ao|occlusion)(\.|_|$)', re.I), LIGHT),
)


def _guess_kind(image):
    name = os.path.basename(image.filepath) or image.name
    for rx, kind in _KIND_BY_NAME:
        if rx.search(name):
            return kind
    return DIFFUSE


def find_material_textures(mat):
    """Return {kind: bpy.types.Image} for *mat* (may be empty)."""
    tex = {}
    if mat is None or not mat.use_nodes or mat.node_tree is None:
        return tex
    tree = mat.node_tree
    groups = [n for n in tree.nodes if n.bl_idname == 'ShaderNodeGroup' and n.node_tree is not None]

    # 1. XPS Shader group (created by XNALaraMesh on import)
    for node in groups:
        if node.node_tree.name.startswith('XPS Shader'):
            for name, kind in _XPS_GROUP_INPUTS.items():
                img = _upstream_image(node.inputs.get(name))
                if img is not None:
                    tex.setdefault(kind, img)
            if tex:
                return tex

    # 2. mmd_tools shader group
    for node in groups:
        if node.node_tree.name.startswith('MMDShader'):
            img = _upstream_image(node.inputs.get('Base Tex'))
            if img is not None:
                tex[DIFFUSE] = img
                return tex

    # 3. Principled BSDF
    principled = _find_principled(tree)
    if principled is not None:
        for name, kind in _PRINCIPLED_INPUTS.items():
            sock = principled.inputs.get(name)
            if sock is None or not sock.is_linked:
                continue
            img = _upstream_image(sock)
            if img is not None:
                tex.setdefault(kind, img)
        if DIFFUSE not in tex:
            img = _upstream_image(principled.inputs.get('Alpha'))
            if img is not None:
                tex[DIFFUSE] = img
        if EMISSION in tex:
            strength = principled.inputs.get('Emission Strength')
            if strength is not None and not strength.is_linked and strength.default_value <= 0.0:
                del tex[EMISSION]
        if tex:
            return tex

    # 4. any other node group with conventional socket names
    for node in groups:
        for name, kind in _GENERIC_GROUP_INPUTS.items():
            sock = node.inputs.get(name)
            if sock is not None and sock.is_linked:
                img = _upstream_image(sock)
                if img is not None:
                    tex.setdefault(kind, img)
        if tex:
            return tex

    # 5. plain image nodes, classified by file name
    for node in tree.nodes:
        if node.bl_idname == 'ShaderNodeTexImage' and node.image is not None:
            tex.setdefault(_guess_kind(node.image), node.image)
    return tex


# --------------------------------------------------------------------------- alpha

def image_has_alpha_channel(image):
    try:
        if image.alpha_mode == 'NONE':
            return False
        return image.depth in (32, 64, 128) and image.channels == 4
    except Exception:
        return False


def image_uses_alpha(image, max_pixels=4096 * 4096):
    """True when the image has an alpha channel with at least some
    non-opaque pixels.  Falls back to the channel test for huge images."""
    if not image_has_alpha_channel(image):
        return False
    try:
        w, h = image.size
        n = w * h
        if n == 0:
            return False
        if n > max_pixels:
            return True
        buf = np.empty(n * 4, dtype=np.float32)
        image.pixels.foreach_get(buf)
        return bool((buf[3::4] < 0.98).any())
    except Exception:
        return True


ALPHA_SAMPLE_THRESHOLD = 0.08     # fraction of UV samples on transparent texels

_alpha_cache = {}


def clear_alpha_cache():
    _alpha_cache.clear()


def _image_alpha_plane(image, max_pixels=8192 * 8192):
    """Return (width, height, alpha float32 array) or None."""
    key = (image.name, tuple(image.size))
    if key in _alpha_cache:
        return _alpha_cache[key]
    result = None
    try:
        w, h = image.size
        if 0 < w * h <= max_pixels and image_has_alpha_channel(image):
            buf = np.empty(w * h * 4, dtype=np.float32)
            image.pixels.foreach_get(buf)
            result = (w, h, np.ascontiguousarray(buf[3::4]))
    except Exception:
        result = None
    _alpha_cache[key] = result
    return result


def alpha_fraction_at_uvs(image, uvs, cutoff=0.5):
    """Fraction of the given Blender UV samples (n, 2) that land on texels
    with alpha < cutoff.  None when the image cannot be inspected.
    *image* may also be a (width, height, alpha array) plane."""
    plane = image if isinstance(image, tuple) else _image_alpha_plane(image)
    if plane is None or uvs is None or len(uvs) == 0:
        return None
    w, h, alpha = plane
    uvs = np.asarray(uvs, dtype=np.float64)
    u = np.mod(uvs[:, 0], 1.0)
    v = np.mod(uvs[:, 1], 1.0)
    col = np.minimum((u * w).astype(np.int64), w - 1)
    row = np.minimum((v * h).astype(np.int64), h - 1)
    samples = alpha[row * w + col]
    return float((samples < cutoff).mean())


def material_is_invisible(mat):
    """Materials that render fully transparent (e.g. mmd_tools eye overlay
    shells with alpha 0, or a lone Transparent BSDF) are not worth exporting."""
    if mat is None:
        return False
    mmd = getattr(mat, 'mmd_material', None)
    if mmd is not None and getattr(mmd, 'alpha', 1.0) <= 0.001:
        return True
    if mat.use_nodes and mat.node_tree is not None:
        nodes = mat.node_tree.nodes
        principled = _find_principled(mat.node_tree)
        if principled is not None:
            alpha = principled.inputs.get('Alpha')
            if alpha is not None and not alpha.is_linked and alpha.default_value <= 0.001:
                return True
        elif any(n.bl_idname == 'ShaderNodeBsdfTransparent' for n in nodes) and \
                not any(n.bl_idname in ('ShaderNodeTexImage', 'ShaderNodeGroup', 'ShaderNodeEmission') for n in nodes):
            return True
    return False


def material_needs_alpha(mat, diffuse_image, mode='AUTO', uv_samples=None, plane=None):
    """Decide whether a part needs an alpha render group.

    In AUTO mode the texture's alpha is sampled at the part's own UVs, so an
    atlas whose unused area is transparent does not force a body part into an
    alpha group, while hair cards / lashes / lace are detected reliably."""
    if mode == 'NEVER':
        return False
    if mode == 'ALWAYS':
        return True
    if mat is None:
        return False
    if 'xps_alpha' in mat.keys():
        return bool(mat['xps_alpha'])
    mmd = getattr(mat, 'mmd_material', None)
    if mmd is not None and getattr(mmd, 'alpha', 1.0) < 0.999:
        return True
    blend = getattr(mat, 'blend_method', None)
    if blend == 'OPAQUE' and plane is None:
        return False
    if plane is not None:
        if uv_samples is None:
            return bool((plane[2] < 0.5).mean() >= ALPHA_SAMPLE_THRESHOLD)
        frac = alpha_fraction_at_uvs(plane, uv_samples)
        return frac is not None and frac >= ALPHA_SAMPLE_THRESHOLD
    if diffuse_image is None or not image_has_alpha_channel(diffuse_image):
        return False
    if uv_samples is not None:
        frac = alpha_fraction_at_uvs(diffuse_image, uv_samples)
        if frac is not None:
            return frac >= ALPHA_SAMPLE_THRESHOLD
    return image_uses_alpha(diffuse_image)


# --------------------------------------------------------------------------- render group

def choose_render_group(kinds, alpha, unlit=False):
    kinds = set(kinds)
    if unlit:
        return 21 if alpha else 10
    bump = BUMP in kinds
    spec = SPEC in kinds
    emis = EMISSION in kinds
    light = LIGHT in kinds
    if bump and spec and emis:
        return 39 if alpha else 38
    if bump and emis:
        return 37 if alpha else 36
    if bump and spec and light:
        return 25 if alpha else 24
    if bump and spec:
        return 41 if alpha else 40
    if bump and light:
        return 8 if alpha else 2
    if bump:
        return 6 if alpha else 4
    if light:
        return 9 if alpha else 3
    return 7 if alpha else 5


def material_base_color(mat):
    """RGBA used for a generated flat texture when no diffuse image exists."""
    if mat is None:
        return (0.8, 0.8, 0.8, 1.0)
    mmd = getattr(mat, 'mmd_material', None)
    if mmd is not None:
        try:
            c = mmd.diffuse_color
            return (c[0], c[1], c[2], 1.0)
        except Exception:
            pass
    if mat.use_nodes and mat.node_tree is not None:
        principled = _find_principled(mat.node_tree)
        if principled is not None:
            c = principled.inputs['Base Color'].default_value
            return (c[0], c[1], c[2], 1.0)
    c = mat.diffuse_color
    return (c[0], c[1], c[2], 1.0)


# --------------------------------------------------------------------------- texture files

def _safe_filename(name, ascii_only=True):
    if ascii_only:
        name = ''.join(ch if ch.isascii() else '' for ch in name)
    name = re.sub(r'[\\/:*?"<>|]+', '-', name)
    name = re.sub(r'\s+', ' ', name).strip(' .-')
    return name or 'texture'


def _linear_to_srgb(c):
    return 12.92 * c if c <= 0.0031308 else 1.055 * (c ** (1.0 / 2.4)) - 0.055


class TextureExporter(object):
    """Copies images into *out_dir* and hands back the file names to write
    into the model.  Every image is processed once."""

    def __init__(self, out_dir, copy=True, ascii_names=True, report=None):
        self.out_dir = out_dir
        self.copy = copy
        self.ascii_names = ascii_names
        self.report = report
        self._by_image = {}
        self._name_owner = {}      # lower-case target name -> source key
        self.written = []

    def _warn(self, msg):
        if self.report is not None:
            self.report.warn(msg)

    def _unique_name(self, base, source_key):
        base = base.strip() or 'texture.png'
        name, ext = os.path.splitext(base)
        cand = base
        n = 2
        while cand.lower() in self._name_owner and self._name_owner[cand.lower()] != source_key:
            cand = '%s-%d%s' % (name, n, ext)
            n += 1
        self._name_owner[cand.lower()] = source_key
        return cand

    def export_image(self, image):
        key = image.name
        if key in self._by_image:
            return self._by_image[key]
        src = ''
        if image.source in ('FILE', 'SEQUENCE'):
            try:
                src = bpy.path.abspath(image.filepath, library=image.library)
            except Exception:
                src = bpy.path.abspath(image.filepath)
            src = os.path.normpath(src) if src else ''
        packed = image.packed_file is not None
        base = os.path.basename(src) if src else image.name
        stem, ext = os.path.splitext(base)
        ext = ext.lower()
        on_disk = bool(src) and os.path.isfile(src)

        if on_disk and ext in IMAGE_EXTENSIONS and not packed:
            target = self._unique_name(_safe_filename(stem, self.ascii_names) + ext, src.lower())
            if self.copy:
                dst = os.path.join(self.out_dir, target)
                if os.path.normcase(os.path.abspath(src)) != os.path.normcase(os.path.abspath(dst)):
                    try:
                        shutil.copy2(src, dst)
                        self.written.append(dst)
                    except Exception as exc:
                        self._warn('复制贴图失败 %s: %s' % (src, exc))
            elif not self.ascii_names or base == target:
                target = base
        else:
            target = self._unique_name(_safe_filename(stem or image.name, self.ascii_names) + '.png', src.lower() or key)
            if self.copy:
                dst = os.path.join(self.out_dir, target)
                if not self._save_png(image, dst):
                    self._warn('贴图 %s 无法保存为 PNG（源文件缺失?），模型里仍引用 %s' % (image.name, target))
        self._by_image[key] = target
        return target

    def _save_png(self, image, dst):
        """Convert any image (packed, generated, .hdr/.exr/.tif ...) to an
        8-bit PNG by reading its pixels; the source image is left untouched."""
        try:
            if not image.has_data:
                image.reload()
            w, h = image.size
            if w == 0 or h == 0:
                raise RuntimeError('image has no pixel data')
            buf = np.empty(w * h * 4, dtype=np.float32)
            image.pixels.foreach_get(buf)
            rgba = buf.reshape(-1, 4)
            if not save_rgba_png(dst, w, h, rgba):
                raise RuntimeError('PNG write failed')
            self.written.append(dst)
            return True
        except Exception as exc:
            self._warn('保存 %s 失败: %s' % (dst, exc))
            return False

    def save_baked(self, baked):
        """Write a BakedTexture next to the model; returns the file name."""
        key = 'baked:' + baked.name
        if key in self._by_image:
            return self._by_image[key]
        target = self._unique_name(_safe_filename(baked.name, True) + '_baked.png', key)
        if self.copy:
            dst = os.path.join(self.out_dir, target)
            if save_rgba_png(dst, baked.width, baked.height, baked.rgba):
                self.written.append(dst)
            else:
                self._warn('保存烘焙贴图失败: %s' % target)
        self._by_image[key] = target
        return target

    def flat_color(self, mat, label):
        """Write a tiny PNG filled with the material colour; returns its name."""
        key = 'flat:' + (mat.name if mat is not None else label)
        if key in self._by_image:
            return self._by_image[key]
        stem = _safe_filename((mat.name if mat is not None else label), True) or 'material'
        target = self._unique_name(stem + '_diffuse.png', key)
        if self.copy:
            rgba = material_base_color(mat)
            rgba = tuple(min(1.0, max(0.0, _linear_to_srgb(c))) for c in rgba[:3]) + (1.0,)
            img = None
            try:
                img = bpy.data.images.new('blender2xps_flat', 4, 4, alpha=True)
                img.pixels = list(rgba) * 16
                img.filepath_raw = os.path.join(self.out_dir, target)
                img.file_format = 'PNG'
                img.save()
                self.written.append(img.filepath_raw)
            except Exception as exc:
                self._warn('生成纯色贴图失败 %s: %s' % (target, exc))
            finally:
                if img is not None:
                    try:
                        bpy.data.images.remove(img)
                    except Exception:
                        pass
        self._by_image[key] = target
        return target


# --------------------------------------------------------------------------- baking
# Materials whose colour or alpha is produced by nodes (colour ramps, channel
# splits, procedural irises ...) have no texture XPS could use.  We bake the
# Principled "Base Color" and "Alpha" inputs into an RGBA image on the mesh's
# first UV layer using a Cycles emission bake (exact colours, no lighting).

_SIMPLE_COLOR_NODES = {'ShaderNodeGamma', 'ShaderNodeHueSaturation', 'ShaderNodeBrightContrast',
                       'ShaderNodeInvert', 'ShaderNodeRGBCurve', 'NodeReroute'}
TINT_TOLERANCE = 0.1


class BakedTexture(object):
    __slots__ = ('name', 'width', 'height', 'rgba')

    def __init__(self, name, width, height, rgba):
        self.name = name
        self.width = width
        self.height = height
        self.rgba = rgba          # float32 (w*h, 4), scene linear, straight alpha

    def alpha_plane(self):
        return (self.width, self.height, np.ascontiguousarray(self.rgba[:, 3]))


def _mix_color_inputs(node):
    """(factor socket, [colour input sockets]) for MixRGB (3.x) / Mix (4.x)."""
    if node.bl_idname == 'ShaderNodeMixRGB':
        return node.inputs.get('Fac'), [node.inputs.get('Color1'), node.inputs.get('Color2')]
    if node.bl_idname == 'ShaderNodeMix':
        fac = node.inputs.get('Factor')
        cols = [s for s in node.inputs if s.type == 'RGBA' and s.enabled]
        return fac, cols[:2]
    return None, []


def _color_chain_is_simple(socket, depth=0):
    """True when an input is a constant or an image reached through nothing
    but mild colour adjustments (near-white tint, gamma, hue/sat ...)."""
    if socket is None or not socket.is_linked:
        return True
    if depth > 16:
        return False
    link = socket.links[0]
    node = link.from_node
    if node.bl_idname == 'ShaderNodeTexImage':
        return link.from_socket.name == 'Color' and node.image is not None
    if node.bl_idname in _SIMPLE_COLOR_NODES:
        color_in = next((s for s in node.inputs if s.type == 'RGBA'), None)
        return _color_chain_is_simple(color_in, depth + 1)
    if node.bl_idname in ('ShaderNodeMixRGB', 'ShaderNodeMix'):
        fac, cols = _mix_color_inputs(node)
        if fac is not None and fac.is_linked:
            return False
        linked = [s for s in cols if s is not None and s.is_linked]
        const = [s for s in cols if s is not None and not s.is_linked]
        if len(linked) != 1 or len(const) != 1:
            return False
        c = const[0].default_value
        if any(abs(1.0 - c[i]) > TINT_TOLERANCE for i in range(3)):
            return False            # a real tint: bake so the colour survives
        return _color_chain_is_simple(linked[0], depth + 1)
    return False


def material_wants_bake(mat, mode='AUTO'):
    """Decide whether *mat* needs its colour/alpha baked for XPS."""
    if mode == 'OFF' or mat is None or not mat.use_nodes or mat.node_tree is None:
        return False
    tree = mat.node_tree
    for node in tree.nodes:
        if node.bl_idname == 'ShaderNodeGroup' and node.node_tree is not None and \
                node.node_tree.name.startswith(('XPS Shader', 'MMDShader')):
            return False                      # handled natively
    principled = _find_principled(tree)
    if principled is None:
        if mode == 'ALL':
            return any(n.bl_idname == 'ShaderNodeTexImage' and n.image for n in tree.nodes)
        return False
    if mode == 'ALL':
        return True
    base = principled.inputs.get('Base Color')
    alpha = principled.inputs.get('Alpha')
    if not _color_chain_is_simple(base):
        return True
    if alpha is not None and alpha.is_linked:
        link = alpha.links[0]
        img = _upstream_image(base) if base is not None else None
        own_alpha = (link.from_node.bl_idname == 'ShaderNodeTexImage' and link.from_socket.name == 'Alpha'
                     and link.from_node.image is not None and img is not None
                     and link.from_node.image.name == img.name)
        if not own_alpha:
            return True
    return False


def material_is_translucent_shell(mat):
    """Semi transparent helper geometry (eye occlusion shells, tear lines,
    fake reflection cards): constant alpha below 0.5, or an alpha scaled
    below 0.5 by a multiply node.  Such parts only look wrong in XPS."""
    if mat is None or not mat.use_nodes or mat.node_tree is None:
        return False
    principled = _find_principled(mat.node_tree)
    if principled is None:
        return False
    alpha = principled.inputs.get('Alpha')
    if alpha is None:
        return False
    if not alpha.is_linked:
        return 0.001 < alpha.default_value < 0.5
    node = alpha.links[0].from_node
    if node.bl_idname == 'ShaderNodeMath' and node.operation == 'MULTIPLY':
        consts = [s.default_value for s in node.inputs[:2] if not s.is_linked]
        return bool(consts) and max(consts) < 0.5
    return False


def bake_size_for(mat, requested=0, cap=2048):
    if requested:
        return int(requested)
    sizes = []
    for n in mat.node_tree.nodes:
        if n.bl_idname == 'ShaderNodeTexImage' and n.image is not None and n.image.size[0] > 0:
            sizes.append(max(n.image.size))
    s = max(sizes) if sizes else 1024
    return int(min(cap, max(512, s)))


def _read_pixels(image):
    w, h = image.size
    buf = np.empty(w * h * 4, dtype=np.float32)
    image.pixels.foreach_get(buf)
    return buf.reshape(-1, 4)


def bake_object_materials(context, obj, mats, size=0, report=None):
    """Bake colour + alpha of the given materials of *obj*.

    Returns {material name: BakedTexture}.  The scene, the object and its
    materials are restored afterwards."""
    results = {}
    mats = [m for m in mats if m is not None]
    if not mats or obj.data is None or not obj.data.uv_layers:
        return results
    scene = context.scene
    render = scene.render
    cycles = getattr(scene, 'cycles', None)
    saved = {
        'engine': render.engine,
        'use_clear': render.bake.use_clear,
        'margin': render.bake.margin,
        'sel_to_active': render.bake.use_selected_to_active,
        'target': render.bake.target,
        'samples': cycles.samples if cycles else None,
        'bake_type': cycles.bake_type if cycles else None,
    }
    originals = [slot.material for slot in obj.material_slots]
    uv_state = [(uv.name, uv.active_render) for uv in obj.data.uv_layers]
    active_before = context.view_layer.objects.active
    selected_before = [o for o in context.view_layer.objects if o.select_get()]
    temp_mats = []
    temp_images = []
    targets = {}
    want = {m.name for m in mats}
    try:
        render.engine = 'CYCLES'
        if cycles:
            cycles.samples = 1
            cycles.bake_type = 'EMIT'
        render.bake.use_clear = True
        render.bake.margin = 8
        render.bake.use_selected_to_active = False
        render.bake.target = 'IMAGE_TEXTURES'
        for uv in obj.data.uv_layers:
            uv.active_render = False
        obj.data.uv_layers[0].active_render = True

        for slot in obj.material_slots:
            mat = slot.material
            if mat is None:
                continue
            if mat.name in targets:
                slot.material = targets[mat.name]['copy']
                continue
            copy = mat.copy()
            copy.name = '__b2x_bake_%s' % mat.name
            temp_mats.append(copy)
            tree = copy.node_tree
            node = tree.nodes.new('ShaderNodeTexImage')
            if mat.name in want:
                s = bake_size_for(mat, size)
                img = bpy.data.images.new('__b2x_bake_%s' % mat.name, s, s, alpha=True)
                temp_images.append(img)
                node.image = img
                principled = _find_principled(tree)
                out = None
                for n in tree.nodes:
                    if n.bl_idname == 'ShaderNodeOutputMaterial' and (out is None or n.is_active_output):
                        out = n
                if out is None:
                    out = tree.nodes.new('ShaderNodeOutputMaterial')
                emit = tree.nodes.new('ShaderNodeEmission')
                alpha_src = None
                alpha_const = 1.0
                if principled is not None:
                    base = principled.inputs.get('Base Color')
                    if base is not None and base.is_linked:
                        tree.links.new(base.links[0].from_socket, emit.inputs['Color'])
                    elif base is not None:
                        emit.inputs['Color'].default_value = tuple(base.default_value)
                    alpha = principled.inputs.get('Alpha')
                    if alpha is not None:
                        if alpha.is_linked:
                            alpha_src = alpha.links[0].from_socket
                        else:
                            alpha_const = float(alpha.default_value)
                else:
                    surf = out.inputs['Surface']
                    if surf.is_linked:
                        emit = None           # bake whatever feeds the output (lit, but better than nothing)
                if emit is not None:
                    for l in list(out.inputs['Surface'].links):
                        tree.links.remove(l)
                    tree.links.new(emit.outputs['Emission'], out.inputs['Surface'])
                targets[mat.name] = {'mat': mat, 'copy': copy, 'img': img, 'emit': emit,
                                     'alpha_src': alpha_src, 'alpha_const': alpha_const, 'size': s}
            else:
                dummy = bpy.data.images.new('__b2x_dummy', 8, 8)
                temp_images.append(dummy)
                node.image = dummy
            tree.nodes.active = node
            slot.material = copy

        for o in selected_before:
            try:
                o.select_set(False)
            except Exception:
                pass
        obj.select_set(True)
        context.view_layer.objects.active = obj

        def run_bake():
            override = dict(active_object=obj, selected_objects=[obj], object=obj,
                            selected_editable_objects=[obj])
            with context.temp_override(**override):
                bpy.ops.object.bake(type='EMIT')

        run_bake()
        colors = {name: _read_pixels(t['img']) for name, t in targets.items()}

        # second pass: alpha as emission colour
        for name, t in targets.items():
            emit = t['emit']
            if emit is None:
                continue
            tree = t['copy'].node_tree
            for l in list(emit.inputs['Color'].links):
                tree.links.remove(l)
            if t['alpha_src'] is not None:
                tree.links.new(t['alpha_src'], emit.inputs['Color'])
            else:
                a = t['alpha_const']
                emit.inputs['Color'].default_value = (a, a, a, 1.0)
        run_bake()
        for name, t in targets.items():
            rgba = colors[name]
            if t['emit'] is not None:
                alpha = _read_pixels(t['img'])[:, 0]
            else:
                alpha = rgba[:, 3]
            rgba = rgba.copy()
            rgba[:, 3] = np.clip(alpha, 0.0, 1.0)
            results[name] = BakedTexture(name, t['size'], t['size'], rgba)
            if report is not None:
                report.note('  烘焙材质 %s -> %dx%d' % (name, t['size'], t['size']))
    except Exception as exc:
        if report is not None:
            report.warn('烘焙 %s 失败: %s' % (obj.name, exc))
    finally:
        for slot, mat in zip(obj.material_slots, originals):
            try:
                slot.material = mat
            except Exception:
                pass
        for uv in obj.data.uv_layers:
            for name, flag in uv_state:
                if uv.name == name:
                    uv.active_render = flag
        for m in temp_mats:
            try:
                bpy.data.materials.remove(m)
            except Exception:
                pass
        for img in temp_images:
            try:
                bpy.data.images.remove(img)
            except Exception:
                pass
        render.engine = saved['engine']
        render.bake.use_clear = saved['use_clear']
        render.bake.margin = saved['margin']
        render.bake.use_selected_to_active = saved['sel_to_active']
        render.bake.target = saved['target']
        if cycles:
            cycles.samples = saved['samples']
            cycles.bake_type = saved['bake_type']
        try:
            obj.select_set(False)
            for o in selected_before:
                o.select_set(True)
            context.view_layer.objects.active = active_before
        except Exception:
            pass
    return results


def save_rgba_png(path, width, height, rgba):
    """Write a scene-linear float RGBA array as an 8-bit sRGB PNG."""
    img = None
    try:
        img = bpy.data.images.new('__b2x_save', width, height, alpha=True)
        img.alpha_mode = 'STRAIGHT'
        img.pixels.foreach_set(np.ascontiguousarray(rgba, dtype=np.float32).ravel())
        img.filepath_raw = path
        img.file_format = 'PNG'
        img.save()
        return True
    except Exception:
        return False
    finally:
        if img is not None:
            try:
                bpy.data.images.remove(img)
            except Exception:
                pass
