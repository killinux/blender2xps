# -*- coding: utf-8 -*-
"""Bone naming helpers: rig detection, XPS standard names, MMD helper-bone
merge rules and a Japanese -> ASCII fallback.  No Blender dependency."""

import re

# --------------------------------------------------------------------------- XPS standard names
# The names XPS itself uses for its "Generic_Item" characters.  Standard poses
# distributed by the community reference these names, so mapping a rig onto
# them makes the exported model pose-compatible.

XPS_NAMES = {
    'root': 'root ground',
    'hips': 'root hips',
    'spine_lower': 'spine lower',
    'spine_middle': 'spine middle',
    'spine_upper': 'spine upper',
    'neck': 'head neck lower',
    'head': 'head neck upper',
    'eye_l': 'head eyeball left',
    'eye_r': 'head eyeball right',
    'shoulder_l': 'arm left shoulder 1',
    'shoulder_r': 'arm right shoulder 1',
    'upper_arm_l': 'arm left shoulder 2',
    'upper_arm_r': 'arm right shoulder 2',
    'elbow_l': 'arm left elbow',
    'elbow_r': 'arm right elbow',
    'wrist_l': 'arm left wrist',
    'wrist_r': 'arm right wrist',
    'thigh_l': 'leg left thigh',
    'thigh_r': 'leg right thigh',
    'knee_l': 'leg left knee',
    'knee_r': 'leg right knee',
    'ankle_l': 'leg left ankle',
    'ankle_r': 'leg right ankle',
    'toes_l': 'leg left toes',
    'toes_r': 'leg right toes',
    'jaw': 'head jaw',
}
_FINGERS = (('thumb', 1), ('index', 2), ('middle', 3), ('ring', 4), ('pinky', 5))
for _finger, _num in _FINGERS:
    for _seg, _letter in ((1, 'a'), (2, 'b'), (3, 'c')):
        for _side, _word in (('l', 'left'), ('r', 'right')):
            XPS_NAMES['%s_%d_%s' % (_finger, _seg, _side)] = 'arm %s finger %d%s' % (_word, _num, _letter)


# --------------------------------------------------------------------------- alias tables
# canonical key -> list of names used by popular rigs.  Matching is
# case-insensitive.  ``{L}``/``{R}`` placeholders are expanded with the side
# spellings listed in _SIDE_STYLES so one entry covers "腕.L", "左腕", "arm_L" ...

_SIDE_STYLES = (
    # (left spelling, right spelling, position)  position: 'prefix' or 'suffix'
    ('左', '右', 'prefix'),         # MMD Japanese
    ('.L', '.R', 'suffix'),         # Blender / mmd_tools
    ('_L', '_R', 'suffix'),         # mmd_tools english, misc
    (' L', ' R', 'suffix'),
    ('L_', 'R_', 'prefix'),
    ('Left', 'Right', 'prefix'),    # Mixamo style (LeftArm)
    ('left', 'right', 'prefix'),
    ('L ', 'R ', 'prefix'),         # "Bip001 L Thigh" handled separately below
)

