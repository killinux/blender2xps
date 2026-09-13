# -*- coding: utf-8 -*-
"""Blender2XPS - export Blender models (mesh + armature + weights) to
XNALara / XPS (.xps / .mesh / .mesh.ascii).

The exporter is self contained: it does not use XNALaraMesh or any other
add-on.  It works in world space, normalises bone weights, maps rig bone names
to the XPS standard names, understands mmd_tools models and re-reads the
written file to verify it.
"""

bl_info = {
    "name": "Blender2XPS (XPS / XNALara Exporter)",
    "author": "blender2xps",
    "version": (1, 0, 0),
    "blender": (3, 6, 0),
    "location": "File > Export > XPS / XNALara,  3D View > Sidebar > XPS",
    "description": "Export meshes with armature and weights to XPS/XNALara (.xps/.mesh/.mesh.ascii)",
    "category": "Import-Export",
    "doc_url": "https://github.com/",
}

import importlib
import os

if "bpy" in locals():
    for _m in ("xps_format", "bone_names", "materials", "export_xps"):
        if _m in locals():
            importlib.reload(locals()[_m])

import bpy
from bpy.props import (BoolProperty, EnumProperty, FloatProperty, IntProperty,
                       PointerProperty, StringProperty)
from bpy_extras.io_utils import ExportHelper

from . import xps_format
from . import bone_names
from . import materials
from . import export_xps


# --------------------------------------------------------------------------- shared properties

