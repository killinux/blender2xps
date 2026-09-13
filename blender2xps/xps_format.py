# -*- coding: utf-8 -*-
"""XPS / XNALara model file format: in-memory model, writers and a reader.

This module has NO Blender dependency so it can be reused by command line tools
and tests.  Format knowledge comes from the GLLara documentation, the
johnzero7/XNALaraMesh reference implementation and byte-level inspection of
files written by XPS 11.7 / 11.8 itself.

Coordinate system stored in the file: X right, Y up, Z towards the viewer.
Triangles are stored clockwise (XNA / DirectX convention).

Supported container variants (``FMT_*``):

``XPS3``   header, version 3.15  - variable weight count per vertex, no tangents (XPS 11.8.9+)
``XPS2``   header, version 2.15  - fixed 4 weights per vertex, no tangents (what XPS 11.7/11.8 writes)
``MESH``   no header ("classic")  - fixed 4 weights per vertex, 4-float tangent per UV layer (XNALara)
``ASCII``  text (.mesh.ascii)     - variable weight count, no tangents
"""

import io
import os
import struct

MAGIC = 323232
XNA_ARAL = 'XNAaraL'
ENCODING = 'utf-8'

FMT_XPS3 = 'XPS3'
FMT_XPS2 = 'XPS2'
FMT_MESH = 'MESH'
FMT_ASCII = 'ASCII'
ALL_FORMATS = (FMT_XPS3, FMT_XPS2, FMT_MESH, FMT_ASCII)

FIXED_WEIGHTS = 4


# --------------------------------------------------------------------------- model

class XpsBone(object):
    __slots__ = ('name', 'parent', 'pos')

    def __init__(self, name, parent, pos):
        self.name = name
        self.parent = parent          # -1 for root
        self.pos = tuple(pos)         # XPS space


class XpsTexture(object):
    __slots__ = ('file', 'uv_layer')

    def __init__(self, file, uv_layer=0):
        self.file = file
        self.uv_layer = uv_layer


class XpsMesh(object):
    """One render part.  All per-vertex lists have the same length."""
    __slots__ = ('name', 'uv_count', 'textures', 'positions', 'normals', 'colors',
                 'uvs', 'bone_ids', 'bone_weights', 'faces', 'tangents')

    def __init__(self, name, uv_count=1, textures=None):
        self.name = name
        self.uv_count = uv_count
        self.textures = textures or []
        self.positions = []      # [(x, y, z)]
        self.normals = []        # [(x, y, z)]
        self.colors = []         # [(r, g, b, a)] 0..255
        self.uvs = []            # [[(u, v), ...uv_count]]
        self.bone_ids = []       # [[int, ...]]
        self.bone_weights = []   # [[float, ...]]
        self.faces = []          # [(a, b, c)]
        self.tangents = None     # optional [[(x, y, z, w), ...uv_count]]

    @property
    def vertex_count(self):
        return len(self.positions)


class XpsModel(object):
    def __init__(self, bones=None, meshes=None):
        self.bones = bones if bones is not None else []
        self.meshes = meshes if meshes is not None else []
        # informational, filled by the reader
        self.fmt = None
        self.version = None
        self.header_info = {}


# --------------------------------------------------------------------------- helpers

def format_from_path(path, default=FMT_XPS3):
    low = path.lower()
    if low.endswith('.ascii'):
        return FMT_ASCII
    return default


def extension_for_format(fmt, current_path=''):
    """Return the file extension a format should use, keeping a user supplied
    .mesh/.xps choice for the header formats."""
    low = current_path.lower()
    if fmt == FMT_ASCII:
        return '.mesh.ascii'
    if fmt == FMT_MESH:
        return '.mesh'
    if low.endswith('.mesh'):
        return '.mesh'
    return '.xps'


def fix_extension(path, fmt):
    """Replace/append the extension so it matches *fmt*."""
    low = path.lower()
    base = path
    for ext in ('.mesh.ascii', '.ascii', '.mesh', '.xps'):
        if low.endswith(ext):
            base = path[:-len(ext)]
            break
    return base + extension_for_format(fmt, path)