_ALIASES = {
    'root': ['全ての親', 'ParentNode', 'root', 'Root', 'Armature', 'master', 'Hips_root', 'Bip001 Root', 'mixamorig:Hips_root', 'J_Root', 'Root_G'],
    'hips': ['センター', 'center', 'Center', 'hips', 'Hips', 'pelvis', 'Pelvis', 'Bip001 Pelvis', 'Bip001', 'mixamorig:Hips', 'J_Bip_C_Hips', 'root hips'],
    'spine_lower': ['上半身', 'UpperBody', 'upper body', 'spine', 'Spine', 'Bip001 Spine', 'mixamorig:Spine', 'J_Bip_C_Spine', 'spine lower', 'spine.001', 'chest', 'spine_01'],
    'spine_middle': ['上半身1', 'UpperBody1', 'upper body 1', 'spine1', 'Spine1', 'Bip001 Spine1', 'mixamorig:Spine1', 'spine middle', 'spine.002'],
    'spine_upper': ['上半身2', 'UpperBody2', 'upper body 2', 'spine2', 'Spine2', 'Bip001 Spine2', 'mixamorig:Spine2', 'J_Bip_C_Chest', 'upper_chest', 'spine upper', 'spine.003', 'J_Bip_C_UpperChest'],
    'neck': ['首', 'Neck', 'neck', 'Bip001 Neck', 'mixamorig:Neck', 'J_Bip_C_Neck', 'head neck lower', 'neck_01'],
    'head': ['頭', 'Head', 'head', 'Bip001 Head', 'mixamorig:Head', 'J_Bip_C_Head', 'head neck upper'],
    'eye_l': ['目{L}', 'eye{L}', 'Eye{L}', 'Bip001 L Eye', 'mixamorig:LeftEye', 'J_Adj_L_FaceEye', 'head eyeball left', 'eye.L', 'LeftEye', 'FACIAL_L_Eye'],
    'eye_r': ['目{R}', 'eye{R}', 'Eye{R}', 'Bip001 R Eye', 'mixamorig:RightEye', 'J_Adj_R_FaceEye', 'head eyeball right', 'eye.R', 'RightEye', 'FACIAL_R_Eye'],
    'shoulder_l': ['肩{L}', 'shoulder{L}', 'Shoulder{L}', 'Bip001 L Clavicle', 'mixamorig:LeftShoulder', 'J_Bip_L_Shoulder', 'arm left shoulder 1', 'clavicle{L}', 'LeftCollar', 'LeftShoulder'],
    'shoulder_r': ['肩{R}', 'shoulder{R}', 'Shoulder{R}', 'Bip001 R Clavicle', 'mixamorig:RightShoulder', 'J_Bip_R_Shoulder', 'arm right shoulder 1', 'clavicle{R}', 'RightCollar', 'RightShoulder'],
    'upper_arm_l': ['腕{L}', 'arm{L}', 'Arm{L}', 'upper_arm{L}', 'UpperArm{L}', 'Bip001 L UpperArm', 'mixamorig:LeftArm', 'J_Bip_L_UpperArm', 'arm left shoulder 2', 'LeftArm', 'LeftUpperArm'],
    'upper_arm_r': ['腕{R}', 'arm{R}', 'Arm{R}', 'upper_arm{R}', 'UpperArm{R}', 'Bip001 R UpperArm', 'mixamorig:RightArm', 'J_Bip_R_UpperArm', 'arm right shoulder 2', 'RightArm', 'RightUpperArm'],
    'elbow_l': ['ひじ{L}', '肘{L}', 'elbow{L}', 'Elbow{L}', 'forearm{L}', 'ForeArm{L}', 'lower_arm{L}', 'LowerArm{L}', 'Bip001 L Forearm', 'mixamorig:LeftForeArm', 'J_Bip_L_LowerArm', 'arm left elbow', 'LeftElbow', 'LeftForeArm', 'LeftLowerArm'],
    'elbow_r': ['ひじ{R}', '肘{R}', 'elbow{R}', 'Elbow{R}', 'forearm{R}', 'ForeArm{R}', 'lower_arm{R}', 'LowerArm{R}', 'Bip001 R Forearm', 'mixamorig:RightForeArm', 'J_Bip_R_LowerArm', 'arm right elbow', 'RightElbow', 'RightForeArm', 'RightLowerArm'],
    'wrist_l': ['手首{L}', 'wrist{L}', 'Wrist{L}', 'hand{L}', 'Hand{L}', 'Bip001 L Hand', 'mixamorig:LeftHand', 'J_Bip_L_Hand', 'arm left wrist', 'LeftWrist', 'LeftHand'],
    'wrist_r': ['手首{R}', 'wrist{R}', 'Wrist{R}', 'hand{R}', 'Hand{R}', 'Bip001 R Hand', 'mixamorig:RightHand', 'J_Bip_R_Hand', 'arm right wrist', 'RightWrist', 'RightHand'],
    'thigh_l': ['足{L}', 'leg{L}', 'Leg{L}', 'thigh{L}', 'Thigh{L}', 'upper_leg{L}', 'UpperLeg{L}', 'Bip001 L Thigh', 'mixamorig:LeftUpLeg', 'J_Bip_L_UpperLeg', 'leg left thigh', 'LeftHip', 'LeftUpLeg', 'LeftUpperLeg'],
    'thigh_r': ['足{R}', 'leg{R}', 'Leg{R}', 'thigh{R}', 'Thigh{R}', 'upper_leg{R}', 'UpperLeg{R}', 'Bip001 R Thigh', 'mixamorig:RightUpLeg', 'J_Bip_R_UpperLeg', 'leg right thigh', 'RightHip', 'RightUpLeg', 'RightUpperLeg'],
    'knee_l': ['ひざ{L}', '膝{L}', 'knee{L}', 'Knee{L}', 'shin{L}', 'Shin{L}', 'calf{L}', 'lower_leg{L}', 'LowerLeg{L}', 'Bip001 L Calf', 'mixamorig:LeftLeg', 'J_Bip_L_LowerLeg', 'leg left knee', 'LeftKnee', 'LeftLeg', 'LeftLowerLeg'],
    'knee_r': ['ひざ{R}', '膝{R}', 'knee{R}', 'Knee{R}', 'shin{R}', 'Shin{R}', 'calf{R}', 'lower_leg{R}', 'LowerLeg{R}', 'Bip001 R Calf', 'mixamorig:RightLeg', 'J_Bip_R_LowerLeg', 'leg right knee', 'RightKnee', 'RightLeg', 'RightLowerLeg'],
    'ankle_l': ['足首{L}', 'ankle{L}', 'Ankle{L}', 'foot{L}', 'Foot{L}', 'Bip001 L Foot', 'mixamorig:LeftFoot', 'J_Bip_L_Foot', 'leg left ankle', 'LeftAnkle', 'LeftFoot'],
    'ankle_r': ['足首{R}', 'ankle{R}', 'Ankle{R}', 'foot{R}', 'Foot{R}', 'Bip001 R Foot', 'mixamorig:RightFoot', 'J_Bip_R_Foot', 'leg right ankle', 'RightAnkle', 'RightFoot'],
    'toes_l': ['足先EX{L}', 'つま先{L}', 'toe{L}', 'Toe{L}', 'toes{L}', 'Toes{L}', 'ToeTipEX{L}', 'Bip001 L Toe0', 'mixamorig:LeftToeBase', 'J_Bip_L_ToeBase', 'leg left toes', 'LeftToeBase', 'LeftToes', 'ball{L}'],
    'toes_r': ['足先EX{R}', 'つま先{R}', 'toe{R}', 'Toe{R}', 'toes{R}', 'Toes{R}', 'ToeTipEX{R}', 'Bip001 R Toe0', 'mixamorig:RightToeBase', 'J_Bip_R_ToeBase', 'leg right toes', 'RightToeBase', 'RightToes', 'ball{R}'],
    'jaw': ['FACIAL_C_Jaw', 'jaw', 'Jaw', 'あご', '顎', 'Bip001 Jaw', 'mixamorig:Jaw', 'J_Adj_C_Jaw', 'head jaw'],
}