def _export_props():
    return {
        'fmt': EnumProperty(
            name='文件格式', default='AUTO',
            items=[
                ('AUTO', '自动 (推荐)', '路径以 .ascii 结尾则写文本格式；否则写 XPS 二进制：权重上限≤4 用 v2.15（所有 XPS 11.x 都能读），否则 v3.15'),
                ('XPS2', 'XPS 二进制 v2.15 (4 权重)', '带文件头，每顶点固定 4 个权重。XPS 11.x 全部兼容'),
                ('XPS3', 'XPS 二进制 v3.15 (不限权重)', '带文件头，每顶点权重数量可变。需要 XPS 11.8.9 或更新'),
                ('MESH', '经典 .mesh (无文件头)', '老 XNALara 格式：4 权重 + 切线。兼容性最广但没有文件头信息'),
                ('ASCII', 'ASCII 文本 (.mesh.ascii)', '可读的文本格式，便于检查'),
            ]),
        'scope': EnumProperty(
            name='导出范围', default='ARMATURE',
            items=[
                ('ARMATURE', '骨架绑定的全部网格', '导出绑定到目标骨架（活动物体/选中的骨架）的所有网格'),
                ('SELECTED', '仅选中的物体', '只导出选中的网格；只选中骨架时导出绑定到它的全部网格'),
                ('VISIBLE', '全部可见网格', '导出视图层里所有可见的网格'),
            ]),
        'visible_only': BoolProperty(name='跳过隐藏物体', default=True, description='隐藏的网格不导出'),
        'apply_modifiers': BoolProperty(
            name='应用修改器', default=True,
            description='导出计算后的网格（镜像/细分/形态键等）。骨架修改器始终按静止姿态处理，不会被烘焙'),
        'bake_pose': BoolProperty(
            name='把当前姿态烘焙为绑定姿态', default=False,
            description='用当前姿态下的网格和骨骼位置作为 XPS 的默认姿态（例如把 A-pose 改成 T-pose 后导出）'),
        'scale': FloatProperty(name='缩放', default=1.0, min=0.0001, max=10000.0, description='导出时乘以的比例。XPS 角色通常 1.5~1.9 单位高'),
        'max_weights': IntProperty(
            name='每顶点最多权重数', default=4, min=0, max=32,
            description='超过的权重按大小截断并重新归一化；0 = 不限制（只对 v3 / ASCII 有效）'),
        'unweighted': EnumProperty(
            name='无权重顶点', default='ROOT',
            items=[
                ('ROOT', '绑定到根骨', '没有任何骨骼权重的顶点跟随根骨移动'),
                ('NEAREST', '绑定到最近的骨骼', '按距离找最近的骨骼'),
                ('KEEP', '保持 0 权重 (不推荐)', 'XPS 里这些顶点会塌到原点'),
            ]),
        'bone_naming': EnumProperty(
            name='骨骼命名', default='XPS',
            items=[
                ('XPS', '映射为 XPS 标准骨名', '识别 MMD / Bip001 / Mixamo / VRoid / Rigify 等常见骨名，改成 root hips、arm left elbow 等 XPS 标准名，其余保持原名'),
                ('KEEP', '保持原名', '骨骼名原样写入'),
            ]),
        'ascii_bone_names': BoolProperty(name='其余骨名转成 ASCII', default=False, description='未映射的日文骨名按 mmd_tools 词典翻译成英文'),
        'mmd_merge_helpers': BoolProperty(
            name='合并 MMD 辅助骨', default=True,
            description='足D/ひざD/足首D 并入 足/ひざ/足首，腕捩/手捩 并入 腕/ひじ，肩C/肩P 并入 肩，权重和子骨一起转移。这样在 XPS 里转动主骨骼时网格才会跟着动'),
        'drop_internal_bones': BoolProperty(name='去掉 mmd_tools 内部骨', default=True, description='去掉 _dummy_ / _shadow_ 开头且没有权重的约束辅助骨'),
        'sort_bones': BoolProperty(name='父骨排在子骨前面', default=True),
        'ascii_mesh_names': BoolProperty(name='网格名转成 ASCII', default=True, description='部件名里的非 ASCII 字符按词典翻译或去掉'),
        'copy_textures': BoolProperty(name='复制贴图到输出目录', default=True, description='把用到的贴图复制/转成 PNG 放到模型旁边（XPS 按同目录找贴图）'),
        'alpha_mode': EnumProperty(
            name='透明材质', default='AUTO',
            items=[
                ('AUTO', '自动检测', '材质混合模式不是不透明、且漫反射贴图确实含有透明像素时使用带 alpha 的渲染组'),
                ('NEVER', '全部不透明', '所有部件用不透明渲染组'),
                ('ALWAYS', '全部透明', '所有部件用带 alpha 的渲染组'),
            ]),
        'unlit': BoolProperty(name='无光照渲染组 (卡通)', default=False, description='使用渲染组 10/21（不受光照影响），适合 MMD 卡通贴图'),
        'specular': FloatProperty(name='高光值', default=0.1, min=0.0, max=10.0, description='写进部件名的高光强度，XPS 里可再调'),
        'vertex_colors': BoolProperty(name='导出顶点色', default=True),
        'write_report': BoolProperty(name='写导出报告 (.report.txt)', default=True),
        'all_uv_layers': BoolProperty(name='导出全部 UV 层', default=False, description='默认只导出第一层 UV（XPS 只用第一层，光照贴图用第二层）'),
        'hide_helper_bones': BoolProperty(
            name='隐藏辅助骨 (unused 前缀)', default=True,
            description='扭转骨、肌肉修正骨、IK/附件骨等加上 unused_ 前缀，XPS 的骨骼列表里不显示但仍参与蒙皮'),
        'hide_facial_bones': BoolProperty(
            name='隐藏面部骨', default=False,
            description='MetaHuman 风格的 FACIAL_* 骨（下颌、眼球、舌头除外）加上 unused_ 前缀'),
        'skip_translucent': BoolProperty(
            name='跳过半透明辅助壳', default=True,
            description='固定 alpha < 0.5 的材质（眼部遮蔽壳、泪线、假反射片）不导出'),
        'bake_mode': EnumProperty(
            name='材质烘焙', default='AUTO',
            items=[
                ('AUTO', '自动 (只烘复杂材质)', '颜色或透明由节点计算（渐变、通道拆分、程序化虹膜等）的材质用 Cycles 烘焙成 RGBA 贴图'),
                ('ALL', '全部烘焙', '每个材质都烘焙'),
                ('OFF', '不烘焙', '只使用节点里找到的贴图'),
            ]),
        'bake_size': IntProperty(name='烘焙尺寸', default=0, min=0, max=8192, description='0 = 按材质里贴图的尺寸（最大 2048）'),
        'strip_common_prefix': BoolProperty(name='去掉部件名的公共前缀', default=True, description='Fiona_Face / Fiona_Hair -> Face / Hair'),
        'auto_facing': BoolProperty(
            name='自动转正朝向', default=True,
            description='按脚踝->脚趾方向判断角色朝向，整体绕 Z 轴旋转使其面向 XPS 观众（Blender -Y）。没有腿骨时不做处理'),
    }