def encode_string(text):
    """.NET BinaryWriter string: 7-bit encoded byte length + UTF-8 bytes."""
    data = text.encode(ENCODING)
    n = len(data)
    out = bytearray()
    while True:
        byte = n & 0x7F
        n >>= 7
        if n:
            out.append(byte | 0x80)
        else:
            out.append(byte)
            break
    out += data
    return bytes(out)


def _fmt_float(v):
    v = float(v) + 0.0          # kills negative zero
    return '{:.7G}'.format(v)


# --------------------------------------------------------------------------- binary writer

def _write_header(buf, major, minor, pose_text='', machine='', user='', files=''):
    buf += struct.pack('<I', MAGIC)
    buf += struct.pack('<HH', major, minor)
    buf += encode_string(XNA_ARAL)

    pose_bytes = pose_text.encode(ENCODING)
    pose_len = len(pose_bytes)
    pad = (-pose_len) % 4
    pose_bone_count = pose_text.count('\n')

    settings = bytearray()
    settings += struct.pack('<II', 6, 3)                       # settings "hash" + item count (as XPS writes)
    settings += struct.pack('<III', 1, pose_len, pose_bone_count)
    settings += pose_bytes + b'\0' * pad                       # default pose (optional)
    settings += struct.pack('<III', 2, 4, 4)                   # render flags block
    settings += struct.pack('<8I', 2, 1, 3, 0, 4, 3, 5, 4)     # cast shadows, tangent space X+ Y- Z+
    settings += struct.pack('<III', 0, 256, 0)                 # empty block
    settings += b'\0' * (256 * 4)

    buf += struct.pack('<I', len(settings) // 4)
    buf += encode_string(machine)
    buf += encode_string(user)
    buf += encode_string(files)
    buf += settings


def _pack_weights_fixed(ids, weights):
    ids = list(ids)[:FIXED_WEIGHTS]
    weights = list(weights)[:FIXED_WEIGHTS]
    while len(ids) < FIXED_WEIGHTS:
        ids.append(0)
        weights.append(0.0)
    return struct.pack('<4H', *ids) + struct.pack('<4f', *weights)


def _pack_weights_variable(ids, weights):
    ids = list(ids)
    weights = list(weights)
    while len(ids) < FIXED_WEIGHTS:      # XPS files in the wild never carry fewer than 4 entries
        ids.append(0)
        weights.append(0.0)
    n = len(ids)
    return (struct.pack('<H', n) + struct.pack('<%dH' % n, *ids)
            + struct.pack('<%df' % n, *weights))


def write_binary(model, fmt, pose_text='', machine='', user='', files=''):
    """Serialise *model* to bytes in one of the binary formats."""
    if fmt not in (FMT_XPS3, FMT_XPS2, FMT_MESH):
        raise ValueError('not a binary format: %r' % fmt)
    buf = bytearray()
    if fmt == FMT_XPS3:
        _write_header(buf, 3, 15, pose_text, machine, user, files)
    elif fmt == FMT_XPS2:
        _write_header(buf, 2, 15, pose_text, machine, user, files)
    has_tangent = fmt == FMT_MESH
    variable = fmt == FMT_XPS3
    has_bones = len(model.bones) > 0

    buf += struct.pack('<I', len(model.bones))
    for bone in model.bones:
        buf += encode_string(bone.name)
        buf += struct.pack('<h', -1 if bone.parent is None or bone.parent < 0 else bone.parent)
        buf += struct.pack('<3f', *bone.pos)

    buf += struct.pack('<I', len(model.meshes))
    pack3f = struct.Struct('<3f').pack
    pack2f = struct.Struct('<2f').pack
    pack4f = struct.Struct('<4f').pack
    pack4B = struct.Struct('<4B').pack
    for mesh in model.meshes:
        buf += encode_string(mesh.name)
        buf += struct.pack('<I', mesh.uv_count)
        buf += struct.pack('<I', len(mesh.textures))
        for tex in mesh.textures:
            buf += encode_string(tex.file)
            buf += struct.pack('<I', tex.uv_layer)
        n = mesh.vertex_count
        buf += struct.pack('<I', n)
        tangents = mesh.tangents
        for i in range(n):
            buf += pack3f(*mesh.positions[i])
            buf += pack3f(*mesh.normals[i])
            buf += pack4B(*mesh.colors[i])
            uvs = mesh.uvs[i]
            for layer in range(mesh.uv_count):
                buf += pack2f(*uvs[layer])
                if has_tangent:
                    if tangents is not None:
                        buf += pack4f(*tangents[i][layer])
                    else:
                        buf += pack4f(1.0, 0.0, 0.0, 1.0)
            if has_bones:
                if variable:
                    buf += _pack_weights_variable(mesh.bone_ids[i], mesh.bone_weights[i])
                else:
                    buf += _pack_weights_fixed(mesh.bone_ids[i], mesh.bone_weights[i])
        buf += struct.pack('<I', len(mesh.faces))
        for face in mesh.faces:
            buf += struct.pack('<3I', *face)
    return bytes(buf)


# --------------------------------------------------------------------------- ascii writer

def write_ascii(model):
    out = io.StringIO()
    w = out.write
    w('%d # bones\n' % len(model.bones))
    for bone in model.bones:
        w(bone.name + '\n')
        w('%d # parent index\n' % (-1 if bone.parent is None or bone.parent < 0 else bone.parent))
        w('%s %s %s\n' % tuple(_fmt_float(v) for v in bone.pos))
    w('%d # meshes\n' % len(model.meshes))
    has_bones = len(model.bones) > 0
    for mesh in model.meshes:
        w(mesh.name + '\n')
        w('%d # uv layers\n' % mesh.uv_count)
        w('%d # textures\n' % len(mesh.textures))
        for tex in mesh.textures:
            w(tex.file + '\n')
            w('%d # uv layer index\n' % tex.uv_layer)
        n = mesh.vertex_count
        w('%d # vertices\n' % n)
        for i in range(n):
            w('%s %s %s\n' % tuple(_fmt_float(v) for v in mesh.positions[i]))
            w('%s %s %s\n' % tuple(_fmt_float(v) for v in mesh.normals[i]))
            w('%d %d %d %d\n' % tuple(int(c) for c in mesh.colors[i]))
            uvs = mesh.uvs[i]
            for layer in range(mesh.uv_count):
                w('%s %s\n' % (_fmt_float(uvs[layer][0]), _fmt_float(uvs[layer][1])))
            if has_bones:
                ids = list(mesh.bone_ids[i])
                weights = list(mesh.bone_weights[i])
                while len(ids) < FIXED_WEIGHTS:
                    ids.append(0)
                    weights.append(0.0)
                w(' '.join(str(int(b)) for b in ids) + '\n')
                w(' '.join(_fmt_float(x) for x in weights) + '\n')
        w('%d # faces\n' % len(mesh.faces))
        for face in mesh.faces:
            w('%d %d %d\n' % tuple(face))
    return out.getvalue()


def write_model_file(model, path, fmt, **header_kw):
    if fmt == FMT_ASCII:
        with open(path, 'w', encoding=ENCODING, newline='\n') as f:
            f.write(write_ascii(model))
    else:
        data = write_binary(model, fmt, **header_kw)
        with open(path, 'wb') as f:
            f.write(data)


# --------------------------------------------------------------------------- reader

class _Bin(object):
    def __init__(self, data):
        self.data = data
        self.pos = 0

    def unpack(self, fmt):
        st = struct.Struct(fmt)
        vals = st.unpack_from(self.data, self.pos)
        self.pos += st.size
        return vals

    def u8(self):
        return self.unpack('<B')[0]

    def u16(self):
        return self.unpack('<H')[0]

    def i16(self):
        return self.unpack('<h')[0]

    def u32(self):
        return self.unpack('<I')[0]

    def f32(self):
        return self.unpack('<f')[0]

    def string(self):
        n = 0
        shift = 0
        while True:
            b = self.u8()
            n |= (b & 0x7F) << shift
            shift += 7
            if not b & 0x80:
                break
        raw = self.data[self.pos:self.pos + n]
        self.pos += n
        try:
            return raw.decode(ENCODING)
        except UnicodeDecodeError:
            return raw.decode('latin-1')

    def skip(self, n):
        self.pos += n

    @property
    def remaining(self):
        return len(self.data) - self.pos


def _read_header(b, model):
    info = {}
    b.u32()  # magic
    major, minor = b.unpack('<HH')
    info['version'] = (major, minor)
    info['tool'] = b.string()
    settings_len = b.u32()
    info['machine'] = b.string()
    info['user'] = b.string()
    info['files'] = b.string()
    start = b.pos
    old_settings = (minor <= 12 and major <= 2)
    if old_settings:
        b.skip(settings_len * 4)
    else:
        info['settings_hash'] = b.u32()
        items = b.u32()
        for _ in range(items):
            kind = b.u32()
            count = b.u32()
            extra = b.u32()
            if kind == 0:
                b.skip(count * 4)
            elif kind == 1:
                padded = (count + 3) // 4 * 4
                pose = b.data[b.pos:b.pos + count]
                b.skip(padded)
                info['pose'] = pose.decode(ENCODING, 'replace')
                info['pose_bones'] = extra
            elif kind == 2:
                flags = {}
                for _f in range(count):
                    flag, value = b.unpack('<II')
                    flags[flag] = value
                info['flags'] = flags
            else:
                break
        b.pos = start + settings_len * 4
    model.version = info['version']
    model.header_info = info
    return info


def read_binary(data):
    b = _Bin(data)
    model = XpsModel()
    has_header = len(data) >= 4 and struct.unpack_from('<I', data, 0)[0] == MAGIC
    if has_header:
        info = _read_header(b, model)
        major, minor = info['version']
        has_tangent = (minor <= 12 and major <= 2)
        variable = major >= 3
        model.fmt = FMT_XPS3 if variable else FMT_XPS2
    else:
        has_tangent = True
        variable = False
        model.fmt = FMT_MESH
        model.version = (0, 0)

    n_bones = b.u32()
    for _ in range(n_bones):
        name = b.string()
        parent = b.i16()
        pos = b.unpack('<3f')
        model.bones.append(XpsBone(name, parent, pos))
    has_bones = n_bones > 0

    n_meshes = b.u32()
    for _ in range(n_meshes):
        name = b.string()
        uv_count = b.u32()
        n_tex = b.u32()
        textures = []
        for _t in range(n_tex):
            tex = b.string()
            layer = b.u32()
            textures.append(XpsTexture(tex, layer))
        mesh = XpsMesh(name, uv_count, textures)
        n_verts = b.u32()
        if has_tangent:
            mesh.tangents = []
        for _v in range(n_verts):
            mesh.positions.append(b.unpack('<3f'))
            mesh.normals.append(b.unpack('<3f'))
            mesh.colors.append(b.unpack('<4B'))
            uvs = []
            tans = []
            for _l in range(uv_count):
                uvs.append(b.unpack('<2f'))
                if has_tangent:
                    tans.append(b.unpack('<4f'))
            mesh.uvs.append(uvs)
            if has_tangent:
                mesh.tangents.append(tans)
            if has_bones:
                count = b.u16() if variable else FIXED_WEIGHTS
                ids = list(b.unpack('<%dH' % count)) if count else []
                weights = list(b.unpack('<%df' % count)) if count else []
                mesh.bone_ids.append(ids)
                mesh.bone_weights.append(weights)
            else:
                mesh.bone_ids.append([])
                mesh.bone_weights.append([])
        n_faces = b.u32()
        for _f in range(n_faces):
            mesh.faces.append(b.unpack('<3I'))
        model.meshes.append(mesh)
    model.header_info['trailing_bytes'] = b.remaining
    return model


def _ascii_lines(text):
    for line in text.splitlines():
        yield line


class _AsciiReader(object):
    def __init__(self, text):
        self.lines = text.splitlines()
        self.i = 0

    def line(self):
        while self.i < len(self.lines):
            s = self.lines[self.i]
            self.i += 1
            if s.strip():
                return s
        raise EOFError('unexpected end of ascii model')

    def string(self):
        return self.line().split('#')[0].strip()

    def ints(self):
        return [int(float(v)) for v in self.line().split('#')[0].split()]

    def floats(self):
        return [float(v) for v in self.line().split('#')[0].split()]

    def int(self):
        return self.ints()[0]


def read_ascii(text):
    r = _AsciiReader(text)
    model = XpsModel()
    model.fmt = FMT_ASCII
    n_bones = r.int()
    for _ in range(n_bones):
        name = r.string()
        parent = r.int()
        pos = r.floats()[:3]
        model.bones.append(XpsBone(name, parent, pos))
    has_bones = n_bones > 0
    n_meshes = r.int()
    for _ in range(n_meshes):
        name = r.string()
        uv_count = r.int()
        n_tex = r.int()
        textures = []
        for _t in range(n_tex):
            tex = r.string()
            layer = r.int()
            textures.append(XpsTexture(tex, layer))
        mesh = XpsMesh(name, uv_count, textures)
        n_verts = r.int()
        for _v in range(n_verts):
            mesh.positions.append(tuple(r.floats()[:3]))
            mesh.normals.append(tuple(r.floats()[:3]))
            mesh.colors.append(tuple(r.ints()[:4]))
            uvs = []
            for _l in range(uv_count):
                uvs.append(tuple(r.floats()[:2]))
            mesh.uvs.append(uvs)
            if has_bones:
                ids = r.ints()
                weights = r.floats()
                mesh.bone_ids.append(ids)
                mesh.bone_weights.append(weights)
            else:
                mesh.bone_ids.append([])
                mesh.bone_weights.append([])
        n_faces = r.int()
        for _f in range(n_faces):
            mesh.faces.append(tuple(r.ints()[:3]))
        model.meshes.append(mesh)
    return model


def read_model_file(path):
    with open(path, 'rb') as f:
        data = f.read()
    if path.lower().endswith('.ascii'):
        try:
            text = data.decode(ENCODING)
        except UnicodeDecodeError:
            text = data.decode('latin-1')
        return read_ascii(text)
    return read_binary(data)


# --------------------------------------------------------------------------- verification

def verify_model(model, weight_tolerance=0.01):
    """Structural checks that mirror what XPS needs to load a file.

    Returns (errors, warnings, stats)."""
    errors = []
    warnings = []
    n_bones = len(model.bones)
    seen = {}
    for i, bone in enumerate(model.bones):
        if bone.parent is not None and bone.parent >= 0:
            if bone.parent >= n_bones:
                errors.append('bone %d %r: parent index %d out of range' % (i, bone.name, bone.parent))
            elif bone.parent == i:
                errors.append('bone %d %r is its own parent' % (i, bone.name))
        key = bone.name.strip().lower()
        if key in seen:
            warnings.append('duplicate bone name (case-insensitive): %r (#%d and #%d)' % (bone.name, seen[key], i))
        else:
            seen[key] = i
        if not bone.name.strip():
            errors.append('bone %d has an empty name' % i)
    # cycles
    for i in range(n_bones):
        j = i
        steps = 0
        while j >= 0 and steps <= n_bones:
            j = model.bones[j].parent if model.bones[j].parent is not None else -1
            steps += 1
        if steps > n_bones:
            errors.append('bone hierarchy has a cycle involving bone %d %r' % (i, model.bones[i].name))
            break

    total_verts = 0
    total_faces = 0
    unweighted = 0
    bad_sum = 0
    max_weights = 0
    mesh_names = {}
    for m_idx, mesh in enumerate(model.meshes):
        n = mesh.vertex_count
        total_verts += n
        total_faces += len(mesh.faces)
        if not mesh.name.strip():
            errors.append('mesh %d has an empty name' % m_idx)
        key = mesh.name.lower()
        if key in mesh_names:
            warnings.append('duplicate mesh name: %r' % mesh.name)
        mesh_names[key] = m_idx
        if n < 3 or not mesh.faces:
            warnings.append('mesh %r has no faces' % mesh.name)
        if n > 65535:
            warnings.append('mesh %r has %d vertices (> 65535); very old XNALara builds cannot load it' % (mesh.name, n))
        if len(mesh.normals) != n or len(mesh.colors) != n or len(mesh.uvs) != n:
            errors.append('mesh %r: per-vertex arrays have inconsistent lengths' % mesh.name)
        for face in mesh.faces:
            if max(face) >= n:
                errors.append('mesh %r: face index %d >= vertex count %d' % (mesh.name, max(face), n))
                break
        if n_bones:
            for i in range(n):
                ids = mesh.bone_ids[i]
                weights = mesh.bone_weights[i]
                if len(ids) != len(weights):
                    errors.append('mesh %r vertex %d: %d bone ids but %d weights' % (mesh.name, i, len(ids), len(weights)))
                    break
                active = [(b, w) for b, w in zip(ids, weights) if w > 0.0]
                max_weights = max(max_weights, len(active))
                for b, _w in active:
                    if b >= n_bones:
                        errors.append('mesh %r vertex %d: bone index %d out of range' % (mesh.name, i, b))
                        break
                s = sum(w for _b, w in active)
                if s <= 1e-6:
                    unweighted += 1
                elif abs(s - 1.0) > weight_tolerance:
                    bad_sum += 1
    if unweighted:
        warnings.append('%d vertices have no bone weight at all (they will collapse to the origin in XPS)' % unweighted)
    if bad_sum:
        warnings.append('%d vertices have weights that do not sum to 1.0' % bad_sum)
    stats = {
        'bones': n_bones,
        'meshes': len(model.meshes),
        'vertices': total_verts,
        'faces': total_faces,
        'max_weights_per_vertex': max_weights,
        'unweighted_vertices': unweighted,
        'weights_not_normalized': bad_sum,
    }
    return errors, warnings, stats


def model_bounds(model):
    """Axis aligned bounds of all vertices in XPS space, or None."""
    lo = [float('inf')] * 3
    hi = [float('-inf')] * 3
    found = False
    for mesh in model.meshes:
        for p in mesh.positions:
            found = True
            for k in range(3):
                if p[k] < lo[k]:
                    lo[k] = p[k]
                if p[k] > hi[k]:
                    hi[k] = p[k]
    return (tuple(lo), tuple(hi)) if found else None


def describe_model(model, max_meshes=50):
    lines = []
    lines.append('format: %s  version: %s' % (model.fmt, model.version))
    hi = model.header_info
    if hi:
        lines.append('header: %s' % {k: v for k, v in hi.items() if k not in ('pose',)})
    lines.append('bones: %d' % len(model.bones))
    roots = [b.name for b in model.bones if b.parent is None or b.parent < 0]
    lines.append('  roots: %s' % roots[:10])
    for b in model.bones[:8]:
        lines.append('  %-30s parent=%-4d pos=(%.4f, %.4f, %.4f)' % (b.name, b.parent, b.pos[0], b.pos[1], b.pos[2]))
    if len(model.bones) > 8:
        lines.append('  ...')
    lines.append('meshes: %d' % len(model.meshes))
    for m in model.meshes[:max_meshes]:
        tex = ', '.join(t.file for t in m.textures)
        lines.append('  %-40s verts=%-7d faces=%-7d uv=%d tex=[%s]' % (m.name, m.vertex_count, len(m.faces), m.uv_count, tex))
    bounds = model_bounds(model)
    if bounds:
        lo, hi_ = bounds
        lines.append('bounds (XPS space): min=(%.3f, %.3f, %.3f) max=(%.3f, %.3f, %.3f) height(Y)=%.3f'
                     % (lo[0], lo[1], lo[2], hi_[0], hi_[1], hi_[2], hi_[1] - lo[1]))
    return '\n'.join(lines)