# fingers: (canonical finger, MMD kanji, mmd_tools english, bip001 index, mixamo, vroid)
_FINGER_DEFS = (
    ('thumb', '親指', 'thumb', 0, 'Thumb', 'Thumb'),
    ('index', '人指', 'fore', 1, 'Index', 'Index'),
    ('middle', '中指', 'middle', 2, 'Middle', 'Middle'),
    ('ring', '薬指', 'third', 3, 'Ring', 'Ring'),
    ('pinky', '小指', 'little', 4, 'Pinky', 'Little'),
)
_FULLWIDTH_DIGITS = {0: '０', 1: '１', 2: '２', 3: '３'}
for _finger, _kanji, _en, _bip, _mixamo, _vroid in _FINGER_DEFS:
    for _seg in (1, 2, 3):
        for _side, _S, _side_word, _bip_side in (('l', 'L', 'left', 'L'), ('r', 'R', 'right', 'R')):
            key = '%s_%d_%s' % (_finger, _seg, _side)
            names = []
            # MMD: thumb uses 0/1/2, other fingers 1/2/3 (full width digits, half width as well)
            mmd_num = _seg - 1 if _finger == 'thumb' else _seg
            names.append('%s%s{%s}' % (_kanji, _FULLWIDTH_DIGITS[mmd_num], _S))
            names.append('%s%d{%s}' % (_kanji, mmd_num, _S))
            names.append('%s%d{%s}' % (_en, mmd_num, _S))
            names.append('%s%d{%s}' % (_en.capitalize(), mmd_num, _S))
            # Bip001 L Finger0 / Finger01 / Finger02
            bip_suffix = '%d' % _bip if _seg == 1 else '%d%d' % (_bip, _seg - 1)
            names.append('Bip001 %s Finger%s' % (_bip_side, bip_suffix))
            names.append('%sFinger%s' % (_side_word.capitalize(), bip_suffix))
            names.append('mixamorig:%sHand%s%d' % (_side_word.capitalize(), _mixamo, _seg))
            names.append('%sHand%s%d' % (_side_word.capitalize(), _mixamo, _seg))
            names.append('J_Bip_%s_%s%d' % (_bip_side, _vroid, _seg))
            names.append('arm %s finger %d%s' % (_side_word, dict(_FINGERS)[_finger], 'abc'[_seg - 1]))
            # rigify / generic: thumb.01.L f_index.01.L
            rig = {'thumb': 'thumb', 'index': 'f_index', 'middle': 'f_middle', 'ring': 'f_ring', 'pinky': 'f_pinky'}[_finger]
            names.append('%s.%02d{%s}' % (rig, _seg, _S))
            names.append('%s_%02d{%s}' % (_finger, _seg, _S))          # UE4/UE5 mannequin: thumb_01_l
            _ALIASES[key] = names


