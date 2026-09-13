# -*- coding: utf-8 -*-
"""Talk to a running Blender through the blender-mcp add-on socket
(127.0.0.1:9876).  Used for installing / reloading / testing the add-on in the
user's live session without touching the mouse.

    python tools/blender_mcp.py exec  script.py        # run a python file inside Blender
    python tools/blender_mcp.py reload                 # disable + reload + enable blender2xps
    python tools/blender_mcp.py enable                 # enable + save preferences
    python tools/blender_mcp.py info                   # get_scene_info
"""
import json
import os
import socket
import sys
import time

HOST, PORT = '127.0.0.1', 9876
HERE = os.path.dirname(os.path.abspath(__file__))


def call(cmd_type, params=None, timeout=300):
    s = socket.create_connection((HOST, PORT), timeout=timeout)
    s.sendall(json.dumps({'type': cmd_type, 'params': params or {}}).encode('utf-8'))
    buf = b''
    t0 = time.time()
    while True:
        try:
            chunk = s.recv(1 << 16)
        except socket.timeout:
            break
        if not chunk:
            break
        buf += chunk
        try:
            resp = json.loads(buf.decode('utf-8'))
            s.close()
            return resp
        except ValueError:
            if time.time() - t0 > timeout:
                break
    s.close()
    raise RuntimeError('no complete response: %r' % buf[:400])


def run_code(code):
    resp = call('execute_code', {'code': code})
    if resp.get('status') != 'success':
        raise RuntimeError(resp.get('message'))
    res = resp.get('result')
    return res.get('result') if isinstance(res, dict) else res


RELOAD_CODE = r'''
import bpy, sys, importlib, traceback
try:
    try:
        bpy.ops.preferences.addon_disable(module='blender2xps')
    except Exception as e:
        print('disable:', e)
    for name in [n for n in list(sys.modules) if n == 'blender2xps' or n.startswith('blender2xps.')]:
        del sys.modules[name]
    bpy.ops.preferences.addon_refresh()
    bpy.ops.preferences.addon_enable(module='blender2xps')
    bpy.ops.wm.save_userpref()
    print('blender2xps enabled:', 'blender2xps' in bpy.context.preferences.addons)
except Exception:
    traceback.print_exc()
'''


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        return 1
    mode = sys.argv[1]
    if mode == 'exec':
        with open(sys.argv[2], encoding='utf-8') as f:
            print(run_code(f.read()))
    elif mode in ('reload', 'enable'):
        print(run_code(RELOAD_CODE))
    elif mode == 'info':
        print(json.dumps(call('get_scene_info'), ensure_ascii=False, indent=1))
    else:
        print(json.dumps(call(mode), ensure_ascii=False, indent=1))
    return 0


if __name__ == '__main__':
    sys.exit(main())