def _apply_props(cls):
    ann = cls.__dict__.get('__annotations__', None)
    if ann is None:
        ann = {}
        setattr(cls, '__annotations__', ann)
    for k, v in _export_props().items():
        ann[k] = v
    return cls


@_apply_props
class Blender2XpsSettings(bpy.types.PropertyGroup):
    armature_name: StringProperty(name='骨架', description='留空则自动选择（活动物体 / 选中的骨架 / 网格绑定的骨架）')
    export_path: StringProperty(name='输出文件', subtype='FILE_PATH', default='//xps_export/model.xps')


def _copy_settings(src, dst):
    for k in _export_props():
        try:
            setattr(dst, k, getattr(src, k))
        except Exception:
            pass


def _draw_settings(layout, props, compact=False):
    box = layout.box()
    box.label(text='范围与几何', icon='MESH_DATA')
    box.prop(props, 'scope')
    box.prop(props, 'visible_only')
    box.prop(props, 'apply_modifiers')
    box.prop(props, 'bake_pose')
    box.prop(props, 'scale')
    box.prop(props, 'vertex_colors')
    box.prop(props, 'all_uv_layers')
    box.prop(props, 'auto_facing')

    box = layout.box()
    box.label(text='骨骼与权重', icon='ARMATURE_DATA')
    box.prop(props, 'max_weights')
    box.prop(props, 'unweighted')
    box.prop(props, 'bone_naming')
    box.prop(props, 'ascii_bone_names')
    box.prop(props, 'mmd_merge_helpers')
    box.prop(props, 'drop_internal_bones')
    box.prop(props, 'sort_bones')
    box.prop(props, 'hide_helper_bones')
    box.prop(props, 'hide_facial_bones')

    box = layout.box()
    box.label(text='材质与文件', icon='MATERIAL')
    box.prop(props, 'fmt')
    box.prop(props, 'alpha_mode')
    box.prop(props, 'bake_mode')
    box.prop(props, 'bake_size')
    box.prop(props, 'skip_translucent')
    box.prop(props, 'unlit')
    box.prop(props, 'specular')
    box.prop(props, 'copy_textures')
    box.prop(props, 'ascii_mesh_names')
    box.prop(props, 'strip_common_prefix')
    box.prop(props, 'write_report')


# --------------------------------------------------------------------------- operators

@_apply_props
class BLENDER2XPS_OT_export(bpy.types.Operator, ExportHelper):
    """导出为 XNALara / XPS 模型（骨骼 + 权重 + 贴图）"""
    bl_idname = 'blender2xps.export'
    bl_label = '导出 XPS 模型'
    bl_options = {'REGISTER'}

    filename_ext = '.xps'
    filter_glob: StringProperty(default='*.xps;*.mesh;*.ascii', options={'HIDDEN'})
    armature_name: StringProperty(default='', options={'HIDDEN'})
    use_scene_settings: BoolProperty(default=False, options={'HIDDEN', 'SKIP_SAVE'})

    def invoke(self, context, event):
        if self.use_scene_settings:
            sp = context.scene.blender2xps
            _copy_settings(sp, self)
            self.armature_name = sp.armature_name
            if sp.export_path:
                self.filepath = bpy.path.abspath(sp.export_path)
        if not self.filepath:
            base = os.path.splitext(os.path.basename(bpy.data.filepath))[0] or 'model'
            self.filepath = base + self.filename_ext
        return ExportHelper.invoke(self, context, event)

    def check(self, context):
        fmt = export_xps.resolve_format(export_xps.Settings(fmt=self.fmt, filepath=self.filepath, max_weights=self.max_weights))
        new = xps_format.fix_extension(self.filepath, fmt)
        if new != self.filepath:
            self.filepath = new
            return True
        return False

    def draw(self, context):
        _draw_settings(self.layout, self)

    def execute(self, context):
        settings = export_xps.Settings.from_props(self)
        settings.filepath = self.filepath
        settings.armature_name = self.armature_name
        try:
            result = export_xps.export_model(context, settings)
        except export_xps.ExportError as exc:
            self.report({'ERROR'}, str(exc))
            return {'CANCELLED'}
        except Exception as exc:
            import traceback
            traceback.print_exc()
            self.report({'ERROR'}, '导出失败: %s' % exc)
            return {'CANCELLED'}
        context.scene.blender2xps.export_path = result.path
        if result.errors:
            self.report({'ERROR'}, '导出完成但校验有错误: %s' % result.errors[0])
        elif result.warnings:
            self.report({'WARNING'}, '已导出 %s  (%d 条警告，见报告)' % (result.summary(), len(result.warnings)))
        else:
            self.report({'INFO'}, '已导出 ' + result.summary())
        return {'FINISHED'}