def _expand_side(name, side):
    """Expand {L}/{R} placeholders into all spellings."""
    if '{L}' not in name and '{R}' not in name:
        return [name]
    out = []
    for left, right, pos in _SIDE_STYLES:
        token = left if side == 'l' else right
        base = name.replace('{L}', '').replace('{R}', '')
        if pos == 'prefix':
            out.append(token + base)
        else:
            out.append(base + token)
    return out


def _build_alias_index():
    index = {}
    for key, names in _ALIASES.items():
        side = key[-1] if key.endswith('_l') or key.endswith('_r') else None
        for name in names:
            for expanded in _expand_side(name, side):
                # 3ds Max Biped rigs from UE games spell it Bip001-L-Clavicle
                # (Stellar Blade); index the hyphenated form as well
                for variant in (expanded, expanded.replace(' ', '_'), expanded.replace(' ', '-')):
                    low = variant.strip().lower()
                    if low and low not in index:
                        index[low] = key
    return index


_ALIAS_INDEX = _build_alias_index()


def canonical_key(bone_name):
    """Return the canonical key of a bone name or None."""
    return _ALIAS_INDEX.get(bone_name.strip().lower())


def xps_name_for(bone_name):
    key = canonical_key(bone_name)
    return XPS_NAMES.get(key) if key else None


_UE_SPINE_RE = re.compile(r'^spine_(\d{2})$', re.I)


def _ue_spine_overrides(names):
    """UE rigs number their spine bones (spine_01..spine_05); pick the lower /
    middle / upper ones depending on how many there are."""
    chain = sorted((int(m.group(1)), n) for n in names for m in [_UE_SPINE_RE.match(n)] if m)
    if not chain:
        return {}
    ordered = [n for _i, n in chain]
    if len(ordered) == 1:
        picks = {'spine_lower': ordered[0]}
    elif len(ordered) == 2:
        picks = {'spine_lower': ordered[0], 'spine_upper': ordered[1]}
    else:
        picks = {'spine_lower': ordered[0], 'spine_middle': ordered[len(ordered) // 2], 'spine_upper': ordered[-1]}
    return {n: key for key, n in picks.items()}


def map_names_to_xps(names):
    """Map a list of bone names to XPS standard names.

    Returns dict original -> new for every bone that changes.  The first bone
    claiming a canonical key wins; later ones keep their name so the result
    never introduces duplicates by itself."""
    mapping = {}
    used = set()
    overrides = _ue_spine_overrides(names)
    for name in names:
        if _UE_SPINE_RE.match(name):
            key = overrides.get(name)
        else:
            key = canonical_key(name)
        if key and key not in used:
            new = XPS_NAMES[key]
            if new != name:
                mapping[name] = new
            used.add(key)
    return mapping


# --------------------------------------------------------------------------- side handling

def split_side(name):
    """Split a name into (base, side, style) where side is 'l'/'r'/None.

    style is a callable that re-attaches a side to a base name in the same
    spelling, so merge rules can be written once for both sides."""
    for left, right, pos in _SIDE_STYLES:
        for token, side in ((left, 'l'), (right, 'r')):
            if pos == 'prefix' and name.startswith(token) and len(name) > len(token):
                base = name[len(token):]
                return base, side, (lambda b, t=token: t + b)
            if pos == 'suffix' and name.endswith(token) and len(name) > len(token):
                base = name[:-len(token)]
                return base, side, (lambda b, t=token: b + t)
    return name, None, (lambda b: b)


# --------------------------------------------------------------------------- MMD helper bones
# (regex on the side-less base name) -> replacement base name.  Bones matching
# a rule are removed from the export, their vertex weights are added to the
# target bone and their children are re-parented to it.

MMD_MERGE_RULES = [
    (re.compile(r'^足D$'), '足'),
    (re.compile(r'^ひざD$'), 'ひざ'),
    (re.compile(r'^足首D$'), '足首'),
    (re.compile(r'^腕捩[0-9０-９]*$'), '腕'),
    (re.compile(r'^手捩[0-9０-９]*$'), 'ひじ'),
    (re.compile(r'^肩[CP]$'), '肩'),
    (re.compile(r'^腰キャンセル$'), '足'),
    (re.compile(r'^LegD$', re.I), 'Leg'),
    (re.compile(r'^KneeD$', re.I), 'Knee'),
    (re.compile(r'^AnkleD$', re.I), 'Ankle'),
    (re.compile(r'^ArmTwist[0-9]*$', re.I), 'Arm'),
    (re.compile(r'^WristTwist[0-9]*$', re.I), 'Elbow'),
    (re.compile(r'^Shoulder[CP]$', re.I), 'Shoulder'),
    (re.compile(r'^WaistCancel$', re.I), 'Leg'),
]

# bones created by mmd_tools purely for constraints; never weighted
MMD_INTERNAL_RE = re.compile(r'^(_dummy_|_shadow_)')


def mmd_merge_target(name, existing_names):
    """Return the bone *name* should be merged into, or None."""
    base, side, attach = split_side(name)
    for rx, target_base in MMD_MERGE_RULES:
        if rx.match(base):
            target = attach(target_base)
            if target in existing_names and target != name:
                return target
            # try the other side spellings, e.g. '左足D' -> '足.L'
            if side:
                for left, right, pos in _SIDE_STYLES:
                    token = left if side == 'l' else right
                    cand = token + target_base if pos == 'prefix' else target_base + token
                    if cand in existing_names and cand != name:
                        return cand
            return None
    return None


# --------------------------------------------------------------------------- ASCII fallback

_JP_TO_EN = [
    ('全ての親', 'ParentNode'), ('操作中心', 'ControlNode'), ('センター', 'Center'), ('ｾﾝﾀｰ', 'Center'),
    ('グループ', 'Group'), ('グルーブ', 'Groove'), ('キャンセル', 'Cancel'), ('上半身', 'UpperBody'),
    ('下半身', 'LowerBody'), ('手首', 'Wrist'), ('足首', 'Ankle'), ('首', 'Neck'), ('頭', 'Head'), ('顔', 'Face'),
    ('下顎', 'Chin'), ('下あご', 'Chin'), ('あご', 'Jaw'), ('顎', 'Jaw'), ('両目', 'Eyes'), ('目', 'Eye'),
    ('眉', 'Eyebrow'), ('舌', 'Tongue'), ('涙', 'Tears'), ('泣き', 'Cry'), ('歯', 'Teeth'), ('照れ', 'Blush'),
    ('青ざめ', 'Pale'), ('ガーン', 'Gloom'), ('汗', 'Sweat'), ('怒', 'Anger'), ('感情', 'Emotion'), ('符', 'Marks'),
    ('暗い', 'Dark'), ('腰', 'Waist'), ('髪', 'Hair'), ('三つ編み', 'Braid'), ('胸', 'Breast'), ('乳', 'Boob'),
    ('おっぱい', 'Tits'), ('筋', 'Muscle'), ('腹', 'Belly'), ('鎖骨', 'Clavicle'), ('肩', 'Shoulder'), ('腕', 'Arm'),
    ('うで', 'Arm'), ('ひじ', 'Elbow'), ('肘', 'Elbow'), ('手', 'Hand'), ('親指', 'Thumb'), ('人指', 'IndexFinger'),
    ('人差指', 'IndexFinger'), ('中指', 'MiddleFinger'), ('薬指', 'RingFinger'), ('小指', 'LittleFinger'),
    ('足', 'Leg'), ('ひざ', 'Knee'), ('膝', 'Knee'), ('つま', 'Toe'), ('袖', 'Sleeve'), ('新規', 'New'), ('ボーン', 'Bone'),
    ('捩', 'Twist'), ('回転', 'Rotation'), ('軸', 'Axis'), ('ﾈｸﾀｲ', 'Necktie'), ('ネクタイ', 'Necktie'),
    ('ヘッドセット', 'Headset'), ('飾り', 'Accessory'), ('リボン', 'Ribbon'), ('襟', 'Collar'), ('紐', 'String'),
    ('コード', 'Cord'), ('イヤリング', 'Earring'), ('メガネ', 'Eyeglasses'), ('眼鏡', 'Glasses'), ('帽子', 'Hat'),
    ('ｽｶｰﾄ', 'Skirt'), ('スカート', 'Skirt'), ('パンツ', 'Pantsu'), ('シャツ', 'Shirt'), ('フリル', 'Frill'),
    ('マフラー', 'Muffler'), ('ﾏﾌﾗｰ', 'Muffler'), ('服', 'Clothes'), ('ブーツ', 'Boots'), ('ねこみみ', 'CatEars'),
    ('ジップ', 'Zip'), ('ｼﾞｯﾌﾟ', 'Zip'), ('ダミー', 'Dummy'), ('ﾀﾞﾐｰ', 'Dummy'), ('基', 'Category'), ('あほ毛', 'Antenna'),
    ('アホ毛', 'Antenna'), ('モミアゲ', 'Sideburn'), ('もみあげ', 'Sideburn'), ('ツインテ', 'Twintail'), ('おさげ', 'Pigtail'),
    ('ひらひら', 'Flutter'), ('調整', 'Adjustment'), ('補助', 'Aux'), ('右', 'Right'), ('左', 'Left'), ('前', 'Front'),
    ('後ろ', 'Behind'), ('後', 'Back'), ('横', 'Side'), ('中', 'Middle'), ('上', 'Upper'), ('下', 'Lower'), ('親', 'Parent'),
    ('先', 'Tip'), ('パーツ', 'Part'), ('光', 'Light'), ('影', 'Shadow'), ('尻', 'Butt'), ('尾', 'Tail'), ('翼', 'Wing'),
    ('羽', 'Feather'), ('耳', 'Ear'), ('鼻', 'Nose'), ('口', 'Mouth'), ('唇', 'Lip'), ('頬', 'Cheek'), ('額', 'Forehead'),
    ('体', 'Body'), ('胴', 'Torso'), ('背', 'Back'), ('指', 'Finger'), ('爪', 'Nail'), ('ＩＫ', 'IK'), ('ＥＸ', 'EX'),
    ('０', '0'), ('１', '1'), ('２', '2'), ('３', '3'), ('４', '4'), ('５', '5'), ('６', '6'), ('７', '7'), ('８', '8'), ('９', '9'),
    ('　', ' '), ('・', '.'), ('ー', '-'),
]
_JP_TO_EN.sort(key=lambda t: -len(t[0]))


def romanize(name):
    """Replace known Japanese terms by English and drop anything else that is
    not ASCII.  Returns the original name unchanged if it is already ASCII."""
    if name.isascii():
        return name
    out = name
    for jp, en in _JP_TO_EN:
        if jp in out:
            out = out.replace(jp, en)
    out = ''.join(ch if ch.isascii() and ch.isprintable() else '' for ch in out)
    out = re.sub(r'\s+', ' ', out).strip()
    return out


def make_unique(names, existing=None):
    """Make *names* unique (case-insensitive) by appending ' 2', ' 3', ..."""
    used = set(n.lower() for n in (existing or ()))
    out = []
    for name in names:
        base = name.strip() or 'bone'
        cand = base
        n = 2
        while cand.lower() in used:
            cand = '%s %d' % (base, n)
            n += 1
        used.add(cand.lower())
        out.append(cand)
    return out


# --------------------------------------------------------------------------- helper bones (XPS "unused" prefix)
# XPS / XNALara hide bones whose name starts with "unused" from the bone list
# (they still deform).  Rigs from games carry hundreds of twist / corrective /
# attachment bones nobody poses by hand; prefixing them keeps the XPS list usable.

UNUSED_PREFIX = 'unused_'

HELPER_BONE_RE = re.compile(
    r'(twist|_corrective|correctiveroot|_out(_|$)|_in(_|$)|_fwd(_|$)|_bck(_|$)|_bulge|_half(_|$)|_side_(inn|out)'
    r'|_dip(_|$)|_pip(_|$)|_mcp(_|$)|_palm|_slide|_pec(_|$)|_latissimus|_scap(_|$)|_metacarpal'
    r'|^ik_|_ik(_|$)|^vb_|^anim_attachment|attach|_nub$|_end$|footsteps|^weapon|_socket'
    r'|^_dummy_|^_shadow_|IK\b|ＩＫ|キャンセル)',
    re.I)
FACIAL_BONE_RE = re.compile(r'^(facial_|face_)', re.I)
FACIAL_KEEP_RE = re.compile(r'^FACIAL_(C_Jaw|[LR]_Eye|C_TongueBase|C_Tongue\d*)$', re.I)


def is_helper_bone(name):
    return bool(HELPER_BONE_RE.search(name))


def is_facial_bone(name):
    return bool(FACIAL_BONE_RE.match(name)) and not FACIAL_KEEP_RE.match(name)


def hide_name(name):
    if name.lower().startswith('unused'):
        return name
    return UNUSED_PREFIX + name