class BLENDER2XPS_OT_diagnose(bpy.types.Operator):
    """检查骨架、权重、材质，列出导出时会怎样处理（不写文件）"""
    bl_idname = 'blender2xps.diagnose'
    bl_label = '检查模型'

    def execute(self, context):
        sp = context.scene.blender2xps
        settings = export_xps.Settings.from_props(sp)
        settings.armature_name = sp.armature_name
        try:
            lines = export_xps.analyze(context, settings)
        except Exception as exc:
            import traceback
            traceback.print_exc()
            self.report({'ERROR'}, '检查失败: %s' % exc)
            return {'CANCELLED'}
        text = '\n'.join(lines)
        print('=== Blender2XPS 检查 ===\n' + text)
        tb = bpy.data.texts.get('blender2xps_report')
        if tb is None:
            tb = bpy.data.texts.new('blender2xps_report')
        tb.clear()
        tb.write(text + '\n')

        def draw(menu, _context):
            col = menu.layout.column()
            for line in lines[:45]:
                col.label(text=line[:140])
            if len(lines) > 45:
                col.label(text='... 完整内容见文本编辑器里的 blender2xps_report')

        context.window_manager.popup_menu(draw, title='Blender2XPS 检查结果', icon='INFO')
        self.report({'INFO'}, '检查完成，详情见文本块 blender2xps_report 与系统控制台')
        return {'FINISHED'}


class BLENDER2XPS_OT_open_folder(bpy.types.Operator):
    """打开输出目录"""
    bl_idname = 'blender2xps.open_folder'
    bl_label = '打开输出目录'

    def execute(self, context):
        path = bpy.path.abspath(context.scene.blender2xps.export_path)
        folder = os.path.dirname(path)
        if not folder or not os.path.isdir(folder):
            self.report({'WARNING'}, '目录不存在: %s' % folder)
            return {'CANCELLED'}
        bpy.ops.wm.path_open(filepath=folder)
        return {'FINISHED'}


# --------------------------------------------------------------------------- UI

class BLENDER2XPS_PT_panel(bpy.types.Panel):
    bl_label = 'Blender2XPS 导出'
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = 'XPS'

    def draw(self, context):
        layout = self.layout
        sp = context.scene.blender2xps
        active = context.view_layer.objects.active
        arm = None
        if sp.armature_name:
            arm = bpy.data.objects.get(sp.armature_name)
        elif active is not None:
            arm = active if active.type == 'ARMATURE' else export_xps.find_armature_for(active)
        col = layout.column(align=True)
        col.prop_search(sp, 'armature_name', bpy.data, 'objects', text='骨架')
        col.label(text='目标骨架: %s' % (arm.name if arm else '自动/无'), icon='ARMATURE_DATA')
        col.prop(sp, 'export_path')

        row = layout.row(align=True)
        row.scale_y = 1.4
        row.operator('blender2xps.diagnose', icon='VIEWZOOM')
        op = row.operator('blender2xps.export', icon='EXPORT', text='导出...')
        op.use_scene_settings = True
        layout.operator('blender2xps.open_folder', icon='FILE_FOLDER')

        _draw_settings(layout, sp)


def menu_func_export(self, context):
    self.layout.operator(BLENDER2XPS_OT_export.bl_idname, text='XPS / XNALara (.xps/.mesh/.ascii) [Blender2XPS]')


classes = (
    Blender2XpsSettings,
    BLENDER2XPS_OT_export,
    BLENDER2XPS_OT_diagnose,
    BLENDER2XPS_OT_open_folder,
    BLENDER2XPS_PT_panel,
)


def register():
    for cls in classes:
        bpy.utils.register_class(cls)
    bpy.types.Scene.blender2xps = PointerProperty(type=Blender2XpsSettings)
    bpy.types.TOPBAR_MT_file_export.append(menu_func_export)


def unregister():
    bpy.types.TOPBAR_MT_file_export.remove(menu_func_export)
    del bpy.types.Scene.blender2xps
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)


if __name__ == '__main__':
    register()
